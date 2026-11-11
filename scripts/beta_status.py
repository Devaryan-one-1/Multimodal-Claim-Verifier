"""Read-only operator health summary. No prompts, keys or provider requests."""
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))


@contextmanager
def connection():
    if os.getenv("CUSTOMER_STORAGE", "local").lower() == "supabase":
        from pilot.cloud_db import db
        with db() as c:
            c.execute("SET TRANSACTION READ ONLY")
            yield c
        return
    path = Path(os.getenv("CUSTOMER_DATA_ROOT", str(PROJECT / "customer_data"))) / "control.sqlite3"
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as c:
        c.execute("PRAGMA query_only=ON")
        yield c


def snapshot(c, now=None):
    now = time.time() if now is None else now
    if os.getenv("CUSTOMER_STORAGE", "local").lower() == "supabase":
        from pilot.cloud_db import worker_heartbeat_age
        age = worker_heartbeat_age(c)
    else:
        row = c.execute("SELECT heartbeat FROM worker_status WHERE id=1").fetchone()
        age = now-row[0] if row else None
    if age is not None and age < 0:
        age = None
    counts = {row[0]: row[1] for row in c.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status").fetchall()}
    queued = c.execute("SELECT MIN(created) FROM jobs WHERE status='queued'").fetchone()[0]
    running = c.execute("SELECT MIN(updated) FROM jobs WHERE status='running'").fetchone()[0]
    recent = c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('failed','paused') AND updated>=?", (now-86400,)).fetchone()[0]
    warnings = []
    if age is None or not 0 <= age < 15:
        warnings.append("worker_unavailable")
    if queued is not None and now-queued > 300:
        warnings.append("queue_wait_over_5_minutes")
    if running is not None and now-running > int(os.getenv("CUSTOMER_JOB_TIMEOUT_SECONDS", "1800"))+30:
        warnings.append("running_job_without_recent_progress")
    if recent:
        warnings.append("failed_or_paused_jobs_in_last_24_hours")
    return {"status": "attention" if warnings else "ok", "worker_age_seconds": round(age, 1) if age is not None else None,
            "job_counts": {key: counts.get(key, 0) for key in ("queued", "running", "complete", "failed", "paused")},
            "failed_or_paused_last_24_hours": recent,
            "oldest_queue_wait_seconds": max(0, round(now-queued)) if queued is not None else None,
            "warnings": warnings, "provider_calls": 0,
            "google_embedding_usage": "not_measured; check the operator project in Google AI Studio"}


def main():
    from dotenv import load_dotenv
    load_dotenv(PROJECT / ".env", override=False)
    try:
        with connection() as c:
            report = snapshot(c)
    except Exception as exc:
        report = {"status": "unavailable", "code": "monitor_storage_unavailable",
                  "error_type": type(exc).__name__, "provider_calls": 0}
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
