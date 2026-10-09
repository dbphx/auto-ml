import threading
import uuid
import os
import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, jsonify, request, send_from_directory

from .trainer import train_job
from .storage import _s3_client, is_s3_uri

app = Flask(__name__)
JOBS = {}
JOBS_LOCK = threading.Lock()
JOBS_DB_PATH = Path(os.getenv("AUTO_ML_DB_PATH", str(Path(os.getenv("OUTPUT_DIR", "output")) / "jobs.sqlite3")))
S3_SETTINGS_PATH = JOBS_DB_PATH.parent / "s3_settings.json"
S3_SETTINGS_LOCK = threading.Lock()
try:
    MAX_WORKERS = max(1, int(os.getenv("AUTO_ML_WORKERS", "4")))
except ValueError:
    MAX_WORKERS = 4
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="auto-ml-job")


@app.get("/")
def management():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api")
def api_docs():
    return send_from_directory(app.static_folder, "api.html")


@app.get("/openapi.json")
def openapi_spec():
    return send_from_directory(app.static_folder, "openapi.json")


@app.get("/settings/s3")
def get_s3_settings():
    return jsonify(_public_s3_settings())


@app.put("/settings/s3")
def save_s3_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    with S3_SETTINGS_LOCK:
        saved = _read_saved_s3_settings()
        for key in ("region", "endpoint_url"):
            if key in payload and isinstance(payload[key], str):
                saved[key] = payload[key].strip()
        for key in ("access_key_id", "secret_access_key", "session_token"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                saved[key] = value.strip()
        if payload.get("clear_credentials"):
            for key in ("access_key_id", "secret_access_key", "session_token"):
                saved.pop(key, None)
        settings = {
            **saved,
            "region": saved.get("region") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "",
            "endpoint_url": saved.get("endpoint_url") or os.getenv("AWS_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL") or "",
            "access_key_id": saved.get("access_key_id") or os.getenv("AWS_ACCESS_KEY_ID") or "",
            "secret_access_key": saved.get("secret_access_key") or os.getenv("AWS_SECRET_ACCESS_KEY") or "",
            "session_token": saved.get("session_token") or os.getenv("AWS_SESSION_TOKEN") or "",
        }
        endpoint = settings.get("endpoint_url", "")
        if endpoint and urlparse(endpoint).scheme not in {"http", "https"}:
            return jsonify({"error": "endpoint_url must start with http:// or https://"}), 400
        if bool(settings.get("access_key_id")) != bool(settings.get("secret_access_key")):
            return jsonify({"error": "access_key_id and secret_access_key must be provided together"}), 400
        _write_s3_settings(saved)
    return jsonify(_public_s3_settings(settings))


@app.get("/s3/buckets")
def list_s3_buckets():
    settings = _read_s3_settings()
    if not settings.get("access_key_id") or not settings.get("secret_access_key"):
        return jsonify({"error": "Save S3 access and secret keys first"}), 400
    try:
        response = _s3_client(settings).list_buckets()
        buckets = sorted(bucket["Name"] for bucket in response.get("Buckets", []))
        return jsonify({"buckets": buckets})
    except Exception as exc:
        return jsonify({"error": f"Could not list S3 buckets: {exc}"}), 502


@app.get("/s3/browse")
def browse_s3():
    bucket = request.args.get("bucket", "").strip()
    prefix = request.args.get("prefix", "").strip().lstrip("/")
    if not bucket:
        return jsonify({"error": "bucket is required"}), 400
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    settings = _read_s3_settings()
    if not settings.get("access_key_id") or not settings.get("secret_access_key"):
        return jsonify({"error": "Save S3 access and secret keys first"}), 400
    try:
        response = _s3_client(settings).list_objects_v2(
            Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=1000)
        folders = [item["Prefix"] for item in response.get("CommonPrefixes", [])]
        files = [item["Key"][len(prefix):] for item in response.get("Contents", [])
                 if item.get("Key") != prefix]
        return jsonify({
            "bucket": bucket,
            "prefix": prefix,
            "parent": prefix.rstrip("/").rsplit("/", 1)[0] + "/" if "/" in prefix.rstrip("/") else "",
            "folders": folders,
            "files": files,
            "has_dataset": {"normal.txt", "attack.txt"}.issubset(set(files)),
        })
    except Exception as exc:
        return jsonify({"error": f"Could not browse S3 bucket: {exc}"}), 502


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect_db():
    return sqlite3.connect(JOBS_DB_PATH, timeout=30)


def _save_job(job):
    with _connect_db() as connection:
        connection.execute(
            "INSERT INTO jobs (job_id, created_at, updated_at, payload) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET created_at=excluded.created_at, "
            "updated_at=excluded.updated_at, payload=excluded.payload",
            (job["job_id"], job.get("created_at", ""), job.get("updated_at", ""),
             json.dumps(job, ensure_ascii=False, default=str)),
        )


def _load_jobs():
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect_db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, payload TEXT NOT NULL)")
        rows = connection.execute("SELECT payload FROM jobs").fetchall()
    for (payload,) in rows:
        job = json.loads(payload)
        if job.get("status") in {"queued", "running", "retrying", "agent_tuning", "agent_recommendation", "category_tests"}:
            timestamp = _now()
            message = "Server restarted before this job finished"
            failure = {"status": "failed", "error": message}
            job.update(failure, updated_at=timestamp, finished_at=timestamp,
                       process={"status": "failed", "message": message})
            job.setdefault("events", []).append({"timestamp": timestamp, **failure})
            job["events"] = job["events"][-500:]
        JOBS[job["job_id"]] = job
    for job in JOBS.values():
        _save_job(job)


