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
from services.observability import event, model_call_summary
from pilot.model_connections import ModelConnectionError, credentials_for_job
from pilot.pdf_limits import check_pdf
from services.failure_details import describe_failure
from pilot.cloud_db import enabled as cloud_enabled


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
    request_calls = []
    last_stage = "Starting"

    def save(status="running", stage="Working", result=None):
        with db() as c:
            authorize(c, access)
            if not c.execute("SELECT 1 FROM jobs WHERE id=? AND run_token=? AND status='running'", (job_id, token)).fetchone():
                raise RuntimeError("Job ownership changed.")
            visible_result = result if result is not None else {"results": checkpoint.get("results", [])}
            c.execute("UPDATE jobs SET status=?,stage=?,result=?,checkpoint=?,updated=? WHERE id=? AND run_token=?", (status, stage, json.dumps(visible_result, default=str), json.dumps(checkpoint, default=str), time.time(), job_id, token))

    class Reporter:
        def update(self, stage, completed=0, total=None, message=None):
            nonlocal last_stage
            last_stage = str(stage)
            save(stage=str(stage)[:160])
        def log(self, message):
            pass  # Do not publish backend paths, prompts or provider error bodies.

    reporter = Reporter()
    previous_key = os.environ.get("OPENROUTER_API_KEY")
    using_customer_key = False
    try:
        with db() as c:
            authorize(c, access)
        if cloud_enabled() and os.getenv("CUSTOMER_WORKSPACE_ID") != access.workspace:
            raise RuntimeError("Cloud worker workspace does not match the assigned request.")
        version = code_version()
        if checkpoint.get("code_version") and checkpoint["code_version"] != version:
            save("paused", "Code changed. Start a new request to use this version.",
                 {"results": checkpoint.get("results", []), "failure": describe_failure(kind="code_changed")})
            return
        checkpoint["code_version"] = version
        from services.model_gateway import configuration_snapshot, capture_calls, configure
        if row["kind"] in {"process", "verify"}:
            key, selected = credentials_for_job(access, payload)
            # This process runs one job. Never set customer credentials in the UI or supervisor.
            os.environ["OPENROUTER_API_KEY"] = key
            using_customer_key = True
            configure(selected)
        config = configuration_snapshot()
        if checkpoint.get("models") and checkpoint["models"] != config:
            save("paused", "Model settings changed. Restore them to resume, or create a new request.",
                 {"results": checkpoint.get("results", []), "failure": describe_failure(kind="settings_changed")})
            return
        checkpoint["models"] = config
        if cloud_enabled() and row["kind"] in {"process", "verify"}:
            from pilot.cloud_files import hydrate
            hydrate(access, [payload["document_id"]] if row["kind"] == "process" else payload["documents"])
        if row["kind"] == "process":
            from pipelines.document_pipeline import process_document
            doc_id = payload["document_id"]
            with db() as c:
                source = document(c, access, doc_id)
            data = file_path(access.workspace, "data/documents", uid(doc_id), "original.pdf").read_bytes()
            if hashlib.sha256(data).hexdigest() != source["sha"]:
                raise ValueError("Source version changed.")
            check_pdf(data)
            with capture_calls() as (calls, start):
                try:
                    result = process_document(data, source["name"], doc_id, reporter=reporter, **payload["options"])
                finally:
                    request_calls = list(calls[start:])
            if cloud_enabled():
                from pilot.cloud_files import publish
                publish(access,doc_id,job_id,token)
            with db() as c:
                authorize(c, access)
                c.execute("UPDATE documents SET status='ready',metadata=? WHERE id=? AND workspace=?", (json.dumps(result), doc_id, access.workspace))
            save("complete", "Document ready", {"document_id": doc_id, "model_calls": [model_call_summary(x) for x in request_calls]})
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
                    request_calls = list(calls[start:])
                    checkpoint["parsing_calls"] = [model_call_summary(x) for x in calls[start:]]
                if not parsed or any(p.get("parsing_error") for p in parsed):
                    failure = next((p for p in parsed or [] if p.get("parsing_error")), {})
                    event("customer_job_paused", level="warning", status="paused",
                          stage="claim_parser", error_kind=failure.get("error_kind"))
                    save("paused", "Verification failed while understanding the claim; retry when service is available.",
                         {"results": checkpoint.get("results", []), "error_stage": "claim_parser",
                          "error_kind": failure.get("error_kind"), "model_calls": checkpoint["parsing_calls"],
                          "failure": describe_failure(kind=failure.get("error_kind", ""),
                              message=failure.get("parsing_error", ""), calls=request_calls, stage="claim_parser")})
                    return
                checkpoint["parsed"] = parsed
                checkpoint["results"] = []
                save(stage="Claims understood")
            results = checkpoint["results"]
            for pc in checkpoint["parsed"][len(results):]:
                started = time.perf_counter()
                with capture_calls() as (calls, start):
                    result = _verify_one(pc, payload["documents"], reporter)
                    request_calls = list(calls[start:])
                    result["model_calls"] = [model_call_summary(x) for x in calls[start:]]
                result["processing_time_seconds"] = time.perf_counter()-started
                if result.get("status") == "error":
                    event("customer_job_paused", level="warning", status="paused",
                          stage=result.get("error_stage"), error_kind=result.get("error_kind"))
                    save("paused", "Verification failed — retry. Completed subclaims were saved.",
                         {"results": results, "error_stage": result.get("error_stage"),
                          "error_kind": result.get("error_kind"), "model_calls": result["model_calls"],
                          "failure": describe_failure(kind=result.get("error_kind", ""),
                              message=result.get("reason", ""), calls=request_calls, stage=result.get("error_stage") or "verification")})
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
            if cloud_enabled():
                from pilot.cloud_files import delete_document as delete_private_document
                delete_private_document(access,doc_id)
            cache = file_path(access.workspace, "data/model_cache", uid(doc_id))
            if cache.exists():
                shutil.rmtree(cache)
            with db() as c:
                authorize(c, access)
                c.execute("DELETE FROM documents WHERE id=? AND workspace=?", (doc_id, access.workspace))
            save("complete", "Source and its index deleted; saved review snapshots retained")
        else:
            raise ValueError("Unknown job kind.")
    except ModelConnectionError as exc:
        event("customer_job_paused", level="warning", status="paused",
              stage="openrouter_connection", error_kind=type(exc).__name__)
        details = describe_failure(exc, stage="openrouter_connection")
        save("paused", details["title"], {"results": checkpoint.get("results", []),
                                 "error_kind": type(exc).__name__, "error_stage": "openrouter_connection",
                                 "failure": details})
    except Exception as exc:
        # Persist useful classification, but never expose raw provider exception bodies.
        event("customer_job_failed", level="error", status="failed",
              stage=row["kind"], error_kind=type(exc).__name__)
        if not request_calls and getattr(exc, "metadata", None):
            request_calls = [exc.metadata]
        if cloud_enabled() and row["kind"] == "process":
            try:
                from pilot.cloud_files import publish
                publish(access,payload["document_id"],job_id,token)
            except Exception:
                event("customer_cloud_checkpoint_unavailable",level="warning",status="paused")
        details = describe_failure(exc, calls=request_calls, stage=last_stage if last_stage != "Starting" else getattr(exc, "stage", row["kind"]))
        save("failed", "Processing failed — " + details["title"],
             {"error_kind": type(exc).__name__, "error_stage": getattr(exc, "stage", row["kind"]),
              "results": checkpoint.get("results", []), "failure": details,
              "model_calls": [model_call_summary(x) for x in request_calls]})
        with db() as c:
            if row["kind"] == "process":
                c.execute("UPDATE documents SET status='error' WHERE id=? AND workspace=?", (payload.get("document_id"), access.workspace))
    finally:
        if cloud_enabled() and row["kind"] in {"process","verify"}:
            try:
                from pilot.cloud_files import publish_query_cache
                publish_query_cache(access)
            except Exception:
                event("customer_query_cache_not_saved",level="warning",status="error")
        if using_customer_key:
            if previous_key is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = previous_key


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
