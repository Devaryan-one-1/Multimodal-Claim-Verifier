"""Operational failures are distinct from factual UNKNOWN verdicts."""
from __future__ import annotations

import math
import re


class VerificationFailure(RuntimeError):
    def __init__(self, message: str, *, stage: str, pause: dict | None = None):
        super().__init__(message)
        self.stage = stage
        self.pause = pause


PARSE_VALIDATION_MESSAGES = {
    "wording": "The parsed statement changed or invented wording from the submitted claim.",
    "quantity": "A parsed quantity lost its original value, sign, scale or unit.",
    "metric": "The parsed financial metric is missing or does not match the submitted claim.",
    "binding": "A parsed quantity is not attached to its original financial metric or clause.",
    "coverage": "The parsed claims omitted a stated quantity or factual clause.",
    "period": "The parsed claims did not preserve the original reporting dates or periods.",
    "operation": "The parsed calculation does not match the operation stated in the claim.",
    "claim_type": "The parser could not preserve whether the input was an assertion or question.",
    "invalid_structure": "The model's claim structure did not pass local validation.",
}


PARSE_PERIOD_RULES = {
    "quote_not_grounded": "The parser's date quote could not be matched to the submitted dates and reporting qualifiers.",
    "missing_shared_period": "A numerical clause omitted its reporting period and no unambiguous shared period could be recovered.",
    "unstated_year": "A parsed reporting year was not stated in the submitted claim.",
    "conflicting_durations": "One parsed clause mixed different reporting durations; split the period/value statements.",
    "unrelated_start_period": "A single-date value retained an unrelated comparison start year.",
    "multiple_periods": "One value still covers multiple reporting years; each dated value needs a separate target.",
    "identical_comparison_years": "A two-year comparison assigned the same year to both inputs.",
    "missing_year": "An explicit reporting year was omitted from the parsed targets.",
    "comparison_year_mismatch": "The comparison fields did not preserve both stated reporting years.",
}


class ParseValidationError(VerificationFailure):
    def __init__(self, message: str, *, reason=None, rule=None):
        super().__init__(message, stage="claim_parser")
        if isinstance(rule,str) and rule in PARSE_PERIOD_RULES:
            reason="period"
        if reason not in PARSE_VALIDATION_MESSAGES:
            text=message.casefold()
            reason=next((code for code,words in (
                ("period",("year","period","quarter","date")),
                ("quantity",("literal","quantity","currency","sign","scale","unit")),
                ("operation",("operation","margin","ratio","cagr")),
                ("metric",("metric","subject")),
                ("claim_type",("question","unclear","declarative")),
                ("wording",("claim_text","wording","threshold"))) if any(word in text for word in words)),"invalid_structure")
        self.validation_reason=reason
        self.metadata={"validation_reason":reason}
        if isinstance(rule,str) and rule in PARSE_PERIOD_RULES:
            self.metadata["validation_rule"]=rule


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
