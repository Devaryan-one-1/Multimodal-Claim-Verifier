"""Small, privacy-conscious JSON event log; no prompts, PDF text or credentials."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

_request = ContextVar("claim_verifier_request", default=None)
_lock = threading.Lock()
_FIELDS = {
    "document_id", "page", "count", "status", "stage", "error_kind",
    "verdict", "provider", "model", "task", "duration_seconds", "elapsed_ms",
    "size_bytes", "claim_length", "document_count", "operation", "available",
    "table_count", "chart_count", "ocr_page_count", "evidence_count", "chunk_count",
    "requested_model", "actual_model", "upstream_provider", "http_status",
    "provider_request_id", "retry_after_seconds",
}


class EventFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname, "event": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            **getattr(record, "event_fields", {}),
        }, ensure_ascii=False, default=str)


def configure_logging():
    logger = logging.getLogger("claim_verifier.events")
    with _lock:
        if getattr(logger, "_configured", False):
            return logger
        logger.setLevel(logging.INFO)
        logger.propagate = False
        formatter = EventFormatter()
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
        try:
            folder = Path(os.getenv("CLAIM_VERIFIER_LOG_DIR", "data/logs"))
            folder.mkdir(parents=True, exist_ok=True)
            # Per-process files avoid rotation races across separate workers.
            handler = RotatingFileHandler(folder / f"app-{os.getpid()}.jsonl",
                                          maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except OSError:
            logger.warning("file_logging_unavailable")
        logger._configured = True
    return logger


def event(name, *, level="info", **fields):
    safe = {k: v for k, v in fields.items() if k in _FIELDS and
            (v is None or isinstance(v, (str, int, float, bool)))}
    safe = {k: v[:200] if isinstance(v, str) else v for k, v in safe.items()}
    logger = configure_logging()
    logger.log({"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}.get(level, logging.INFO),
               name, extra={"request_id": _request.get(), "event_fields": safe})


def operation(name):
    """Correlate nested synchronous workflow events; preserve public signatures."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            token = _request.set(_request.get() or uuid.uuid4().hex)
            started = time.perf_counter()
            event(name + "_started")
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                event(name + "_failed", level="error", error_kind=type(exc).__name__,
                      stage=getattr(exc, "stage", name), duration_seconds=time.perf_counter() - started)
                raise
            else:
                # Finished means the function returned; it does not imply factual success.
                event(name + "_finished", duration_seconds=time.perf_counter() - started)
                return result
            finally:
                _request.reset(token)
        return wrapped
    return decorate


def model_call_summary(record):
    """Share allowlisted diagnostics; omit prompts, credentials and error bodies."""
    elapsed = record.get("elapsed_ms")
    duration = record.get("duration_seconds")
    if elapsed is None and isinstance(duration, (int, float)):
        elapsed = round(duration * 1000, 1)
    usage = record.get("usage") or {}
    usage_fields = {"prompt_tokens", "completion_tokens", "total_tokens", "cost",
                    "prompt_token_count", "candidates_token_count", "total_token_count",
                    "thoughts_token_count", "cached_content_token_count"}
    return {
        "provider": record.get("gateway_provider") or record.get("provider"),
        "model": record.get("actual_model") or record.get("requested_model") or record.get("model"),
        "requested_model": record.get("requested_model") or record.get("model"),
        "actual_model": record.get("actual_model"),
        "upstream_provider": record.get("upstream_provider"),
        "status": record.get("status"), "task": record.get("task"), "elapsed_ms": elapsed,
        "http_status": record.get("http_status"),
        "error_kind": record.get("error_type") or record.get("error_kind"),
        "provider_request_id": record.get("request_id") or record.get("provider_request_id"),
        "retry_after_seconds": record.get("retry_after_seconds"),
        "finish_reason": record.get("finish_reason") if record.get("finish_reason") in ("stop", "length", "content_filter", "tool_calls") else None,
        "output_token_limit": record.get("output_token_limit") if isinstance(record.get("output_token_limit"), int)
                              and not isinstance(record.get("output_token_limit"), bool)
                              and 256 <= record["output_token_limit"] <= 131072 else None,
        "usage": {k: v for k, v in usage.items() if k in usage_fields and isinstance(v, (int, float))}
                 if isinstance(usage, dict) else {},
    }


def record_model_call(record):
    """Called by the existing gateway recorder; keep its caches and tracing intact."""
    event("llm_called", level="warning" if record.get("status") in {"error", "cooldown"} else "info",
          **model_call_summary(record))
