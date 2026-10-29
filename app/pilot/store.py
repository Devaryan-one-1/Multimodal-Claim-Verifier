from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]

def root():
    path = Path(os.getenv("CUSTOMER_DATA_ROOT", str(PROJECT / "customer_data"))).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def uid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid resource identifier.")
    return value


def principal(issuer, subject):
    return hashlib.sha256(json.dumps([issuer, subject], separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Access:
    principal: str
    workspace: str
    expires: float


@contextmanager
def db():
    connection = sqlite3.connect(root() / "control.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript('''
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(principal TEXT, workspace TEXT REFERENCES workspaces(id), PRIMARY KEY(principal,workspace));
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, workspace TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL, sha TEXT NOT NULL, status TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', context TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, UNIQUE(workspace,sha));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, workspace TEXT NOT NULL REFERENCES workspaces(id), principal TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', checkpoint TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL, run_token TEXT);
CREATE TABLE IF NOT EXISTS decisions(job TEXT NOT NULL REFERENCES jobs(id), atom INTEGER NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL, reviewer TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(job,atom));
CREATE TABLE IF NOT EXISTS decision_events(id INTEGER PRIMARY KEY, job TEXT NOT NULL, atom INTEGER NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL, reviewer TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS worker_status(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL);
''')


def authorize(c, access):
    uid(access.workspace)
    if access.expires <= time.time() or not c.execute("SELECT 1 FROM members WHERE principal=? AND workspace=?", (access.principal, access.workspace)).fetchone():
        raise PermissionError("Workspace access is unavailable. Sign in again or contact the administrator.")


def memberships(who):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT w.* FROM workspaces w JOIN members m ON m.workspace=w.id WHERE m.principal=? ORDER BY w.name", (who,))]


def workspace_dir(workspace):
    # Only server-generated IDs are accepted; never take a client-supplied path.
    base = root() / "workspaces"
    base.mkdir(exist_ok=True)
    path = base / uid(workspace)
    if path.is_symlink():
        raise PermissionError("Symbolic workspace paths are not allowed.")
    path.mkdir(exist_ok=True)
    if path.resolve().parent != base.resolve():
        raise PermissionError("Invalid workspace path.")
    return path


def file_path(workspace, *parts):
    base = workspace_dir(workspace).resolve()
    path = base.joinpath(*parts)
    if not path.resolve().is_relative_to(base):
        raise PermissionError("File is outside the workspace.")
    return path


def document(c, access, document_id, ready=False):
    authorize(c, access)
    row = c.execute("SELECT * FROM documents WHERE id=? AND workspace=?", (uid(document_id), access.workspace)).fetchone()
    if not row or (ready and row["status"] != "ready"):
        raise PermissionError("The source is unavailable in this workspace.")
    return dict(row)


def document_bytes(access, document_id):
    with db() as c:
        row = document(c, access, document_id)
        data = file_path(access.workspace, "data", "documents", uid(document_id), "original.pdf").read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha"]:
            raise ValueError("The source version changed. Upload it as a new document.")
        return data


def documents(access):
    with db() as c:
        authorize(c, access)
        return [dict(r) for r in c.execute("SELECT * FROM documents WHERE workspace=? ORDER BY created DESC", (access.workspace,))]


def queue(c, access, kind, payload):
    authorize(c, access)
    pending = c.execute("SELECT count(*) FROM jobs WHERE workspace=? AND status IN ('queued','running')", (access.workspace,)).fetchone()[0]
    if pending >= 10:
        raise ValueError("Ten requests are already queued. Wait for processing to finish.")
    today = c.execute("SELECT count(*) FROM jobs WHERE workspace=? AND created>?", (access.workspace, time.time()-86400)).fetchone()[0]
    if today >= int(os.getenv("CUSTOMER_JOBS_PER_DAY", "100")):
        raise ValueError("Workspace daily request limit reached.")
    job = uuid.uuid4().hex
    now = time.time()
    c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,created,updated) VALUES(?,?,?,?,?,'queued','Waiting for worker',?,?)", (job, access.workspace, access.principal, kind, json.dumps(payload), now, now))
    return job


def upload(access, filename, data, options):
    with db() as c:
        authorize(c, access)
    import pymupdf
    limit = int(os.getenv("CUSTOMER_MAX_UPLOAD_MB", "10"))
    if not data or len(data) > limit*1024*1024:
        raise ValueError(f"Upload a nonempty PDF up to {limit} MB.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.needs_pass or not 1 <= pdf.page_count <= int(os.getenv("CUSTOMER_MAX_PAGES", "500")):
                raise ValueError()
    except Exception as exc:
        raise ValueError("Use an unlocked, readable PDF within the page limit.") from exc
    sha = hashlib.sha256(data).hexdigest()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        authorize(c, access)
        old = c.execute("SELECT id FROM documents WHERE workspace=? AND sha=?", (access.workspace, sha)).fetchone()
        if old:
            return old["id"]
        count = c.execute("SELECT count(*) FROM documents WHERE workspace=?", (access.workspace,)).fetchone()[0]
        if count >= int(os.getenv("CUSTOMER_MAX_DOCUMENTS", "30")):
            raise ValueError("Workspace document limit reached.")
        doc = uuid.uuid4().hex
        target = file_path(access.workspace, "data", "documents", doc, "original.pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        # Ignore upload names for paths. Keep a bounded display-only name.
        name = filename.replace("\\", "/").split("/")[-1][:180]
        try:
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,'queued',?)", (doc, access.workspace, name, sha, time.time()))
            queue(c, access, "process", {"document_id": doc, "options": {"enable_ocr": bool(options.get("enable_ocr", True)), "chart_mode": "candidate pages", "visual_limit": min(30, max(1, int(options.get("visual_limit", 20))))}})
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return doc


def submit_review(access, name, claim, document_ids):
    claim = claim.strip()
    if not claim or len(claim) > 6000:
        raise ValueError("Enter between 1 and 6,000 characters.")
    ids = list(dict.fromkeys(document_ids))
    if not 1 <= len(ids) <= 3:
        raise ValueError("Select one to three source documents.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        versions = {d: document(c, access, d, ready=True)["sha"] for d in ids}
        return queue(c, access, "verify", {"name": name[:160] or "Financial review", "claim": claim, "documents": ids, "versions": versions})


def jobs(access, kind=None):
    with db() as c:
        authorize(c, access)
        rows = c.execute("SELECT * FROM jobs WHERE workspace=? ORDER BY created DESC", (access.workspace,))
        return [dict(r) for r in rows if kind is None or r["kind"] == kind]


def get_job(access, job):
    with db() as c:
        authorize(c, access)
        row = c.execute("SELECT * FROM jobs WHERE id=? AND workspace=?", (uid(job), access.workspace)).fetchone()
        if not row:
            raise PermissionError("Review not found in this workspace.")
        return dict(row)


def resume(access, job):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        authorize(c, access)
        row = c.execute("SELECT * FROM jobs WHERE id=? AND workspace=?", (uid(job), access.workspace)).fetchone()
        if not row or row["status"] not in {"failed", "paused"} or row["attempts"] >= 3:
            raise ValueError("This request cannot be retried; at most three attempts are allowed.")
        c.execute("UPDATE jobs SET status='queued',stage='Queued for explicit retry',updated=? WHERE id=?", (time.time(), job))


def save_context(access, doc, company, period, currency):
    with db() as c:
        document(c, access, doc)
        context = {"company": company[:160], "period": period[:80], "currency": currency[:20], "confirmed_by": access.principal, "confirmed_at": time.time()}
        c.execute("UPDATE documents SET context=? WHERE id=? AND workspace=?", (json.dumps(context), doc, access.workspace))


def decide(access, job, atom, decision, note, expected_updated=None):
    if decision not in {"unreviewed", "reviewed", "incorrect_evidence"}:
        raise ValueError("Invalid review decision.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        authorize(c, access)
        row = c.execute("SELECT result FROM jobs WHERE id=? AND workspace=?", (uid(job), access.workspace)).fetchone()
        atoms = json.loads(row["result"]).get("results", []) if row else []
        if not isinstance(atom, int) or not 0 <= atom < len(atoms):
            raise ValueError("Result is not available.")
        existing = c.execute("SELECT updated FROM decisions WHERE job=? AND atom=?", (job, atom)).fetchone()
        if (existing["updated"] if existing else None) != expected_updated:
            raise ValueError("Another reviewer changed this decision. Refresh before saving.")
        values = (job, atom, decision, note[:2000], access.principal, time.time())
        c.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?) ON CONFLICT(job,atom) DO UPDATE SET decision=excluded.decision,note=excluded.note,reviewer=excluded.reviewer,updated=excluded.updated", values)
        c.execute("INSERT INTO decision_events(job,atom,decision,note,reviewer,updated) VALUES(?,?,?,?,?,?)", values)


def decisions(access, job):
    get_job(access, job)
    with db() as c:
        authorize(c, access)
        return {r["atom"]: dict(r) for r in c.execute("SELECT * FROM decisions WHERE job=?", (job,))}


def request_delete(access, doc):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        document(c, access, doc)
        if c.execute("SELECT 1 FROM jobs WHERE workspace=? AND status IN ('queued','running')", (access.workspace,)).fetchone():
            raise ValueError("Finish queued work before deleting a document.")
        job = queue(c, access, "delete", {"document_id": doc})
        c.execute("UPDATE documents SET status='deleting' WHERE id=? AND workspace=?", (doc, access.workspace))
        return job
