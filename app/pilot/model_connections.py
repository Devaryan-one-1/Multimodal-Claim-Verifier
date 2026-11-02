"""Per-user OpenRouter connections. Secrets never enter model/job snapshots."""
from __future__ import annotations

import hmac
import json
import os
import re
import time
import uuid

from pilot import store

BASE_URL = "https://openrouter.ai/api/v1"
MODEL_ID = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]{1,180}\Z")


class ModelConnectionError(ValueError):
    """A safe customer message, never a provider body or a credential."""


def _cipher():
    secret = os.getenv("CUSTOMER_KEY_ENCRYPTION_KEY", "").strip()
    if not secret:
        raise ModelConnectionError("Customer key storage is not configured. Contact the administrator.")
    try:
        from cryptography.fernet import Fernet
        return Fernet(secret.encode("ascii"))
    except (ImportError, ValueError, UnicodeError):
        raise ModelConnectionError("Customer key storage is unavailable. Contact the administrator.") from None


def _decode(row):
    try:
        value = json.loads(_cipher().decrypt(row["secret"].encode("ascii")))
        if value["principal"] != row["principal"] or value["version"] != row["version"]:
            raise ValueError()
        return value["key"]
    except ModelConnectionError:
        raise
    except Exception:
        raise ModelConnectionError("Your saved key could not be opened. Reconnect your OpenRouter key.") from None


def model_config(text_model, vision_model=""):
    text_model, vision_model = text_model.strip(), vision_model.strip()
    if not MODEL_ID.fullmatch(text_model) or (vision_model and not MODEL_ID.fullmatch(vision_model)):
        raise ModelConnectionError("Enter exact OpenRouter model IDs, including their provider prefix.")
    return {
        "provider": "openrouter", "text_model": text_model, "vision_model": vision_model,
        "draft_model": text_model, "allow_fallbacks": False,
        "text_fallbacks": [], "vision_fallbacks": [],
        "timeout_seconds": min(600, max(1, float(os.getenv("CUSTOMER_MODEL_TIMEOUT_SECONDS", "120")))),
        "max_output_tokens": min(131072, max(256, int(os.getenv("CUSTOMER_MAX_OUTPUT_TOKENS", "8192")))),
        "send_temperature": False,
    }


def _get(path, api_key):
    import httpx
    try:
        response = httpx.get(BASE_URL + path, headers={"Authorization": "Bearer " + api_key},
                             timeout=20, follow_redirects=False)
        if response.status_code != 200:
            hint = {401: "Check your API key.", 403: "Check your key permissions and account settings.",
                    402: "Check your account credits and key spending limit.",
                    404: "Check the exact model ID, including its provider prefix and any :free suffix.",
                    429: "Wait for the rate limit to reset, then check again."}.get(response.status_code,
                    "Try again later or check your account settings.")
            raise ModelConnectionError(
                f"OpenRouter connection check returned HTTP {response.status_code}. {hint}")
        data = response.json().get("data")
        if not isinstance(data, dict):
            raise ModelConnectionError("OpenRouter returned an invalid connection response. Try again later.")
        return data
    except ModelConnectionError:
        raise
    except Exception:
        raise ModelConnectionError("OpenRouter could not be reached. Try the connection check again later.") from None


def validate(api_key, models):
    if not isinstance(api_key, str) or not api_key.isascii() or not 12 <= len(api_key) <= 512 or re.search(r"\s", api_key):
        raise ModelConnectionError("Enter a valid OpenRouter API key without whitespace.")
    _get("/key", api_key)
    # Check provider endpoints, not model-page marketing or JSON-mode support alone.
    checked = {}
    for model, vision in ((models["text_model"], False), (models["vision_model"], True)):
        if not model:
            continue
        if model not in checked:
            checked[model] = _get(f"/models/{model}/endpoints", api_key)
        data = checked[model]
        architecture = data.get("architecture") or {}
        if not isinstance(architecture, dict):
            raise ModelConnectionError("OpenRouter returned invalid model capabilities. Try again later.")
        if vision and "image" not in (architecture.get("input_modalities") or []):
            raise ModelConnectionError(f"Vision model {model} does not advertise image input. Choose an image-capable model.")
        endpoints = data.get("endpoints") or []
        if not isinstance(endpoints, list):
            raise ModelConnectionError("OpenRouter returned invalid endpoint capabilities. Try again later.")
        compatible = []
        for item in endpoints:
            if not isinstance(item, dict):
                continue
            parameters, limit = item.get("supported_parameters") or [], item.get("max_completion_tokens")
            if (isinstance(parameters, list) and
                {"structured_outputs", "response_format", "max_tokens"}.issubset(parameters) and
                (limit is None or isinstance(limit, (int, float)) and limit >= models["max_output_tokens"])):
                compatible.append(item)
        if not compatible:
            raise ModelConnectionError(f"Model {model} has no listed endpoint supporting strict JSON Schema and the output limit.")