_load_jobs()


def _read_saved_s3_settings():
    if S3_SETTINGS_PATH.exists():
        try:
            saved = json.loads(S3_SETTINGS_PATH.read_text(encoding="utf-8"))
            return saved if isinstance(saved, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _read_s3_settings():
    settings = _read_saved_s3_settings()
    return {
        "region": settings.get("region") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "",
        "endpoint_url": settings.get("endpoint_url") or os.getenv("AWS_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL") or "",
        "access_key_id": settings.get("access_key_id") or os.getenv("AWS_ACCESS_KEY_ID") or "",
        "secret_access_key": settings.get("secret_access_key") or os.getenv("AWS_SECRET_ACCESS_KEY") or "",
        "session_token": settings.get("session_token") or os.getenv("AWS_SESSION_TOKEN") or "",
    }


def _write_s3_settings(settings):
    S3_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".s3-settings-", dir=S3_SETTINGS_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(settings, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, S3_SETTINGS_PATH)
        os.chmod(S3_SETTINGS_PATH, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _public_s3_settings(settings=None):
    settings = settings or _read_s3_settings()
    access_key = settings.get("access_key_id", "")
    return {
        "region": settings.get("region", ""),
        "endpoint_url": settings.get("endpoint_url", ""),
        "has_credentials": bool(access_key and settings.get("secret_access_key")),
        "access_key_hint": f"••••{access_key[-4:]}" if access_key else "",
    }


def _run(job_id, config):
    try:
        def update_job(update):
            with JOBS_LOCK:
                job = JOBS[job_id]
                job.update(update)
                if update.get("status") in {"agent_tuning", "agent_recommendation", "category_tests"}:
                    job["status"] = "running"
                job["updated_at"] = _now()
                event = {"timestamp": job["updated_at"], **update}
                if "report" in event:
                    report = event.pop("report")
                    event["result"] = {key: report.get(key) for key in ("best", "target_met", "artifacts")}
                job["events"].append(event)
                job["events"] = job["events"][-500:]
                if update.get("status"):
                    job["process"] = {key: update[key] for key in
                                       ("status", "message", "model", "trial", "metrics", "round", "params")
                                       if key in update}
                if update.get("status") in {"completed", "failed"}:
                    job["finished_at"] = job["updated_at"]
                _save_job(job)

        report = train_job(config, update_job)
        update_job({"status": "completed", "report": report})
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS[job_id]
            timestamp = _now()
            failure = {"status": "failed", "error": str(exc)}
            job.update(failure, updated_at=timestamp, finished_at=timestamp,
                       process={"status": "failed", "message": str(exc)})
            job["events"].append({"timestamp": timestamp, **failure})
            _save_job(job)


def _request_value(payload, *keys):
    for key in keys:
        if key in payload:
            return payload[key]
    return None


@app.post("/jobs")
@app.post("/hook")
def create_job():
    config = request.get_json(silent=True)
    if not isinstance(config, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    mode = _request_value(config, "mode", "agent_mode")
    input_path = _request_value(config, "input-path", "input_path")
    normal_path = _request_value(config, "normal-path", "normal_path", "path1")
    attack_path = _request_value(config, "attack-path", "attack_path", "path2")
    output_path = _request_value(config, "output-path", "output_path")
    llm_choice = _request_value(config, "llm", "llm_source", "llm-source")
    if request.path == "/hook" and not mode:
        return jsonify({"error": "mode is required (llm or codex)"}), 400
    if mode is not None:
        if not isinstance(mode, str) or mode.lower() not in {"llm", "codex"}:
            return jsonify({"error": "mode must be llm or codex"}), 400
        config["agent_mode"] = mode.lower()
    if mode and mode.lower() == "codex":
        if not llm_choice:
            llm_choice = "internal"
        if llm_choice is not None:
            if not isinstance(llm_choice, str) or llm_choice.lower() not in {"external", "internal"}:
                return jsonify({"error": "llm must be external or internal in codex mode"}), 400
            config["llm_source"] = llm_choice.lower()
    if bool(normal_path) != bool(attack_path):
        return jsonify({"error": "provide both normal-path and attack-path"}), 400
    if isinstance(input_path, dict):
        normal_path = _request_value(input_path, "normal", "normal-path", "normal_path")
        attack_path = _request_value(input_path, "attack", "attack-path", "attack_path")
        if not normal_path or not attack_path:
            return jsonify({"error": "input-path object must contain normal and attack paths"}), 400
    elif isinstance(input_path, list):
        if len(input_path) != 2:
            return jsonify({"error": "input-path array must contain [normal-path, attack-path]"}), 400
        normal_path, attack_path = input_path
    elif input_path is not None:
        if not isinstance(input_path, str) or not input_path:
            return jsonify({"error": "input-path must be a path string, pair object, or two-item array"}), 400
        config["dataset_path"] = input_path

    if bool(normal_path) != bool(attack_path):
        return jsonify({"error": "provide both normal-path and attack-path"}), 400
    if request.path == "/hook" and not input_path and not normal_path:
        return jsonify({"error": "input-path or both normal-path and attack-path are required"}), 400
    if request.path == "/hook":
        config.setdefault("max_trials", 10)
        config.setdefault("max_rounds", 1)
        config.setdefault("category_target_accuracy", 0.90)
        config.pop("target_accuracy", None)
        config.pop("target_metrics", None)
    if normal_path:
        if not isinstance(normal_path, str) or not isinstance(attack_path, str):
            return jsonify({"error": "normal-path and attack-path must be strings"}), 400
        config["normal_path"] = normal_path
        config["attack_path"] = attack_path
    if (is_s3_uri(config.get("dataset_path")) or is_s3_uri(normal_path)
            or is_s3_uri(attack_path) or is_s3_uri(config.get("output_s3"))):
        config["s3_config"] = _read_s3_settings()
    if output_path is not None:
        if not isinstance(output_path, str) or not output_path:
            return jsonify({"error": "output-path must be a non-empty path string"}), 400
        config["artifacts_dir"] = output_path
    # Model choice stays in environment configuration, never in the hook payload.
    config.pop("llm_model", None)
    config.pop("llm-model", None)
    config.pop("codex_model", None)
    config.pop("codex-model", None)

    job_id = uuid.uuid4().hex
    config["job_id"] = job_id
    with JOBS_LOCK:
        timestamp = _now()
        JOBS[job_id] = {"job_id": job_id, "status": "queued", "created_at": timestamp,
                        "updated_at": timestamp, "process": {"status": "queued"},
                        "events": [{"timestamp": timestamp, "status": "queued"}]}
        _save_job(JOBS[job_id])
    JOB_EXECUTOR.submit(_run, job_id, config)
    return jsonify(JOBS[job_id]), 202


@app.get("/jobs/<job_id>")
def get_job(job_id):
    with JOBS_LOCK:
        job = dict(JOBS[job_id]) if job_id in JOBS else None
    return (jsonify(job), 200) if job else (jsonify({"error": "job not found"}), 404)


@app.get("/tasks")
def list_tasks():
    status_filter = request.args.get("status")
    with JOBS_LOCK:
        jobs = [dict(job) for job in JOBS.values()
                if not status_filter or job.get("status") == status_filter]
    jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
    summaries = [{key: job[key] for key in
                  ("job_id", "status", "created_at", "updated_at", "finished_at", "process", "error")
                  if key in job} for job in jobs]
    return jsonify({"tasks": summaries, "count": len(summaries)})


@app.delete("/tasks")
def clear_task_history():
    with JOBS_LOCK:
        finished_ids = [job_id for job_id, job in JOBS.items()
                        if job.get("status") in {"completed", "failed"}]
        if finished_ids:
            placeholders = ",".join("?" for _ in finished_ids)
            with _connect_db() as connection:
                connection.execute(f"DELETE FROM jobs WHERE job_id IN ({placeholders})", finished_ids)
            for job_id in finished_ids:
                JOBS.pop(job_id, None)
    return jsonify({"deleted": len(finished_ids), "message": "Finished job history cleared"})


@app.delete("/tasks/<job_id>")
def delete_task(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "task not found"}), 404
        if job.get("status") not in {"completed", "failed"}:
            return jsonify({"error": "only completed or failed jobs can be deleted"}), 409
        with _connect_db() as connection:
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        JOBS.pop(job_id, None)
    return jsonify({"deleted": 1, "job_id": job_id})


@app.get("/tasks/<job_id>")
def get_task(job_id):
    with JOBS_LOCK:
        job = dict(JOBS[job_id]) if job_id in JOBS else None
    return (jsonify(job), 200) if job else (jsonify({"error": "task not found"}), 404)


@app.get("/tasks/<job_id>/process")
def get_task_process(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        result = {"job_id": job_id, "status": job["status"], "process": job.get("process"),
                  "events": list(job.get("events", []))} if job else None
        if result and job.get("report"):
            report = job["report"]
            result["result"] = {key: report.get(key) for key in
                                ("best", "selection", "target_met",
                                 "trials", "category_tests", "artifacts", "task")}
    return (jsonify(result), 200) if result else (jsonify({"error": "task not found"}), 404)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
