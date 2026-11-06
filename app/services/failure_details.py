"""Customer-safe operational diagnostics. Never return exception/provider bodies."""
from __future__ import annotations

import math
import re


MESSAGES = {
    "output_truncated": ("Model response reached its output limit", "The model stopped before completing its structured response. Partial output was not accepted.", "Start a new processing request to use the current vision budget. If this repeats, choose a model with a larger supported output limit or report the reference below."),
    "context_limit": ("Report region exceeds the model's context limit", "The provider could not fit the document input and requested output into this model's context window.", "Choose a vision model with a larger context window, or use a smaller report. Start a new request after changing the model."),
    "daily_quota": ("Free-model daily quota reached", "OpenRouter reported that the free-model daily allowance is exhausted.", "Wait for the provider's quota reset or choose a model with available quota, then start a new request. Re-uploading the PDF does not reset quota."),
    "rate_limit": ("Model provider rate limit reached", "The provider is temporarily limiting requests. This is an operational failure, not an UNKNOWN factual verdict.", "Wait for the stated retry interval when available, then retry. If the limit persists, choose a model with available quota."),
    "embedding_quota": ("Google embedding quota reached", "The app's Google embedding service has no available quota. Your OpenRouter key does not cover embeddings.", "Ask the administrator to check the Google embedding quota and billing, then start new processing. Successful table reads remain cached."),
    "key_rejected": ("OpenRouter key rejected", "The provider rejected the key or its permissions.", "Open Settings and check your OpenRouter connection. If you replace the key, start a new request using the saved connection."),
    "credits_exhausted": ("OpenRouter credits or spending limit reached", "The provider rejected this request because of an account credit or key spending limit.", "Check credits and the key's spending limit in OpenRouter. Retry after resolving it; changing or replacing the key requires a new request."),
    "model_unavailable": ("Selected model or endpoint unavailable", "The selected model ID or compatible provider endpoint could not be reached.", "Check the exact model ID and any :free suffix in Settings. Check and save a compatible model, then start a new request."),
    "model_incompatible": ("Selected model rejected the request settings", "The endpoint could not accept the required image input, structured JSON response or output allowance.", "Choose an image-capable model with strict JSON Schema support and sufficient output capacity. Check and save the connection, then start a new request."),
    "vision_missing": ("No vision model selected", "Document processing requires a saved image-capable vision model.", "Open Settings, enter the exact vision model ID, check and save the connection, then start new processing with your saved connection."),
    "connection_unavailable": ("Saved OpenRouter connection unavailable", "The saved key was removed, replaced, could not be decrypted, or is missing for this request.", "Check and save your OpenRouter connection in Settings, then start a new request. The app will not substitute an operator key."),
    "key_storage": ("Encrypted key storage unavailable", "The app could not access its customer-key encryption configuration.", "Ask the administrator to restore the existing encryption key and configuration. Do not generate a replacement key for saved connections."),
    "model_configuration": ("Model configuration needs attention", "The model configuration is incomplete or outside the app's supported limits.", "Check and save the model connection in Settings. Ask the administrator to check model limits if validation still fails."),
    "timeout": ("Model request timed out", "The model did not complete within the configured request timeout.", "Retry when the provider is available or choose a faster compatible model. If this repeats, ask the administrator to review the request timeout."),
    "network": ("Model service connection failed", "The app could not establish or maintain a connection to the model service.", "Check service connectivity and retry. The administrator should check outbound network access if this continues."),
    "provider_unavailable": ("Model provider temporarily unavailable", "The provider returned a service failure.", "Wait and retry, or select another compatible model and start a new request."),
    "invalid_response": ("Model returned an unusable structured response", "The response was empty, invalid JSON or did not match the required schema. It was not accepted as evidence.", "Retry once or choose another compatible model. If it repeats, report the reference below; do not treat this as a factual UNKNOWN result."),
    "source_validation": ("Selected source could not be validated", "The selected source reference, metric, reporting period or unit did not match the saved evidence. No numerical verdict was accepted.", "Check the claim's metric and reporting period against the cited page. Create a new review after correcting the claim or updating the app. If it repeats, report the reference and stage below."),
    "refused": ("Model refused or filtered the request", "The provider did not return a completed response for this document.", "Check the selected model's document policy or use a compatible provider. Do not repeatedly retry the same refused request."),
    "pdf_locked": ("PDF is password protected", "The app cannot read a locked PDF.", "Upload an unlocked copy that you are authorized to use."),
    "pdf_limit": ("PDF exceeds processing limits", "The PDF has too many pages, an oversized page or invalid page dimensions.", "Use the upload limits shown in Documents. Split a long PDF or crop oversized pages before uploading."),
    "invalid_pdf": ("PDF could not be read", "The source is empty, damaged or is not a supported readable PDF.", "Open the PDF locally and save a readable copy, then upload that copy."),
    "source_changed": ("Saved PDF version changed", "The saved file no longer matches the version selected for this request.", "Upload the changed PDF as a new source and create a new review. Existing review snapshots remain saved."),
    "table_transcription": ("Table transcription failed validation", "The extracted table has inconsistent rows or column positions. No cells were shifted to force a match.", "Start new processing with a compatible vision model. If it repeats on the same page/table, report the reference and location below."),
    "no_evidence": ("No usable document evidence extracted", "The app did not find readable source text, tables or explicitly labelled chart values to index.", "Check that the PDF is legible and OCR is enabled for scanned pages, then start new processing."),
    "disk_full": ("Processing storage is full", "The app could not save its processing data.", "Ask the administrator to free disk space, check persistent storage and retry after storage is available."),
    "storage": ("Processing storage unavailable", "The app could not access a required processing file or write to its storage.", "Ask the administrator to check the persistent volume, source file and filesystem permissions before retrying."),
    "access_lost": ("Workspace access changed", "This request no longer has access to its workspace or source.", "Sign in again and check workspace membership. A workspace administrator may need to restore access."),
    "worker_timeout": ("Processing time limit reached", "The worker stopped this attempt after the job time limit. Saved completed work is retained.", "Start a new request to reuse completed caches. If this repeats, use a smaller report or ask the administrator to review worker limits."),
    "worker_stopped": ("Processing worker stopped", "The worker was stopped before this request completed.", "Wait for Processing service available, then retry or start a new request. Do not re-upload the same PDF."),
    "worker_interrupted": ("Processing worker interrupted", "The processing process exited before reporting a completed outcome.", "Ask the administrator to check worker health and restart it. Retry only when the processing service is available."),
    "code_changed": ("App updated since this request started", "This saved attempt uses an earlier application version.", "Start new processing with your saved connection, or create a new review, to use the current version. Completed caches and review snapshots remain saved."),
    "settings_changed": ("Model settings changed since this request started", "The saved attempt and current settings do not match.", "Start a new request with your saved connection, or restore the original settings before retrying."),
    "internal_error": ("Processing could not finish", "The app encountered an unexpected operational error. No factual verdict was inferred from it.", "Retry once when the processing service is available. If it repeats, send the error reference, stage and diagnostic code below to the administrator."),
}


