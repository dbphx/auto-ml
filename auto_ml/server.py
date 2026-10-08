import threading
import uuid
from datetime import datetime, timezone
from flask import Flask, jsonify, request

from .trainer import train_job

app = Flask(__name__)
JOBS = {}
JOBS_LOCK = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _run(job_id, config):
    try:
        def update_job(update):
            with JOBS_LOCK:
                job = JOBS[job_id]
                job.update(update)
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
        if request.path == "/hook" and not llm_choice:
            return jsonify({"error": "llm is required for codex mode (external or internal)"}), 400
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
        config.setdefault("target_accuracy", 0.90)
        config.setdefault("category_target_accuracy", config["target_accuracy"])
    if normal_path:
        if not isinstance(normal_path, str) or not isinstance(attack_path, str):
            return jsonify({"error": "normal-path and attack-path must be strings"}), 400
        config["normal_path"] = normal_path
        config["attack_path"] = attack_path
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
    threading.Thread(target=_run, args=(job_id, config), daemon=True).start()
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
    return (jsonify(result), 200) if result else (jsonify({"error": "task not found"}), 404)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
