"""Run one supervisor on the same persistent host as the customer UI."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
load_dotenv(PROJECT / ".env", override=False)
from pilot.store import db, initialize, root, workspace_dir


def exclusive_lock():
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
        c.execute("INSERT INTO worker_status VALUES(1,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat", (time.time(),))


def main():
    initialize()
    lock = exclusive_lock()
    try:
        while True:
            heartbeat()
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    token = uuid.uuid4().hex
                    c.execute("UPDATE jobs SET status='running',stage='Starting',attempts=attempts+1,run_token=?,updated=? WHERE id=?", (token, time.time(), row["id"]))
            if not row:
                time.sleep(2)
                continue
            env = os.environ.copy()
            env["CUSTOMER_DATA_ROOT"] = str(root())
            # The worker does not inherit browser state or a customer-supplied model configuration.
            env["CLAIM_VERIFIER_LOG_DIR"] = str(workspace_dir(row["workspace"]) / "data/logs")
            task = subprocess.Popen([sys.executable, str(PROJECT / "app/pilot_task.py"), row["id"], token], cwd=workspace_dir(row["workspace"]), env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            started = time.monotonic()
            try:
                while task.poll() is None:
                    heartbeat()
                    if time.monotonic()-started > int(os.getenv("CUSTOMER_JOB_TIMEOUT_SECONDS", "3600")):
                        task.terminate()
                        try:
                            task.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            task.kill(); task.wait()
                        break
                    time.sleep(2)
            except BaseException:
                task.terminate()
                try:
                    task.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    task.kill(); task.wait()
                raise
            finally:
                with db() as c:
                    c.execute("UPDATE jobs SET status='paused',stage='Worker stopped or timed out; explicit retry required',updated=? WHERE id=? AND status='running' AND run_token=?", (time.time(), row["id"], token))
    finally:
        lock.close()


if __name__ == "__main__":
    main()
