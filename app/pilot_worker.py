"""Run one supervisor on the same persistent host as the customer UI."""
from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
load_dotenv(PROJECT / ".env", override=False)
from pilot.store import db, initialize, root, workspace_dir
from services.failure_details import describe_failure
from pilot.cloud_db import enabled as cloud_enabled


def exclusive_lock():
    if cloud_enabled():
        from pilot.cloud_db import WorkerLock
        return WorkerLock()
    handle = (root() / "worker.lock").open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit("Another customer worker is already running.")
    return handle


def heartbeat():
    with db() as c:
        if cloud_enabled():
            c.execute("""INSERT INTO worker_status VALUES(1,
                EXTRACT(EPOCH FROM clock_timestamp())::double precision)
                ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat""")
        else:
            c.execute("INSERT INTO worker_status VALUES(1,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat", (time.time(),))


def recover_cloud_jobs():
    """Only after acquiring the database's exclusive worker lock."""
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        for row in c.execute("SELECT id,result FROM jobs WHERE status='running'").fetchall():
            result=json.loads(row["result"])
            result["failure"]=describe_failure(kind="worker_interrupted")
            c.execute("UPDATE jobs SET status='paused',stage='Worker restarted; explicit retry required',run_token=NULL,result=?,updated=? WHERE id=? AND status='running'",
                      (json.dumps(result),time.time(),row["id"]))


def main(stop=None):
    stop = stop or threading.Event()
    initialize()
    lock = exclusive_lock()
    if cloud_enabled():
        try:
            recover_cloud_jobs()
        except BaseException:
            lock.close()
            raise
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        while not stop.is_set():
            if cloud_enabled():
                lock.check()
            heartbeat()
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    token = uuid.uuid4().hex
                    c.execute("UPDATE jobs SET status='running',stage='Starting',attempts=attempts+1,run_token=?,updated=? WHERE id=?", (token, time.time(), row["id"]))
            if not row:
                stop.wait(2)
                continue
            env = os.environ.copy()
            env["CUSTOMER_DATA_ROOT"] = str(root())
            env["CUSTOMER_WORKSPACE_ID"] = row["workspace"]
            env["CUSTOMER_JOB_ID"], env["CUSTOMER_JOB_TOKEN"] = row["id"], token
            # The worker does not inherit browser state or a customer-supplied model configuration.
            env["CLAIM_VERIFIER_LOG_DIR"] = str(workspace_dir(row["workspace"]) / "data/logs")
            task = None
            started = time.monotonic()
            try:
                if not stop.is_set():
                    task = subprocess.Popen([sys.executable, str(PROJECT / "app/pilot_task.py"), row["id"], token], cwd=workspace_dir(row["workspace"]), env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while task is not None and task.poll() is None:
                    if cloud_enabled():
                        lock.check()
                    heartbeat()
                    if stop.is_set() or time.monotonic()-started > int(os.getenv("CUSTOMER_JOB_TIMEOUT_SECONDS", "3600")):
                        break
                    stop.wait(2)
            finally:
                if task is not None and task.poll() is None:
                    task.terminate()
                    try:
                        task.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        task.kill(); task.wait()
                with db() as c:
                    pending = c.execute("SELECT result FROM jobs WHERE id=? AND status='running' AND run_token=?", (row["id"], token)).fetchone()
                    if pending:
                        result = json.loads(pending["result"])
                        reason = ("worker_stopped" if stop.is_set() else "worker_timeout"
                                  if time.monotonic()-started > int(os.getenv("CUSTOMER_JOB_TIMEOUT_SECONDS", "3600"))
                                  else "worker_interrupted")
                        result["failure"] = describe_failure(kind=reason)
                        c.execute("UPDATE jobs SET status='paused',stage='Worker stopped or timed out; explicit retry required',result=?,updated=? WHERE id=? AND status='running' AND run_token=?", (json.dumps(result), time.time(), row["id"], token))
    finally:
        lock.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
