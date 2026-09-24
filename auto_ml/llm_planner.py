import json
import os
import urllib.request
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


def _load_dotenv():
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
        result[model] = {}
        for key, default in allowed.items():
            values = source.get(key, default)
            if not isinstance(values, list):
                values = [values]
            if len(values) > 12:
                values = values[:12]
            result[model][key] = values or default
    return result


def build_plan(dataset_summary, target_metrics, models):
    """Ask an optional OpenAI-compatible endpoint for a bounded search plan."""
    _load_dotenv()
    task = dataset_summary.get("task", "classification")
    defaults = DEFAULT_SPACE if task == "classification" else REGRESSION_SPACE
    fallback = {"search_space": {m: defaults[m] for m in models}, "reason": "deterministic fallback"}
    # Support both generic names and the VLLM_* names used by the deployment.
    endpoint = os.getenv("LLM_API_URL") or os.getenv("VLLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("VLLM_API_KEY")
    model = os.getenv("LLM_MODEL") or os.getenv("VLLM_MODEL", "gpt-4o-mini")
    if not endpoint or not api_key:
        if os.getenv("LLM_REQUIRED", "").lower() in {"1", "true", "yes"}:
            raise RuntimeError("LLM is required but VLLM_BASE_URL/VLLM_API_KEY are not configured")
        return fallback
    endpoint = endpoint.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = urljoin(endpoint + "/", "v1/chat/completions")

    prompt = {
        "role": "user",
        "content": (
            "You are an ML tuning planner. Return JSON only with keys search_space and reason. "
            "Choose only parameters shown in the supplied bounded space. Never invent code.\n" +
            json.dumps({"dataset": dataset_summary, "targets": target_metrics,
                        "models": models, "bounded_space": {m: defaults[m] for m in models}}, default=str)
        ),
    }
    payload = json.dumps({"model": model, "messages": [prompt], "temperature": 0}).encode()
    request = urllib.request.Request(endpoint, data=payload, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode())
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return {"search_space": _safe_space(parsed.get("search_space"), task),
                "reason": str(parsed.get("reason", "LLM plan"))[:1000]}
    except Exception as exc:
        if os.getenv("LLM_REQUIRED", "").lower() in {"1", "true", "yes"}:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        fallback["reason"] = f"LLM unavailable; fallback used: {exc.__class__.__name__}"
        return fallback
