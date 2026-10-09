import json
import sys
import time
from pathlib import Path

import pandas as pd


def run_category_tests(artifacts_dir, data_dir, threshold=0.55, category_target_accuracy=0.90):
    """Run RAW + URL-encoded category tests against this job's artifacts."""
    ml_src = Path(data_dir).parent / "src"
    if str(ml_src) not in sys.path:
        sys.path.insert(0, str(ml_src))
    from preprocessing import encode_request_components, parse_category_lines
    from random_forest.predict import HTTPAttackPredictor as RFPredictor
    from logistic_regression.predict import HTTPAttackPredictor as LRPredictor

    reports = {}
    for model_name, predictor_class in (("random_forest", RFPredictor), ("linear", LRPredictor)):
        model_dir = Path(artifacts_dir) / model_name
        if not (model_dir / "model.joblib").exists():
            reports[model_name] = {"status": "not_available"}
            continue
        predictor = predictor_class(str(model_dir), threshold=threshold)
        rows = []
        passed = 0
        total = 0
        for filename, expected in (("attack_fields.txt", "ATTACK"), ("normal_fields.txt", "NORMAL")):
            path = Path(data_dir) / filename
            if not path.exists():
                continue
            for category in parse_category_lines(str(path)):
                for sample_type, request in (("RAW", category["request"]),
                                             ("ENCODED", encode_request_components(category["request"]))):
                    started = time.time()
                    prediction, confidence = predictor.predict(request)
                    correct = prediction == expected
                    total += 1
                    passed += int(correct)
                    rows.append({"category": category["category"], "type": sample_type,
                                 "expected": expected, "predicted": prediction,
                                 "confidence": confidence,
                                 "time_ms": round((time.time() - started) * 1000, 3),
                                 "correct": correct})
        report = {"status": "completed", "passed": passed, "total": total,
                  "accuracy": passed / total if total else 0.0,
                  "category_target_accuracy": category_target_accuracy,
                  "target_met": passed / total >= category_target_accuracy if total else False,
                  "results": rows}
        (model_dir / "category_results.json").write_text(json.dumps(report, indent=2))
        reports[model_name] = {k: v for k, v in report.items() if k != "results"}
    return reports
