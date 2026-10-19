"""Text/vision routing only. This module never creates or searches embeddings."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import TypeVar

import httpx
from google import genai
from google.genai import types
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from services.runtime import write

SCHEMA = TypeVar("SCHEMA", bound=BaseModel)
BASE_URL = "https://openrouter.ai/api/v1"
_settings: ContextVar[ModelConfig | None] = ContextVar("model_settings", default=None)
_calls: ContextVar[list | None] = ContextVar("model_calls", default=None)
_last: ContextVar[dict | None] = ContextVar("last_model_call", default=None)
_cooldowns: dict[tuple, float] = {}
_cooldown_lock = threading.Lock()
logger = logging.getLogger(__name__)


class ModelServiceError(RuntimeError):
    """An operational failure, never evidence for an UNKNOWN verdict."""


class ModelConfigurationError(ModelServiceError):
    pass


def _boolean(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(value):
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    text_model: str
    vision_model: str
    draft_model: str
    text_fallbacks: tuple[str, ...] = ()
    vision_fallbacks: tuple[str, ...] = ()
    allow_fallbacks: bool = False
    timeout_seconds: float = 120.0
    max_output_tokens: int = 8192
    send_temperature: bool = False

    @classmethod
    def from_env(cls):
        provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        if provider == "openrouter":
            text = os.getenv("OPENROUTER_TEXT_MODEL", "").strip()
            vision = os.getenv("OPENROUTER_VISION_MODEL", "").strip()
            draft = os.getenv("OPENROUTER_DRAFT_MODEL", "").strip() or text
        else:
            text = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
            vision = os.getenv("GEMINI_VISION_MODEL", "").strip() or text
            draft = os.getenv("GEMINI_EVALUATION_MODEL", "").strip() or text
        return cls(
            provider=provider, text_model=text, vision_model=vision, draft_model=draft,
            text_fallbacks=_list(os.getenv("LLM_TEXT_FALLBACK_MODELS", "")),
            vision_fallbacks=_list(os.getenv("LLM_VISION_FALLBACK_MODELS", "")),
            allow_fallbacks=_boolean("LLM_ALLOW_FALLBACKS"),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192")),
            send_temperature=_boolean("OPENROUTER_SEND_TEMPERATURE"),
        )


def config() -> ModelConfig:
    return _settings.get() or ModelConfig.from_env()


def configure(value: ModelConfig | dict) -> None:
    if isinstance(value, dict):
        value = ModelConfig(**value)
    _validate_config(value)
    _settings.set(value)


def _validate_config(value):
    if value.provider not in {"gemini", "openrouter"}:
        raise ModelConfigurationError("Model service configuration: choose gemini or openrouter.")
    if not math.isfinite(value.timeout_seconds) or not 1 <= value.timeout_seconds <= 600:
        raise ModelConfigurationError("Model service configuration: timeout must be 1–600 seconds.")
    if not 256 <= value.max_output_tokens <= 131072:
        raise ModelConfigurationError("Model service configuration: invalid maximum output tokens.")


def model_name(task="text") -> str:
    settings = config()
    return settings.vision_model if task == "vision" else settings.draft_model if task == "draft" else settings.text_model


def configuration_snapshot() -> dict:
    # No credentials, prompts, document text or circuit-breaker state.
    value = asdict(config())
    value.update(gateway_version=1, schema_policy="strict JSON Schema plus local validation",
                 openrouter_base_url=BASE_URL)
    return value


def last_call() -> dict:
    return dict(_last.get() or {})


def api_key_configured(provider=None) -> bool:
    provider = provider or config().provider
    return bool(os.getenv("OPENROUTER_API_KEY")) if provider == "openrouter" else bool(
        os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _key(settings):
    key = (os.getenv("OPENROUTER_API_KEY") if settings.provider == "openrouter" else
           os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    if not key:
        name = "OPENROUTER_API_KEY" if settings.provider == "openrouter" else "GOOGLE_API_KEY"
        raise ModelConfigurationError(f"Model service configuration: set {name}.")
    return key


def list_openrouter_models() -> list[dict]:
    """Only called by the explicit Refresh models button; no generation request."""
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"} if os.getenv("OPENROUTER_API_KEY") else {}
    try:
        response = httpx.get(f"{BASE_URL}/models", headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()["data"]
        if not isinstance(data, list):
            raise ValueError("Invalid catalog")
        return [{"id": item["id"], "name": item.get("name", item["id"]),
                 "input_modalities": item.get("architecture", {}).get("input_modalities", []),
                 "supported_parameters": item.get("supported_parameters", []),
                 "context_length": item.get("context_length"), "pricing": item.get("pricing", {})}
                for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
    except Exception as exc:
        raise ModelServiceError("Model service catalog could not load. Check the connection/key and try again.") from exc


def _strict_schema(value):
    """Close Pydantic object shapes and require their declared properties."""
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item) for key, item in value.items() if key != "default"}
    if result.get("type") == "object" or "properties" in result:
        if isinstance(result.get("additionalProperties"), dict) or result.get("additionalProperties") is True:
            raise ModelConfigurationError("Model service schema: free-form dictionaries need an explicit object schema.")
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _record(event):
    _last.set(dict(event))
    calls = _calls.get()
    if calls is not None:
        calls.append(dict(event))
    logger.info("model_call %s", json.dumps(event, ensure_ascii=False, default=str))


def _retry_seconds(exc):
    response = getattr(exc, "response", None)
    try:
        seconds = float(response.headers.get("retry-after", ""))
        if math.isfinite(seconds) and seconds >= 0:
            return min(seconds + 2, 3600)
    except (AttributeError, ValueError, TypeError):
        pass
    return 65.0


def _transient(exc):
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code is None and getattr(exc, "response", None) is not None:
        code = exc.response.status_code
    return code in {408, 429, 500, 502, 503, 504}


def _openrouter(settings, key, model, prompt, system, schema, images, temperature):
    if "/" not in model or re.search(r"\s", model):
        raise ModelConfigurationError("Model service configuration: use the exact OpenRouter model ID, including its provider prefix.")
    content = [{"type": "text", "text": prompt}]
    for path in images:
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ModelConfigurationError("Model service configuration: unsupported image format.")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    payload = {
        "model": model, "messages": messages, "stream": False,
        "max_tokens": settings.max_output_tokens,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "financial_document_response", "strict": True, "schema": schema}},
        "provider": {"require_parameters": True, "allow_fallbacks": settings.allow_fallbacks},
    }
    # Some reasoning models do not accept temperature. Omit unless explicitly enabled.
    if settings.send_temperature:
        payload["temperature"] = temperature
    response = httpx.post(
        f"{BASE_URL}/chat/completions", json=payload,
        headers={"Authorization": f"Bearer {key}", "X-Title": "Financial Claim Verifier"},
        timeout=httpx.Timeout(settings.timeout_seconds, connect=min(15, settings.timeout_seconds)),
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        # OpenRouter can report a provider failure inside an HTTP 200 response.
        error = body["error"]
        code = error.get("code", 502) if isinstance(error, dict) else 502
        failure = ModelServiceError(f"Model service upstream error ({code}).")
        failure.code = int(code) if str(code).isdigit() else 502
        raise failure
    choice = body.get("choices", [{}])[0]
    if choice.get("finish_reason") not in {"stop"}:
        raise ModelServiceError("Model service output incomplete or refused; no verdict was accepted. Check the output-token limit and selected model.")
    text = choice.get("message", {}).get("content")
    if not isinstance(text, str) or not text.strip():
        raise ModelServiceError("Model service returned no structured text.")
    return json.loads(text), {
        "actual_model": body.get("model", model), "upstream_provider": body.get("provider"),
        "request_id": body.get("id"), "usage": body.get("usage", {}),
    }


def _gemini(settings, key, model, prompt, system, schema, images, temperature):
    contents = [prompt]
    for path in images:
        path = Path(path)
        contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mimetypes.guess_type(path.name)[0] or "image/png"))
    with genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(settings.timeout_seconds * 1000))) as api:
        response = api.models.generate_content(
            model=model, contents=contents,
            config=types.GenerateContentConfig(
                temperature=temperature, system_instruction=system or None,
                response_mime_type="application/json", response_schema=schema,
                max_output_tokens=settings.max_output_tokens,
            ),
        )
    candidate = response.candidates[0] if response.candidates else None
    finish = getattr(getattr(candidate, "finish_reason", None), "value", None) or str(getattr(candidate, "finish_reason", ""))
    if finish not in {"STOP", "FinishReason.STOP"}:
        raise ModelServiceError("Model service output incomplete or refused; no verdict was accepted.")
    text = response.text
    if not text:
        raise ModelServiceError("Model service returned no structured text.")
    usage = getattr(response, "usage_metadata", None)
    return json.loads(text), {
        "actual_model": getattr(response, "model_version", None) or model,
        "upstream_provider": "google", "request_id": getattr(response, "response_id", None),
        "usage": usage.model_dump(mode="json") if hasattr(usage, "model_dump") else {},
    }


def structured(schema: type[SCHEMA], prompt: str, *, system="", model=None,
               task="text", images=(), temperature=0.0) -> SCHEMA:
    settings = config()
    _validate_config(settings)
    key = _key(settings)
    primary = model or model_name(task)
    if not primary:
        raise ModelConfigurationError(f"Model service configuration: choose a {task} model in the sidebar.")
    raw_schema = copy.deepcopy(schema.model_json_schema())
    request_schema = _strict_schema(raw_schema) if settings.provider == "openrouter" else raw_schema
    validator = Draft202012Validator(request_schema)
    backups = settings.vision_fallbacks if task == "vision" else settings.text_fallbacks
    models = tuple(dict.fromkeys((primary, *(backups if settings.allow_fallbacks else ()))))
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16]
    for model_index, chosen in enumerate(models):
        circuit = (settings.provider, fingerprint, chosen)
        with _cooldown_lock:
            wait = _cooldowns.get(circuit, 0) - time.monotonic()
        if wait > 0:
            _record({"time": datetime.now(timezone.utc).isoformat(), "task": task,
                     "gateway_provider": settings.provider, "requested_model": chosen,
                     "actual_model": None, "status": "cooldown", "retry_after_seconds": round(wait, 1)})
            if model_index < len(models) - 1:
                continue
            raise ModelServiceError(f"Model service rate limit cooldown: retry in {math.ceil(wait)} seconds, or select another model.")
        for attempt in range(2):
            started = time.perf_counter()
            event = {"time": datetime.now(timezone.utc).isoformat(), "task": task,
                     "gateway_provider": settings.provider, "requested_model": chosen,
                     "actual_model": None, "attempt": attempt + 1, "fallback": model_index > 0}
            try:
                call = _openrouter if settings.provider == "openrouter" else _gemini
                value, metadata = call(settings, key, chosen, prompt, system, request_schema, images, temperature)
                event.update(metadata)
                validator.validate(value)
                parsed = schema.model_validate(value)
                event.update(status="ok", duration_seconds=round(time.perf_counter() - started, 3))
                _record(event)
                return parsed
            except Exception as exc:
                transient = _transient(exc)
                code = getattr(exc, "code", None)
                if getattr(exc, "response", None) is not None:
                    code = exc.response.status_code
                event.update(status="error", error_type=type(exc).__name__, http_status=code,
                             duration_seconds=round(time.perf_counter() - started, 3))
                _record(event)
                if transient and code == 429:
                    with _cooldown_lock:
                        _cooldowns[circuit] = time.monotonic() + _retry_seconds(exc)
                if transient and model_index < len(models) - 1:
                    break
                if transient and code != 429 and attempt == 0:
                    time.sleep(2)
                    continue
                if code == 429:
                    raise ModelServiceError("Model service rate limit reached. Saved work is unchanged; wait or select another reasoning model.") from exc
                if isinstance(exc, ModelConfigurationError):
                    raise
                if code in {401, 402, 403}:
                    raise ModelServiceError(f"Model service HTTP {code}: check API credentials, credits and model access.") from exc
                raise ModelServiceError(
                    f"Model service failed for {chosen} ({type(exc).__name__}"
                    + (f", HTTP {code}" if code else "")
                    + "). Check model capability, schema support, context/output limits and connectivity."
                ) from exc
    raise ModelServiceError("Model service unavailable: all configured models failed or are cooling down.")


@contextmanager
def capture_calls():
    existing = _calls.get()
    calls = existing if existing is not None else []
    start = len(calls)
    token = _calls.set(calls)
    try:
        yield calls, start
    finally:
        _calls.reset(token)


def trace_verification(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with capture_calls() as (calls, start):
            result = function(*args, **kwargs)
            for item in result:
                item["model_configuration"] = configuration_snapshot()
                item["model_calls"] = copy.deepcopy(calls[start:])
                item["model_calls_scope"] = "entire submitted claim including parsing and all subclaims"
            return result
    return wrapped


def controlled_evaluation(function):
    """Evaluation pins model choice; cached predictions remain model/config specific."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _settings.set(replace(config(), allow_fallbacks=False))
        try:
            with capture_calls() as (calls, start):
                report = function(*args, **kwargs)
                report["model_calls"] = copy.deepcopy(calls[start:])
                report.setdefault("configuration", {})["model_routing"] = configuration_snapshot()
                report["automatic_model_fallbacks"] = False
                if report.get("report_path"):
                    write(Path(report["report_path"]), report)
                return report
        finally:
            _settings.reset(token)
    return wrapped


