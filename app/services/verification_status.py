"""Execution state for UI presentation, separate from factual verdicts."""
from __future__ import annotations


def _paused(result: dict) -> bool:
    parsed = result.get("parsed_claim")
    parsed = parsed if isinstance(parsed, dict) else {}
    return (result.get("status") == "paused"
            or bool(result.get("pause")) or bool(parsed.get("pause")))


def execution_failed(result: dict) -> bool:
    """A completed UNKNOWN is factual; provider/parser failures are not."""
    parsed = result.get("parsed_claim")
    parsed = parsed if isinstance(parsed, dict) else {}
    return (result.get("status") in {"error", "failed", "paused"}
            or bool(result.get("error")) or bool(result.get("parsing_error"))
            or bool(parsed.get("parsing_error")) or _paused(result))


def verification_state(results: list[dict]) -> str:
    """Describe execution without aggregating any unfinished factual verdicts."""
    if not results:
        return "failed"
    unfinished = [result for result in results if execution_failed(result)]
    if not unfinished:
        return "complete"
    if len(unfinished) < len(results):
        return "incomplete"
    return "paused" if any(_paused(result) for result in unfinished) else "failed"
