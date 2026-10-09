import argparse
import json
import os

from .trainer import train_job
from .llm_planner import load_dotenv


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=os.getenv("DATASET_S3_URI", "data"), help="local data directory or s3://bucket/prefix")
    parser.add_argument("--normal-path", "--path1", dest="normal_path",
                        help="normal.txt local path or s3://bucket/object")
    parser.add_argument("--attack-path", "--path2", dest="attack_path",
                        help="attack/malicious.txt local path or s3://bucket/object")
    parser.add_argument("--fallback-dataset", default=os.getenv("FALLBACK_DATASET", "data"))
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR", "output"), help="local output directory")
    parser.add_argument("--output-s3", default=os.getenv("OUTPUT_S3_URI"), help="optional s3://bucket/prefix destination")
    parser.add_argument("--no-local-fallback", action="store_true")
    parser.add_argument("--require-llm", action="store_true",
                        help="fail instead of silently using deterministic tuning")
    parser.add_argument("--agent-mode", choices=["llm", "codex"],
                        default=os.getenv("TRAINING_AGENT_MODE", "llm"),
                        help="llm plans each round; codex tunes after each completed trial")
    parser.add_argument("--codex-model", default=os.getenv("CODEX_MODEL"),
                        help="optional Codex model for --agent-mode codex")
    parser.add_argument("--codex-timeout-seconds", type=float, default=None,
                        help="timeout for each Codex tuning decision (default: CODEX_TIMEOUT_SECONDS or 180)")
    parser.add_argument("--llm-timeout-seconds", type=float, default=None,
                        help="LLM request timeout (default: LLM_TIMEOUT_SECONDS or 90)")
    parser.add_argument("--llm-retries", type=int, default=None,
                        help="retries for transient LLM failures (default: LLM_RETRIES or 2)")
    parser.add_argument("--llm-max-tokens", type=int, default=None,
                        help="LLM response token budget (default: LLM_MAX_TOKENS or 2048)")
    parser.add_argument("--task", default="auto", choices=["auto", "classification", "regression"])
    parser.add_argument("--target", default="label")
    parser.add_argument("--category-target-accuracy", type=float, default=0.90,
                        help="minimum accuracy on the category test set")
    parser.add_argument("--max-trials", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=1,
                        help="retry tuning rounds until category target is met")
    args = parser.parse_args()
    config = {"dataset_path": args.dataset, "fallback_dataset_path": args.fallback_dataset,
                        "allow_local_fallback": not args.no_local_fallback, "artifacts_dir": args.output,
                        "output_s3": args.output_s3, "task": args.task, "target": args.target,
                        "agent_mode": args.agent_mode,
                        "require_llm": args.require_llm,
                        "category_target_accuracy": args.category_target_accuracy,
                        "max_trials": args.max_trials, "max_rounds": args.max_rounds}
    if args.normal_path:
        config["normal_path"] = args.normal_path
    if args.attack_path:
        config["attack_path"] = args.attack_path
    if args.llm_timeout_seconds is not None:
        config["llm_timeout_seconds"] = args.llm_timeout_seconds
    if args.llm_retries is not None:
        config["llm_retries"] = args.llm_retries
    if args.llm_max_tokens is not None:
        config["llm_max_tokens"] = args.llm_max_tokens
    if args.codex_model:
        config["codex_model"] = args.codex_model
    if args.codex_timeout_seconds is not None:
        config["codex_timeout_seconds"] = args.codex_timeout_seconds
    if args.require_llm:
        os.environ["LLM_REQUIRED"] = "1"
    def log_update(update):
        if update.get("status") == "category_tests":
            state = "PASS" if update.get("target_met") else "FAIL"
            print(
                f"[{update['model']}] CATEGORY: {update.get('passed', 0)}/{update.get('total', 0)} "
                f"passed ({update.get('accuracy', 0.0) * 100:.2f}%) "
                f"target={update.get('category_target_accuracy', args.category_target_accuracy) * 100:.2f}% {state}"
            )
        elif update.get("status") == "retrying":
            print(f"[RETRY] round {update['round']}/{update['max_rounds']}: {update['message']}")
        elif update.get("status") == "agent_tuning":
            print(f"[AGENT] {update['agent']} reviewing {update['model']} trial metrics")
        elif update.get("status") == "agent_recommendation":
            print(f"[AGENT] next {update['model']} params={update['params']} reason={update['reason']}")
        else:
            print(update)

    report = train_job(config, log_update)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
