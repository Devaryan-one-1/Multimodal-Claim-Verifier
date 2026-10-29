"""Internal worker child. Never run as a public HTTP endpoint."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
from pilot.store import Access, authorize, db, document, file_path, uid, workspace_dir


def code_version():
    digest = hashlib.sha256()
    for path in sorted((PROJECT / "app").rglob("*.py")):
        digest.update(str(path.relative_to(PROJECT)).encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def run(job_id, token):
    with db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=? AND run_token=? AND status='running'", (uid(job_id), uid(token))).fetchone()
        if not row:
            return
        row = dict(row)
    access = Access(row["principal"], row["workspace"], time.time()+7200)
    if Path.cwd().resolve() != workspace_dir(access.workspace).resolve():
        raise RuntimeError("Worker must run inside the assigned workspace.")
    payload = json.loads(row["payload"])
    checkpoint = json.loads(row["checkpoint"])

    def save(status="running", stage="Working", result=None):
        with db() as c:
            authorize(c, access)
            if not c.execute("SELECT 1 FROM jobs WHERE id=? AND run_token=? AND status='running'", (job_id, token)).fetchone():
                raise RuntimeError("Job ownership changed.")
            visible_result = result if result is not None else {"results": checkpoint.get("results", [])}
            c.execute("UPDATE jobs SET status=?,stage=?,result=?,checkpoint=?,updated=? WHERE id=? AND run_token=?", (status, stage, json.dumps(visible_result, default=str), json.dumps(checkpoint, default=str), time.time(), job_id, token))

    class Reporter:
        def update(self, stage, completed=0, total=None, message=None):
            save(stage=str(stage)[:160])
        def log(self, message):
            pass  # Do not publish backend paths, prompts or provider error bodies.

    reporter = Reporter()
    try:
        with db() as c:
            authorize(c, access)
        version = code_version()
        if checkpoint.get("code_version") and checkpoint["code_version"] != version:
            save("paused", "Code changed. Create a new review to use this version.", {"results": checkpoint.get("results", [])})
            return
        checkpoint["code_version"] = version
        from services.model_gateway import configuration_snapshot, capture_calls
        config = configuration_snapshot()
        if checkpoint.get("models") and checkpoint["models"] != config:
            save("paused", "Model settings changed. Restore them to resume, or create a new review.", {"results": checkpoint.get("results", [])})
            return
        checkpoint["models"] = config
        if row["kind"] == "process":
            from pipelines.document_pipeline import process_document
            doc_id = payload["document_id"]
            with db() as c:
                source = document(c, access, doc_id)
            data = file_path(access.workspace, "data/documents", uid(doc_id), "original.pdf").read_bytes()
            if hashlib.sha256(data).hexdigest() != source["sha"]:
                raise ValueError("Source version changed.")
            result = process_document(data, source["name"], doc_id, reporter=reporter, **payload["options"])
            with db() as c:
                authorize(c, access)
                c.execute("UPDATE documents SET status='ready',metadata=? WHERE id=? AND workspace=?", (json.dumps(result), doc_id, access.workspace))
            save("complete", "Document ready", {"document_id": doc_id})
        elif row["kind"] == "verify":
            from verification.claim_parser import parse_claims
            from pipelines.verification_pipeline import _verify_one
            for doc_id, sha in payload["versions"].items():
                with db() as c:
                    source = document(c, access, doc_id, ready=True)
                data = file_path(access.workspace, "data/documents", uid(doc_id), "original.pdf").read_bytes()
                if source["sha"] != sha or hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("Source version changed.")
            if "parsed" not in checkpoint:
                reporter.update("Understanding claims")
                with capture_calls() as (calls, start):
                    parsed = parse_claims(payload["claim"])
                    checkpoint["parsing_calls"] = [{k: x.get(k) for k in ("provider", "model", "status", "usage", "elapsed_ms")} for x in calls[start:]]
                if not parsed or any(p.get("parsing_error") for p in parsed):
                    save("paused", "Verification failed while understanding the claim; retry when service is available.")
                    return
                checkpoint["parsed"] = parsed
                checkpoint["results"] = []
                save(stage="Claims understood")
            results = checkpoint["results"]
            for pc in checkpoint["parsed"][len(results):]:
                started = time.perf_counter()
                with capture_calls() as (calls, start):
                    result = _verify_one(pc, payload["documents"], reporter)
                    result["model_calls"] = [{k: x.get(k) for k in ("provider", "model", "status", "usage", "elapsed_ms")} for x in calls[start:]]
                result["processing_time_seconds"] = time.perf_counter()-started
                if result.get("status") == "error":
                    save("paused", "Verification failed — retry. Completed subclaims were saved.", {"results": results, "error_stage": result.get("error_stage")})
                    return
                results.append(result)
                save(stage=f"Checked {len(results)} of {len(checkpoint['parsed'])} claims", result={"results": results})
            save("complete", "Review ready", {"results": results, "models": config, "code_version": version,
                                               "source_versions": payload["versions"], "parsing_calls": checkpoint.get("parsing_calls", []),
                                               "cost": None, "cost_note": "Provider cost is not available; do not infer a zero cost."})
        elif row["kind"] == "delete":
            from services.documents import delete_document
            doc_id = payload["document_id"]
            with db() as c:
                document(c, access, doc_id)
            delete_document(doc_id)
            cache = file_path(access.workspace, "data/model_cache", uid(doc_id))
            if cache.exists():
                shutil.rmtree(cache)
            with db() as c:
                authorize(c, access)
                c.execute("DELETE FROM documents WHERE id=? AND workspace=?", (doc_id, access.workspace))
            save("complete", "Source and its index deleted; saved review snapshots retained")
        else:
            raise ValueError("Unknown job kind.")
    except Exception as exc:
        # Persist useful classification, but never expose raw provider exception bodies.
        with db() as c:
            c.execute("UPDATE jobs SET status='failed',stage=?,result=?,updated=? WHERE id=? AND run_token=?", ("Processing failed — retry or contact the administrator", json.dumps({"error_kind": type(exc).__name__, "results": checkpoint.get("results", [])}), time.time(), job_id, token))
            if row["kind"] == "process":
                c.execute("UPDATE documents SET status='error' WHERE id=? AND workspace=?", (payload.get("document_id"), access.workspace))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
