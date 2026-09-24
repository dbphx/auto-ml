import threading
import uuid
from flask import Flask, jsonify, request

from .trainer import train_job

app = Flask(__name__)
JOBS = {}


def _run(job_id, config):
    try:
        report = train_job(config, lambda update: JOBS[job_id].update(update))
        JOBS[job_id].update({"status": "completed", "report": report})
    except Exception as exc:
        JOBS[job_id].update({"status": "failed", "error": str(exc)})


@app.post("/jobs")
def create_job():
    config = request.get_json(silent=True) or {}
    job_id = uuid.uuid4().hex
    config["job_id"] = job_id
    JOBS[job_id] = {"job_id": job_id, "status": "queued"}
    threading.Thread(target=_run, args=(job_id, config), daemon=True).start()
    return jsonify(JOBS[job_id]), 202


@app.get("/jobs/<job_id>")
def get_job(job_id):
    job = JOBS.get(job_id)
    return (jsonify(job), 200) if job else (jsonify({"error": "job not found"}), 404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

