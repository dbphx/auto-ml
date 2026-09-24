import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split, ParameterSampler

ML_ROOT = Path(os.getenv("ML_REPO", Path(__file__).resolve().parents[1]))
if str(ML_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ML_ROOT / "src"))
from feature_engineering import FeatureEngineer  # noqa: E402
from preprocessing import expand_request_to_field_rows, parse_category_lines  # noqa: E402
from .llm_planner import build_plan
from .storage import download_dataset, is_s3_uri, upload_directory


def _now():
    return datetime.now(timezone.utc).isoformat()


def _metrics(task, y_true, prediction):
    if task == "regression":
        return {"r2": float(r2_score(y_true, prediction)),
                "mae": float(mean_absolute_error(y_true, prediction)),
                "rmse": float(np.sqrt(mean_squared_error(y_true, prediction)))}
    return {"accuracy": float(accuracy_score(y_true, prediction)),
            "f1": float(f1_score(y_true, prediction, zero_division=0)),
            "precision": float(__import__("sklearn.metrics", fromlist=["precision_score"]).precision_score(y_true, prediction, zero_division=0)),
            "recall": float(__import__("sklearn.metrics", fromlist=["recall_score"]).recall_score(y_true, prediction, zero_division=0))}


def _meets_targets(metrics, targets):
    return all(float(metrics.get(key, -np.inf)) >= float(value) for key, value in targets.items())


def _load_data(path, target):
    path = Path(path)
    if path.is_dir():
        path = path / "train.csv"
    frame = pd.read_csv(path)
    if target not in frame.columns:
        raise ValueError(f"target column {target!r} not found; columns={list(frame.columns)}")
    frame = frame.dropna(subset=[target]).reset_index(drop=True)
    return frame, str(path)


def _load_text_pair(normal_path, attack_path):
    """Load this repo's numbered `normal.txt` and `attack.txt` category files."""
    rows = []
    for path, label in ((normal_path, 0), (attack_path, 1)):
        categories = parse_category_lines(str(path))
        if not categories:
            raise ValueError(f"no numbered samples found in {path}")
        for category in categories:
            request = dict(category["request"])
            request["label"] = label
            request["category"] = category["category"]
            rows.extend(expand_request_to_field_rows(request, include_combined=False))
    return pd.DataFrame(rows), f"{normal_path},{attack_path}"


def _resolve_dataset(config):
    explicit_normal = config.get("normal_path")
    explicit_attack = config.get("attack_path")
    dataset = config.get("dataset_path")
    if is_s3_uri(dataset):
        try:
            downloaded = download_dataset(dataset)
            return _load_text_pair(downloaded / "normal.txt", downloaded / "attack.txt")
        except Exception as exc:
            if not config.get("allow_local_fallback", True):
                raise
            fallback = config.get("fallback_dataset_path", str(ML_ROOT / "data"))
            print(f"S3 dataset unavailable ({exc}); falling back to {fallback}")
            return _resolve_dataset({"dataset_path": fallback, "target": config.get("target", "label")})
    if explicit_normal and explicit_attack:
        return _load_text_pair(explicit_normal, explicit_attack)
    if dataset:
        dataset_path = Path(dataset)
        if dataset_path.is_dir():
            normal = dataset_path / "normal.txt"
            attack = dataset_path / "attack.txt"
            if normal.exists() and attack.exists():
                return _load_text_pair(normal, attack)
        if dataset_path.suffix.lower() == ".txt":
            sibling = dataset_path.parent
            normal = sibling / "normal.txt"
            attack = sibling / "attack.txt"
            if normal.exists() and attack.exists():
                return _load_text_pair(normal, attack)
        return _load_data(dataset, config.get("target", "label"))
    return _load_text_pair(ML_ROOT / "data/normal.txt", ML_ROOT / "data/attack.txt")