def describe_failure(error=None, *, calls=(), stage="", kind="", message="", metadata=None):
    """Classify private detail, returning only controlled text and allowlisted facts."""
    kind = kind if isinstance(kind, str) else ""
    stage = stage if isinstance(stage, str) else ""
    kinds, texts, seen, current = {kind}, [message], set(), error
    info = dict(metadata) if isinstance(metadata, dict) else {}
    for call in reversed(list(calls)):
        if isinstance(call, dict) and call.get("status") in {"error", "cooldown"}:
            info = {**call, **info}
            break
    while current is not None and id(current) not in seen:
        seen.add(id(current)); kinds.add(type(current).__name__); texts.append(str(current))
        if isinstance(getattr(current, "metadata", None), dict):
            info = {**current.metadata, **info}
        if isinstance(getattr(current, "code", None), int):
            info.setdefault("service_error_code", current.code)
        if getattr(current, "errno", None) == 28:
            kinds.add("disk_full")
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    texts.append(info.get("error_message", ""))
    kinds.add(str(info.get("error_type") or info.get("error_kind") or ""))
    text = " ".join(str(value or "") for value in texts).lower()
    status = info.get("service_error_code") or info.get("http_status")
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    finish = info.get("finish_reason") if isinstance(info.get("finish_reason"), str) else None
    location = re.search(r"(?:Reading table (\d+) on page (\d+)|Locating tables on page (\d+))", stage)
    google = "embedding" in stage.lower() or "embedding" in text or "ClientError" in kinds
    if kind in MESSAGES:
        code = kind
    elif finish == "length" or "structured response was truncated" in text or "output reached the token limit" in text:
        code = "output_truncated"
    elif "context" in text and any(s in text for s in ("limit", "exceed", "length")):
        code = "context_limit"
    elif google and (status == 429 or "quota" in text or "resource_exhausted" in text):
        code = "embedding_quota"
    elif "free-models-per-day" in text or re.search(r"daily.{0,30}quota|quota.{0,30}daily", text):
        code = "daily_quota"
    elif status == 429 or any(s in text for s in ("rate limit", "rate-limit", "cooldown")):
        code = "rate_limit"
    elif status in {401, 403}:
        code = "key_rejected"
    elif status == 402:
        code = "credits_exhausted"
    elif "vision model" in text and any(s in text for s in ("choose", "select", "missing")):
        code = "vision_missing"
    elif "ModelConnectionError" in kinds and any(s in text for s in ("storage", "encryption")):
        code = "key_storage"
    elif "ModelConnectionError" in kinds:
        code = "connection_unavailable"
    elif status == 404:
        code = "model_unavailable"
    elif status in {400, 422} or any(s in text for s in ("does not advertise image", "unsupported image", "no listed endpoint")):
        code = "model_incompatible"
    elif "ModelConfigurationError" in kinds:
        code = "model_configuration"
    elif any("timeout" in k.lower() or "timedout" in k.lower() for k in kinds) or "timed out" in text:
        code = "timeout"
    elif any(s in k.lower() for k in kinds for s in ("connecterror", "transporterror", "networkerror")) or "network connection failed" in text:
        code = "network"
    elif isinstance(status, int) and status >= 500:
        code = "provider_unavailable"
    elif finish in {"content_filter", "refusal"} or "refused" in text:
        code = "refused"
    elif "table_vision_validation" in stage or "different widths" in text:
        code = "table_transcription"
    elif "PDFLimitError" in kinds:
        code = "pdf_limit"
    elif "InvalidPDFError" in kinds and "password" in text:
        code = "pdf_locked"
    elif "InvalidPDFError" in kinds:
        code = "invalid_pdf"
    elif "NoUsableEvidenceError" in kinds:
        code = "no_evidence"
    elif "source version changed" in text:
        code = "source_changed"
    elif "disk_full" in kinds:
        code = "disk_full"
    elif kinds & {"FileNotFoundError", "OSError", "PermissionError"}:
        code = "storage" if "PermissionError" not in kinds or "permission denied" in text else "access_lost"
    elif stage == "numeric_selection" and any(s in text for s in (
            "source selection failed validation against saved evidence", "source selection failed local schema validation")):
        code = "source_validation"
    elif any(s in text for s in ("invalid json", "no structured text", "schema validation", "response envelope")) or kinds & {"ValidationError", "JSONDecodeError"}:
        code = "invalid_response"
    else:
        code = "internal_error"
    title, detail, action = MESSAGES[code]
    if code == "source_validation":
        if "chart period and category year labels conflict" in text:
            detail = "The chart has conflicting period and year-category labels. No numerical verdict was accepted."
        elif "chart period does not match" in text:
            detail = "The selected chart value does not identify the claim's reporting period. No numerical verdict was accepted."
        elif "chart year category lacks an explicit time axis" in text:
            detail = "The chart category could not be confirmed as a reporting year. No numerical verdict was accepted."
    result = {"code": code, "title": title, "message": detail, "action": action}
    if location:
        result["page"] = int(location[2] or location[3])
        if location[1]: result["table"] = int(location[1])
    if re.fullmatch(r"[a-z][a-z0-9_]{0,50}", stage): result["stage"] = stage
    elif stage in {"Saving document", "Extracting text", "Analyzing layout", "Reading table images", "Processing visual evidence", "Building evidence", "Creating search index", "Understanding claims", "Claims understood"}:
        result["stage"] = stage
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599: result["http_status"] = status
    model = info.get("requested_model") or info.get("model")
    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", model) and not model.startswith(("sk-", "sb_")) and (not google or "gemini" in model):
        result["model"] = model
    limit = info.get("output_token_limit")
    if not isinstance(limit, int):
        found = re.search(r"max_tokens=(\d+)", text)
        limit = int(found[1]) if found else None
    if isinstance(limit, int) and not isinstance(limit, bool) and 256 <= limit <= 131072: result["output_token_limit"] = limit
    seconds = info.get("retry_after_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and 0 <= seconds <= 86400:
        result["retry_after_seconds"] = math.ceil(seconds)
    request = info.get("request_id") or info.get("provider_request_id")
    if isinstance(request, str) and re.fullmatch(r"gen-[A-Za-z0-9_.-]{1,100}|[a-fA-F0-9-]{8,64}", request): result["provider_request_id"] = request
    return result
