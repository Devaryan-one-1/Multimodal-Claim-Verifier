"""Operational failures are distinct from factual UNKNOWN verdicts."""
from __future__ import annotations

import math
import re


class VerificationFailure(RuntimeError):
    def __init__(self, message: str, *, stage: str, pause: dict | None = None):
        super().__init__(message)
        self.stage = stage
        self.pause = pause


class ParseValidationError(VerificationFailure):
    def __init__(self, message: str):
        super().__init__(message, stage="claim_parser")


def quota_pause(error=None, calls=()) -> dict | None:
    """Classify current failures/call events; never guess a daily reset time."""
    messages = []
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "pause", None):
            return dict(current.pause)
        messages.append(str(current))
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    relevant = [c for c in calls if c.get("status") in {"error", "cooldown"}]
    messages.extend(str(c.get("error_message") or "") for c in relevant)
    detail = " ".join(messages)
    daily = bool(re.search(r"free-models-per-day|per.day|daily.{0,30}quota|quota.{0,30}daily", detail, re.I))
    limited = daily or bool(re.search(
        r"\b429\b|rate.limit|resource[_ ]exhausted|quota.{0,20}(?:exceeded|exhausted|limit)",
        detail, re.I,
    )) or any(c.get("http_status") == 429 or c.get("status") == "cooldown" for c in relevant)
    if not limited:
        return None
    seconds = None
    for event in reversed(relevant):
        value = event.get("retry_after_seconds")
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            seconds = value
            break
    return {
        "kind": "daily_quota" if daily else "rate_limit_or_cooldown",
        "reason": detail[:1800] or "The model service reported a quota or rate limit.",
        "retry_after_seconds": seconds,
        "reset_at": None,
        "action": "Successful predictions were saved. Resume explicitly when quota is available.",
    }
