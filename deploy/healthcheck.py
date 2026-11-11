"""Liveness checks; read-only and independent of provider availability."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import urlopen


def healthy(role):
    try:
        if role == "web":
            with urlopen("http://127.0.0.1:"+str(int(os.getenv("PORT","8501")))+"/_stcore/health", timeout=3) as response:
                return response.status == 200
        if role != "worker":
            return False
        if os.environ.get("CUSTOMER_STORAGE", "local").lower() == "supabase":
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
            from pilot.cloud_db import db, worker_heartbeat_age
            with db() as c:
                age = worker_heartbeat_age(c)
                return age is not None and 0 <= age < 15
        path = Path(os.environ.get("CUSTOMER_DATA_ROOT", "/var/lib/verifier/customer_data"))/"control.sqlite3"
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as c:
            row = c.execute("SELECT heartbeat FROM worker_status WHERE id=1").fetchone()
            return bool(row and 0 <= time.time()-row[0] < 15)
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(0 if len(sys.argv) == 2 and healthy(sys.argv[1]) else 1)