def trace_extraction(kind):
    """Record actual vision models in new, isolated extraction caches."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with capture_calls() as (calls, start):
                result = function(*args, **kwargs)
                if not isinstance(result, dict):
                    return result
                if result.get("status") in {"cached", "skipped"}:
                    return result
                result["model_configuration"] = configuration_snapshot()
                result["model_calls"] = copy.deepcopy(calls[start:])
                successes = [event for event in calls[start:] if event.get("status") == "ok"]
                result["actual_models"] = list(dict.fromkeys(event["actual_model"] for event in successes))
                if successes:
                    result["gateway_provider"] = successes[-1]["gateway_provider"]
                if isinstance(result.get("evidence"), dict):
                    result["evidence"]["model_configuration"] = result["model_configuration"]
                    result["evidence"]["model_calls"] = result["model_calls"]
                document = result.get("document_id")
                filename, category = {"ocr": ("ocr.json", "pages"),
                                      "charts": ("charts.json", "pages"),
                                      "chart_analysis": ("chart_analysis.json", "charts")}[kind]
                record_id = result.get("chart_id") if kind == "chart_analysis" else result.get("page_number")
                if document and record_id is not None:
                    path = cache_file(document, filename)
                    if path.is_file():
                        saved = json.loads(path.read_text(encoding="utf-8"))
                        if str(record_id) in saved.get(category, {}):
                            saved[category][str(record_id)] = result
                            write(path, saved)
                return result
        return wrapped
    return decorate


def cache_file(document_id, filename, *, task="vision") -> Path:
    """New extraction caches cannot overwrite reviewed legacy source snapshots."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", document_id) or document_id in {".", ".."}:
        raise ValueError("Invalid document ID")
    snapshot = configuration_snapshot()
    visual = {key: snapshot[key] for key in (
        "provider", "vision_model", "vision_fallbacks", "allow_fallbacks", "timeout_seconds",
        "max_output_tokens", "send_temperature", "gateway_version", "schema_policy",
    )}
    signature = hashlib.sha256(json.dumps(visual, sort_keys=True).encode()).hexdigest()[:20]
    return Path("data/model_cache") / document_id / signature / Path(filename).name
