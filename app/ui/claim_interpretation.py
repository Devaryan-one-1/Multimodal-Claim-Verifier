"""Customer-readable interpretation of an already saved claim."""

OPERATIONS = {
    "growth": "Percentage change", "absolute_change": "Change in value",
    "percentage_points": "Percentage-point change", "margin": "Profit margin",
    "ratio": "Share / ratio", "cagr": "Compound annual growth",
    "level": "Reported value", "qualitative": "Statement review", "direction": "Direction of change",
}


def interpretation_lines(result):
    pc = result.get("parsed_claim") or {}
    if not isinstance(pc, dict) or not pc or pc.get("parsing_error"):
        return []
    interpretation = pc.get("interpretation") or {}
    lines = []
    for label, value in (
        ("Submitted wording", pc.get("original_input") or result.get("original_claim") or result.get("claim_text")),
        ("This claim", pc.get("claim_text")), ("Company", pc.get("company")),
        ("Metric", pc.get("metric")), ("Compared with / denominator", pc.get("denominator_metric")),
        ("Segment", pc.get("segment")), ("Claimed value", pc.get("claimed_literal")),
        ("Unit", pc.get("unit")),
        ("Operation", OPERATIONS.get(pc.get("operation"), "Not established")),
    ):
        if value is not None and value != "":
            lines.append(f"{label}: {value}")
    term = interpretation.get("metric")
    if term and term != pc.get("metric"):
        lines.append(f"Search terminology: {term}")
    # Source resolution happens after parsing. Prefer final source dates over
    # the earlier interpretation snapshot, which may still say unresolved.
    inferred = (result.get("calculation") or {}).get("inferred_periods") or pc.get("inferred_periods")
    if inferred:
        values = [str(inferred.get(name, {}).get("raw") or inferred.get(name, {}).get("date")
                      or inferred.get(name, {}).get("year") or "Not established") for name in ("old", "new")]
        lines.append("Periods: " + " to ".join(values) + " (resolved from source)")
    elif pc.get("period_inference") and pc.get("end_period"):
        lines.append(f"Periods: {pc.get('start_period')} to {pc['end_period']} (inferred)")
    elif pc.get("end_period"):
        dates = pc.get("period_quote") or " to ".join(str(pc[k]) for k in ("start_period", "end_period") if pc.get(k))
        lines.append(f"Periods: {dates}")
    else:
        lines.append("Periods: Awaiting source resolution or clarification")
    if pc.get("period_type") not in {None, "unspecified"}:
        lines.append("Period type: " + pc["period_type"].replace("_", " "))
    if pc.get("negated"):
        lines.append("Negation: Present in the claim")
    if pc.get("approximate"):
        lines.append("Precision: Approximate / rounded claim")
    return lines


def render_claim_interpretation(st, result):
    with st.expander("How your claim was understood", expanded=False):
        lines = interpretation_lines(result)
        if not lines:
            st.caption("No validated interpretation is available for this saved result.")
        for line in lines:
            st.text(line)
        if lines:
            st.caption("If this interpretation differs from your meaning, edit the claim and submit a new review.")
