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


def record_model_call(record):
    """Called by the existing gateway recorder; keep its caches and tracing intact."""
    event("llm_called", level="warning" if record.get("status") in {"error", "cooldown"} else "info",
          **{k: record.get(k) for k in ("provider", "model", "task", "status", "elapsed_ms")})
