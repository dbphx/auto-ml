import json
import os
import re
import time
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from urllib.parse import urljoin
from pathlib import Path


DEFAULT_SPACE = {
    "random_forest": {
        "n_estimators": [200, 400, 800],
        "max_depth": [None, 16, 32],
        "min_samples_leaf": [1, 2, 4],
        "max_features": ["sqrt", "log2", 0.5],
    },
    "linear": {
        "C": [0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
        "class_weight": [None, "balanced"],
    },
}
REGRESSION_SPACE = {
    "random_forest": {
        "n_estimators": [200, 400, 800],
        "max_depth": [None, 16, 32],
        "min_samples_leaf": [1, 2, 4],
        "max_features": [1.0, "sqrt", 0.5],
    },
    "linear": {},
}


def load_dotenv():
    """Load simple KEY=VALUE entries without requiring dotenv at runtime."""
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _safe_space(candidate, task="classification"):
    """Keep only bounded, known sklearn parameters from an LLM response."""
    if not isinstance(candidate, dict):
        return DEFAULT_SPACE
    result = {}
    defaults = DEFAULT_SPACE if task == "classification" else REGRESSION_SPACE
    for model, allowed in defaults.items():
        source = candidate.get(model, {})
        if not isinstance(source, dict):
            source = {}
        result[model] = {}
        for key, default in allowed.items():
            values = source.get(key, default)
            if not isinstance(values, list):
                values = [values]
            if len(values) > 12:
                values = values[:12]
            validated = [value for value in values if any(value == option for option in default)]
            result[model][key] = validated or default
    return result


def _parse_codex_response(answer, parameter_names):
    """Extract a JSON object from Codex text, allowing fences or trailing prose."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(answer):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(answer[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and (
            "params" in candidate or set(candidate) == set(parameter_names)
        ):
            return candidate
    raise ValueError("response contains no parameter JSON object")


def _parse_plan_response(data):
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("LLM response has no choices[0]")
    choice = choices[0]
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise ValueError("LLM response choices[0].message is not an object")

    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str) or not content.strip():
        content = message.get("reasoning_content")
    if not isinstance(content, str) or not content.strip():
        finish_reason = choice.get("finish_reason", "unknown")
        raise ValueError(
            f"LLM response content is empty (finish_reason={finish_reason}, "
            f"message_keys={sorted(message.keys())})"
        )

    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char == "{":
                try:
                    candidate, _ = decoder.raw_decode(content[index:])
                    if isinstance(candidate, dict):
                        parsed = candidate
                        break
                except json.JSONDecodeError:
                    continue
    if not isinstance(parsed, dict):
        raise ValueError("LLM response content does not contain a JSON object")
    return parsed


def build_codex_params(model_name, bounded_space, trials, task="classification",
                       target_metrics=None, timeout_seconds=None, model=None,
                       llm_source="auto"):
    """Ask Codex CLI for one bounded next trial, based only on observed metrics."""
    executable = shutil.which(os.getenv("CODEX_CLI", "codex"))
    if not executable:
        raise RuntimeError("Codex mode requires the Codex CLI (`codex`) on PATH")
    timeout_seconds = float(timeout_seconds or os.getenv("CODEX_TIMEOUT_SECONDS", "180"))
    parameters = bounded_space.get(model_name, {})
    if not parameters:
        return {"params": {}, "reason": "model has no tunable parameters"}

    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "params": {
                "type": "object", "additionalProperties": False,
                "properties": {key: {"enum": values} for key, values in parameters.items()},
                "required": list(parameters),
            },
            "reason": {"type": "string"},
        },
        "required": ["params", "reason"],
    }
    prompt = (
        "You are the hyperparameter tuning agent for a machine learning training job. "
        "Choose exactly one next parameter combination for the named model. Use only values "
        "from bounded_space; do not run commands, access files, or modify anything. "
        "Use prior trials to avoid repeats and improve the requested metric. Return JSON matching the schema.\n"
        + json.dumps({"task": task, "target_metrics": target_metrics or {},
                     "model": model_name, "bounded_space": parameters,
                     "previous_trials": trials[-30:]}, default=str)
    )
    with tempfile.TemporaryDirectory(prefix="auto-ml-codex-") as temp_dir:
        temp = Path(temp_dir)
        schema_path = temp / "schema.json"
        answer_path = temp / "answer.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        command = [executable, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                   "--output-schema", str(schema_path), "--output-last-message", str(answer_path)]
        provider_config = next((item for item in (
            (os.getenv("VLLM_BASE_URL"), "VLLM_API_KEY", "VLLM_MODEL"),
            (os.getenv("LLM_API_URL"), "LLM_API_KEY", "LLM_MODEL"),
        ) if item[0] and os.getenv(item[1])), None)
        if llm_source not in {"auto", "external", "internal"}:
            raise ValueError("llm_source must be auto, external, or internal")
        use_external = llm_source == "external" or (llm_source == "auto" and provider_config is not None)
        if use_external and provider_config is None:
            raise RuntimeError("Codex external mode requires a URL and API key in VLLM_* or LLM_* env vars")
        endpoint, api_key_env, model_env = provider_config if use_external else (None, None, None)
        if use_external:
            selected_model = model or os.getenv(model_env) or os.getenv("CODEX_MODEL")
        else:
            selected_model = model or os.getenv("CODEX_MODEL")
        if endpoint:
            endpoint = endpoint.rstrip("/")
            for suffix in ("/v1/chat/completions", "/chat/completions", "/v1/responses", "/responses"):
                if endpoint.endswith(suffix):
                    endpoint = endpoint[:-len(suffix)]
                    break
            if not endpoint.endswith("/v1"):
                endpoint += "/v1"
            command.extend([
                "--config", 'model_provider="training-llm"',
                "--config", 'model_providers.training-llm.name="Training LLM"',
                "--config", f"model_providers.training-llm.base_url={json.dumps(endpoint)}",
                "--config", f'model_providers.training-llm.env_key="{api_key_env}"',
                "--config", 'model_providers.training-llm.wire_api="responses"',
                "--config", 'model_providers.training-llm.requires_openai_auth=false',
                "--config", 'model_providers.training-llm.supports_websockets=false',
            ])
        if selected_model:
            command.extend(["--model", selected_model])
        command.append("-")
        result = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                timeout=timeout_seconds, check=False,
                                cwd=Path(__file__).resolve().parents[1])
        if result.returncode != 0:
            detail = result.stderr.strip()[-1500:] or result.stdout.strip()[-1500:]
            raise RuntimeError(f"Codex CLI failed ({result.returncode}): {detail}")
        answer = answer_path.read_text(encoding="utf-8") if answer_path.exists() else ""
        if not answer.strip():
            if use_external:
                raise RuntimeError(
                    "Codex external provider returned an empty structured response. "
                    "Confirm the endpoint supports the OpenAI Responses API (/v1/responses) "
                    "and that the selected model can return structured output."
                )
            raise RuntimeError(
                "Codex returned an empty structured response. Check Codex authentication "
                "on the host with `codex login`, then restart the service."
            )
        try:
            parsed = _parse_codex_response(answer, parameters)
        except ValueError as exc:
            raise ValueError(f"Codex returned invalid structured JSON: {exc}") from exc

    # Some OpenAI-compatible proxies/models return the schema's parameter object
    # directly (often in a Markdown JSON fence) instead of wrapping it in `params`.
    # Accept only an exact set of known parameter keys; values are validated below.
    if isinstance(parsed, dict) and "params" not in parsed and set(parsed) == set(parameters):
        parsed = {"params": parsed, "reason": "Codex parameter selection"}
    if not isinstance(parsed, dict):
        raise ValueError("Codex response is not a JSON object")
    candidate = parsed.get("params")
    if not isinstance(candidate, dict):
        raise ValueError("Codex response is missing params")
    # Recheck every value locally; the CLI response is never trusted as executable input.
    safe = {}
    for key, values in parameters.items():
        value = candidate.get(key)
        if not any(value == allowed for allowed in values):
            raise ValueError(f"Codex returned an out-of-range value for {key}")
        safe[key] = value
    return {"params": safe, "reason": str(parsed.get("reason", "Codex tuning"))[:1000]}


def build_plan(dataset_summary, target_metrics, models, feedback=None, require_llm=False,
               timeout_seconds=None, retries=None, max_tokens=None, model_override=None):
    """Ask an optional OpenAI-compatible endpoint for a bounded tuning plan."""
    load_dotenv()
    task = dataset_summary.get("task", "classification")
    defaults = DEFAULT_SPACE if task == "classification" else REGRESSION_SPACE
    fallback = {"search_space": {m: defaults[m] for m in models}, "reason": "deterministic fallback"}
    # Support both generic names and the VLLM_* names used by the deployment.
    endpoint = os.getenv("LLM_API_URL") or os.getenv("VLLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("VLLM_API_KEY")
    model = model_override or os.getenv("LLM_MODEL") or os.getenv("VLLM_MODEL", "gpt-4o-mini")
    require_llm = require_llm or os.getenv("LLM_REQUIRED", "").lower() in {"1", "true", "yes"}
    timeout_seconds = float(timeout_seconds or os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    retries = max(0, int(os.getenv("LLM_RETRIES", "2") if retries is None else retries))
    max_tokens = min(8192, max(256, int(max_tokens or os.getenv("LLM_MAX_TOKENS", "2048"))))
    if not endpoint or not api_key:
        if require_llm:
            raise RuntimeError("LLM is required but VLLM_BASE_URL/VLLM_API_KEY are not configured")
        return fallback
    endpoint = endpoint.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = urljoin(endpoint + "/", "v1/chat/completions")

    prompt = {
        "role": "user",
        "content": (
            "Act as an iterative ML tuning agent. Choose values only from bounded_space, focusing "
            "on models below category_target_accuracy. Return one compact JSON object only, with "
            "exactly search_space and reason. Keep reason under 30 words; do not include analysis or code.\n" +
            json.dumps({"dataset": dataset_summary, "targets": target_metrics,
                        "models": models, "bounded_space": {m: defaults[m] for m in models},
                        "previous_round_feedback": feedback}, default=str)
        ),
    }
    try:
        parsed = None
        for attempt in range(retries + 1):
            data = None
            try:
                payload = json.dumps({"model": model, "messages": [prompt], "temperature": 0,
                                      "max_tokens": max_tokens}).encode()
                request = urllib.request.Request(endpoint, data=payload, headers={
                    "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"
                })
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    data = json.loads(response.read().decode())
                parsed = _parse_plan_response(data)
                break
            except urllib.error.HTTPError as exc:
                transient = exc.code in {408, 425, 429} or exc.code >= 500
                if not transient or attempt >= retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt >= retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
            except (ValueError, TypeError, KeyError):
                if attempt >= retries:
                    raise
                choices = data.get("choices", []) if isinstance(data, dict) else []
                finish_reason = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
                if finish_reason == "length":
                    max_tokens = min(max_tokens * 2, 8192)
                time.sleep(min(2 ** attempt, 8))
        return {"search_space": _safe_space(parsed.get("search_space"), task),
                "reason": str(parsed.get("reason", "LLM plan"))[:1000]}
    except Exception as exc:
        if require_llm:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        fallback["reason"] = f"LLM unavailable; fallback used: {exc.__class__.__name__}"
        return fallback