def train_job(config, progress=None):
    def emit(value):
        if progress:
            progress(value)

    job_id = config.get("job_id", uuid.uuid4().hex)
    round_number = int(config.get("_round", 1))
    max_rounds = max(1, min(int(config.get("max_rounds", 10)), 100))
    seed = int(config.get("random_state", 42))
    target = config.get("target", "label")
    frame, data_path = _resolve_dataset(config)
    requested_task = config.get("task", "auto")
    task = requested_task if requested_task != "auto" else ("classification" if frame[target].nunique() <= 20 else "regression")
    if task not in {"classification", "regression"}:
        raise ValueError("task must be auto, classification, or regression")
    if task == "classification":
        frame[target] = frame[target].astype(int)

    models = config.get("models", ["random_forest", "linear"])
    models = [m for m in models if m in {"random_forest", "linear"}]
    if not models:
        raise ValueError("models must contain random_forest and/or linear")
    targets = config.get("target_metrics", {"accuracy": 0.98} if task == "classification" else {"r2": 0.8})
    max_trials = max(1, min(int(config.get("max_trials", 12)), 100))
    test_size = float(config.get("test_size", 0.2))

    feature_columns = [c for c in frame.columns if c not in {target, "weight", "category", "source_payload"}]
    x_frame = frame[feature_columns].copy()
    y = frame[target]
    stratify = y if task == "classification" and y.value_counts().min() >= 2 else None
    train_df, valid_df, y_train, y_valid = train_test_split(
        x_frame, y, test_size=test_size, random_state=seed, stratify=stratify
    )
    fe = FeatureEngineer()
    prepared_train = fe.prepare(train_df)
    prepared_valid = fe.prepare(valid_df)
    fe.fit(prepared_train)
    x_train = fe.transform(prepared_train)
    x_valid = fe.transform(prepared_valid)
    summary = {"rows": len(frame), "features": feature_columns, "task": task,
               "classes": sorted(map(str, y.unique())) if task == "classification" else None}
    plan = build_plan(summary, targets, models)
    artifacts = Path(config.get("artifacts_dir", "output")) / job_id / f"round_{round_number}"
    artifacts.mkdir(parents=True, exist_ok=True)
    emit({"status": "running", "message": "searching hyperparameters", "plan": plan})

    best = None
    best_by_model = {}
    trial_log = []
    for model_name in models:
        space = plan["search_space"].get(model_name, {})
        sampler_seed = seed + (0 if model_name == "random_forest" else 1000)
        for params in ParameterSampler(space, n_iter=max_trials, random_state=sampler_seed):
            if task == "classification":
                estimator = (RandomForestClassifier(random_state=seed, n_jobs=-1, **params)
                             if model_name == "random_forest" else
                             LogisticRegression(max_iter=2000, solver="liblinear", random_state=seed, **params))
            else:
                estimator = (RandomForestRegressor(random_state=seed, n_jobs=-1, **params)
                             if model_name == "random_forest" else LinearRegression(**params))
            estimator.fit(x_train, y_train)
            score = _metrics(task, y_valid, estimator.predict(x_valid))
            row = {"model": model_name, "params": params, "metrics": score}
            trial_log.append(row)
            current_model_best = best_by_model.get(model_name)
            if current_model_best is None or score.get("f1", score.get("r2", -np.inf)) > current_model_best["metrics"].get("f1", current_model_best["metrics"].get("r2", -np.inf)):
                best_by_model[model_name] = {"model": model_name, "params": params, "metrics": score}
            if best is None or _meets_targets(score, targets) and not _meets_targets(best["metrics"], targets) or score.get("f1", score.get("r2", -np.inf)) > best["metrics"].get("f1", best["metrics"].get("r2", -np.inf)):
                best = {"model": model_name, "params": params, "metrics": score, "estimator": estimator}
            emit({"status": "running", "trial": len(trial_log), "model": model_name, "metrics": score})

    if best is None:
        raise RuntimeError("no trial completed")
    # Refit and publish the best artifact for every requested model.
    model_artifacts = {}
    for model_name, selected in best_by_model.items():
        final_fe = FeatureEngineer()
        final_prepared = final_fe.prepare(x_frame)
        final_fe.fit(final_prepared)
        final_x = final_fe.transform(final_prepared)
        if task == "classification":
            final_model = (RandomForestClassifier(random_state=seed, n_jobs=-1, **selected["params"])
                           if model_name == "random_forest" else LogisticRegression(max_iter=2000, solver="liblinear", random_state=seed, **selected["params"]))
        else:
            final_model = (RandomForestRegressor(random_state=seed, n_jobs=-1, **selected["params"])
                           if model_name == "random_forest" else LinearRegression(**selected["params"]))
        final_model.fit(final_x, y)
        model_dir = artifacts / model_name
        model_dir.mkdir(exist_ok=True)
        joblib.dump(final_model, model_dir / "model.joblib")
        final_fe.save(model_dir / "vectorizer.joblib")
        model_artifacts[model_name] = str(model_dir)
    # Keep the winning model at the job root for simple consumers.
    winning_dir = Path(model_artifacts[best["model"]])
    joblib.dump(joblib.load(winning_dir / "model.joblib"), artifacts / "model.joblib")
    (artifacts / "vectorizer.joblib").write_bytes((winning_dir / "vectorizer.joblib").read_bytes())
    category_reports = {}
    if task == "classification":
        from .category_tests import run_category_tests
        category_reports = run_category_tests(
            artifacts,
            ML_ROOT / "data",
            config.get("threshold", 0.55),
            float(config.get("category_target_accuracy", 0.90)),
        )
        for model_name, result in category_reports.items():
            if result.get("status") != "completed":
                emit({"status": "category_tests", "model": model_name, **result})
                continue
            emit({
                "status": "category_tests",
                "model": model_name,
                "passed": result["passed"],
                "total": result["total"],
                "accuracy": result["accuracy"],
                "target_accuracy": result["target_accuracy"],
                "target_met": result["target_met"],
            })
    report = {"job_id": job_id, "round": round_number, "created_at": _now(), "dataset_path": data_path,
              "task": task, "target": target, "targets": targets, "plan": plan,
              "best": {k: v for k, v in best.items() if k != "estimator"},
              "model_artifacts": model_artifacts, "category_tests": category_reports,
              "validation_target_met": _meets_targets(best["metrics"], targets),
              "target_met": all(item.get("target_met", False) for item in category_reports.values()) if category_reports else _meets_targets(best["metrics"], targets),
              "trials": trial_log,
              "artifacts": str(artifacts)}
    output_s3 = config.get("output_s3")
    if output_s3:
        try:
            report["s3_upload"] = upload_directory(artifacts, output_s3.rstrip("/") + "/" + job_id)
        except Exception as exc:
            report["s3_upload"] = {"status": "failed", "error": str(exc), "local_fallback": str(artifacts)}
            print(f"S3 upload unavailable ({exc}); keeping local output at {artifacts}")
    (artifacts / "report.json").write_text(json.dumps(report, indent=2, default=str))
    # Upload report once more because s3_upload status is part of report.json.
    if output_s3 and report.get("s3_upload", {}).get("status") == "uploaded":
        try:
            upload_directory(artifacts, output_s3.rstrip("/") + "/" + job_id)
        except Exception:
            pass
    emit({"status": "completed", **report["best"], "target_met": report["target_met"], "artifacts": str(artifacts)})
    if task == "classification" and not report["target_met"] and round_number < max_rounds:
        emit({"status": "retrying", "message": "category target not met; starting another tuning round",
              "round": round_number + 1, "max_rounds": max_rounds})
        next_config = dict(config)
        next_config["_round"] = round_number + 1
        next_config["random_state"] = seed + 1
        return train_job(next_config, progress)
    return report