def metadata(access):
    with store.db() as c:
        store.authorize(c, access)
        row = c.execute("SELECT version,models,updated FROM model_connections WHERE principal=?", (access.principal,)).fetchone()
    return {"version": row["version"], "models": json.loads(row["models"]), "updated": row["updated"]} if row else None


def save(access, api_key, text_model, vision_model=""):
    cipher = _cipher()
    models = model_config(text_model, vision_model)
    with store.db() as c:
        store.authorize(c, access)
        previous = c.execute("SELECT * FROM model_connections WHERE principal=?", (access.principal,)).fetchone()
        previous = dict(previous) if previous else None
    api_key = api_key.strip()
    previous_key = None
    if previous:
        try:
            previous_key = _decode(previous)
        except ModelConnectionError:
            if not api_key:
                raise
    api_key = api_key or previous_key
    validate(api_key, models)
    version = previous["version"] if previous_key and hmac.compare_digest(api_key, previous_key) else uuid.uuid4().hex
    secret = cipher.encrypt(json.dumps({"key": api_key, "principal": access.principal,
                                       "version": version}).encode()).decode("ascii")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        store.authorize(c, access)
        current = c.execute("SELECT version,updated FROM model_connections WHERE principal=?", (access.principal,)).fetchone()
        # Do not overwrite a remove/replace made while network validation was running.
        if ((current["version"], current["updated"]) if current else None) != (
            (previous["version"], previous["updated"]) if previous else None
        ):
            raise ModelConnectionError("Your connection changed in another tab. Refresh and try again.")
        c.execute("INSERT INTO model_connections(principal,version,secret,models,updated) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(principal) DO UPDATE SET version=excluded.version,secret=excluded.secret,models=excluded.models,updated=excluded.updated",
                  (access.principal, version, secret, json.dumps(models), time.time()))


def remove(access):
    with store.db() as c:
        store.authorize(c, access)
        c.execute("DELETE FROM model_connections WHERE principal=?", (access.principal,))


def snapshot(c, access, *, vision=False):
    store.authorize(c, access)
    row = c.execute("SELECT version,models FROM model_connections WHERE principal=?", (access.principal,)).fetchone()
    if not row:
        raise ModelConnectionError("Connect your OpenRouter key and choose models before submitting work.")
    models = json.loads(row["models"])
    if vision and not models.get("vision_model"):
        raise ModelConnectionError("Choose a compatible vision model before processing a document.")
    return {"connection_version": row["version"], "models": models}


def credentials_for_job(access, payload):
    binding = payload.get("openrouter") or {}
    if not binding.get("connection_version") or not isinstance(binding.get("models"), dict):
        raise ModelConnectionError("This request predates your OpenRouter connection. Connect your key and submit a new request.")
    with store.db() as c:
        store.authorize(c, access)
        row = c.execute("SELECT * FROM model_connections WHERE principal=? AND version=?",
                        (access.principal, binding["connection_version"])).fetchone()
        if not row:
            raise ModelConnectionError("Your OpenRouter connection was removed or replaced. Reconnect and submit a new request.")
        key = _decode(row)
    selected = binding["models"]
    # Reconstruct from allowlisted fields; never inherit server providers/fallbacks.
    config = model_config(selected.get("text_model", ""), selected.get("vision_model", ""))
    for name in ("timeout_seconds", "max_output_tokens"):
        value = selected.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= config[name]:
            raise ModelConnectionError("The request's model limits changed. Submit a new request.")
        if name == "max_output_tokens" and not isinstance(value, int):
            raise ModelConnectionError("The request's output limit is invalid. Submit a new request.")
        config[name] = value
    if config != selected:
        raise ModelConnectionError("The request's model configuration is invalid. Submit a new request.")
    return key, config
