import argparse
import json
import os

from .trainer import train_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=os.getenv("DATASET_S3_URI", "data"), help="local data directory or s3://bucket/prefix")
    parser.add_argument("--fallback-dataset", default=os.getenv("FALLBACK_DATASET", "data"))
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR", "output"), help="local output directory")
    parser.add_argument("--output-s3", default=os.getenv("OUTPUT_S3_URI"), help="optional s3://bucket/prefix destination")
    parser.add_argument("--no-local-fallback", action="store_true")
    parser.add_argument("--require-llm", action="store_true",
                        help="fail instead of silently using deterministic tuning")
    parser.add_argument("--task", default="auto", choices=["auto", "classification", "regression"])
    parser.add_argument("--target", default="label")
    parser.add_argument("--target-accuracy", type=float, default=None,
                        help="optional validation accuracy target; omitted disables this gate")
    parser.add_argument("--category-target-accuracy", type=float, default=0.90,
                        help="minimum category test accuracy for each model")
    parser.add_argument("--max-trials", type=int, default=12)
    parser.add_argument("--max-rounds", type=int, default=10,
                        help="retry tuning rounds until category target is met")
    args = parser.parse_args()
    config = {"dataset_path": args.dataset, "fallback_dataset_path": args.fallback_dataset,
                        "allow_local_fallback": not args.no_local_fallback, "artifacts_dir": args.output,
                        "output_s3": args.output_s3, "task": args.task, "target": args.target,
                        "require_llm": args.require_llm,
                        "target_metrics": {},
                        "category_target_accuracy": args.category_target_accuracy,
                        "max_trials": args.max_trials, "max_rounds": args.max_rounds}
    if args.target_accuracy is not None:
        config["target_metrics"] = {"accuracy": args.target_accuracy}
    if args.require_llm:
        os.environ["LLM_REQUIRED"] = "1"
    def log_update(update):
        if update.get("status") == "category_tests":
            state = "PASS" if update.get("target_met") else "FAIL"
            print(
                f"[{update['model']}] CATEGORY: {update.get('passed', 0)}/{update.get('total', 0)} "
                f"passed ({update.get('accuracy', 0.0) * 100:.2f}%) "
                f"target={update.get('target_accuracy', args.category_target_accuracy) * 100:.2f}% {state}"
            )
        elif update.get("status") == "retrying":
            print(f"[RETRY] round {update['round']}/{update['max_rounds']}: {update['message']}")
        else:
            print(update)

    report = train_job(config, log_update)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
