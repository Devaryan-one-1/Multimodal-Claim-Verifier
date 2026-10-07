import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "retrieval"))
sys.path.insert(0, os.path.dirname(__file__))  # this file's own folder (verification)

from claim_parser import parse_claims
from vector_store import search
from reranker import rerank
from number_parser import parse_number
import financial_calculations as fc
# from comparison import compare, INSUFFICIENT
from comparison import compare, UNKNOWN

TOP_RETRIEVE = 20
KEEP = 5

def verify_claim(claim_text, document_id):
    """Verify one free-text claim (may be compound) against a document."""
    parsed_claims = parse_claims(claim_text)
    results = []
    for pc in parsed_claims:
        results.append(_verify_one(pc, document_id))
    return results


def _verify_one(pc, document_id):
    audit = {
        "claim_text": pc.get("claim_text"),
        "parsed_claim": pc,
        "calculation": None,
        "comparison": None,
        "evidence_source": None,
        "verdict": None,
        "notes": [],
    }

    # questions/unclear claims aren't verifiable assertions
    if pc.get("claim_type") != "assertion":
        audit["verdict"] = UNKNOWN
        audit["notes"].append(f"claim_type is '{pc.get('claim_type')}', not a verifiable assertion")
        return audit

    # 1. retrieve + rerank evidence for this claim
    query = pc.get("claim_text") or ""
    hits = search(query, top_k=TOP_RETRIEVE, document_id=document_id)
    hits = rerank(query, hits, keep=KEEP, parsed_claim=pc)
    if not hits:
        audit["verdict"] = UNKNOWN
        audit["notes"].append("no evidence retrieved")
        return audit

    # 2. extract the two actual values (deterministic -> LLM fallback -> text)
    old_val, new_val, source, extract_notes = _extract_actuals(pc, hits, document_id)
    audit["notes"].extend(extract_notes)
    audit["evidence_source"] = source

    if old_val is None or new_val is None:
        audit["verdict"] = UNKNOWN
        audit["notes"].append("could not extract both values from evidence")
        return audit

    # 3. pick + run the calculation
    calc = _run_calculation(pc, old_val, new_val)
    audit["calculation"] = calc
    if calc is None or calc.get("value") is None:
        audit["verdict"] = UNKNOWN
        audit["notes"].append("calculation undefined")
        return audit

    # 4. compare claimed vs computed
    claimed = pc.get("claimed_change")
    cmp = compare(claimed, calc["value"])
    audit["comparison"] = cmp
    audit["verdict"] = cmp["verdict"]
    return audit


# ---------------- calculation selection ----------------
def _run_calculation(pc, old_val, new_val):
    """Pick the calculation from claim fields, wording as backup."""
    metric = (pc.get("metric") or "").lower()
    change_type = (pc.get("change_type") or "").lower()
    unit = (pc.get("unit") or "").lower()
    wording = (pc.get("claim_text") or "").lower()

    # margin claim
    if "margin" in metric or "margin" in wording:
        # margin change between two margin figures -> analyze_change (pp vs relative)
        return fc.analyze_change(old_val, new_val, claim_wording=wording)

    # CAGR
    if "cagr" in wording or "compound annual" in wording:
        years = _year_span(pc)
        return fc.cagr(old_val, new_val, years) if years else None

    # dollar / absolute change
    if unit in ("dollars", "absolute") and change_type != "growth":
        return fc.absolute_change(old_val, new_val)

    # default: percentage growth (covers 'grew X%', revenue growth, etc.)
    return fc.percentage_growth(old_val, new_val)


def _year_span(pc):
    a, b = pc.get("start_period"), pc.get("end_period")
    if isinstance(a, int) and isinstance(b, int) and b > a:
        return b - a
    return None


# ---------------- actual-value extraction ----------------
def _extract_actuals(pc, hits, document_id):
    """
    Deterministic table match first; LLM fallback if unmatched/ambiguous;
    finally try text evidence. Returns (old, new, source_dict, notes).
    """
    notes = []

    # try each table hit deterministically
    table_hits = [h for h in hits if h.get("type") == "table"]
    for h in table_hits:
        old, new, why = _match_table_cells(pc, h)
        if old is not None and new is not None:
            return old, new, {"kind": "table", "chunk_id": h.get("chunk_id"),
                              "page": h.get("page"), "table_id": h.get("table_id")}, \
                   notes + [f"matched table cells deterministically"]
        notes.append(f"table {h.get('table_id')}: {why}")

    # LLM fallback on the top table (ambiguous / unmatched)
    if table_hits:
        old, new, why = _llm_extract(pc, table_hits[0])
        if old is not None and new is not None:
            return old, new, {"kind": "table_llm", "chunk_id": table_hits[0].get("chunk_id"),
                              "page": table_hits[0].get("page")}, notes + ["used LLM fallback on table"]
        notes.append(f"LLM table extract failed: {why}")

    # text/paragraph fallback
    text_hits = [h for h in hits if h.get("type") in ("text", "paragraph")]
    if text_hits:
        old, new, why = _llm_extract_from_text(pc, text_hits[:3])
        if old is not None and new is not None:
            return old, new, {"kind": "text_llm", "chunk_id": text_hits[0].get("chunk_id"),
                              "page": text_hits[0].get("page")}, notes + ["extracted from text evidence"]
        notes.append(f"text extract failed: {why}")

    return None, None, None, notes


# def _match_table_cells(pc, hit):
#     """
#     Deterministic: find the two period columns and the metric/segment row,
#     return the intersecting cells. Returns (old, new, reason_if_failed).
#     """
#     headers = hit.get("headers") or []
#     rows = hit.get("rows") or []
#     if not headers or not rows:
#         return None, None, "no headers/rows"

#     start, end = pc.get("start_period"), pc.get("end_period")
#     if start is None or end is None:
#         return None, None, "claim has no periods to match columns"

#     # find the column index for each period by matching the year in the header text
#     col_old = _find_col(headers, start)
#     col_new = _find_col(headers, end)
#     if col_old is None or col_new is None:
#         return None, None, f"could not find columns for {start}/{end}"

#     # find the row whose label matches the metric/segment
#     target = " ".join(str(x) for x in (pc.get("segment"), pc.get("metric")) if x).lower().strip()
#     matches = []
#     for r in rows:
#         if not r:
#             continue
#         label = str(r[0]).lower()
#         if target and target in label:
#             matches.append(r)
#         elif pc.get("metric") and str(pc.get("metric")).lower() in label:
#             matches.append(r)

#     if len(matches) == 0:
#         return None, None, f"no row matching '{target}'"
#     if len(matches) > 1:
#         return None, None, f"ambiguous: {len(matches)} rows match '{target}'"

#     row = matches[0]
#     old_raw = row[col_old] if col_old < len(row) else None
#     new_raw = row[col_new] if col_new < len(row) else None

#     old, _ = parse_number(old_raw)
#     new, _ = parse_number(new_raw)
#     if old is None or new is None:
#         return None, None, f"cell unparseable ({old_raw!r}, {new_raw!r})"
#     return old["value"], new["value"], None


# def _find_col(headers, year):
#     """Index of the header cell containing the given year, or None."""
#     for i, h in enumerate(headers):
#         if str(year) in str(h):
#             return i
#     return None


def _match_table_cells(pc, hit):
    """
    Deterministic cell match using the recovered column_map.
    Resolves year + period to the right column (handles duplicate years,
    e.g. Q3-2026 vs YTD-2026), finds the metric/segment row, returns the
    two intersecting cells. Returns (old, new, reason_if_failed).
    """
    rows = hit.get("rows") or []
    col_map = hit.get("column_map") or []
    if not rows:
        return None, None, "no rows"
    if not col_map:
        return None, None, "no column_map (table needs LLM header pass)"

    start, end = pc.get("start_period"), pc.get("end_period")
    start, end = _to_int(start), _to_int(end)
    if start is None or end is None:
        return None, None, "claim has no usable periods"

    # which period does the claim mean? (from wording; default: any)
    wanted_period = _wanted_period(pc)

    col_old = _find_col_by_map(col_map, start, wanted_period)
    col_new = _find_col_by_map(col_map, end, wanted_period)
    if col_old is None or col_new is None:
        return None, None, (f"no columns for {start}/{end}"
                            + (f" ({wanted_period})" if wanted_period else ""))

    # find the row whose label matches the metric/segment
    target = " ".join(str(x) for x in (pc.get("segment"), pc.get("metric")) if x).lower().strip()
    metric = (pc.get("metric") or "").lower().strip()

    matches = []
    for r in rows:
        if not r:
            continue
        label = str(r[0]).lower().strip().rstrip(":")
        if target and target in label:
            matches.append(r)
        elif metric and metric in label:
            matches.append(r)

    if len(matches) == 0:
        return None, None, f"no row matching '{target or metric}'"
    if len(matches) > 1:
        # prefer an exact label match to disambiguate (e.g. 'total net sales')
        exact = [r for r in matches if str(r[0]).lower().strip().rstrip(":") in (target, metric)]
        if len(exact) == 1:
            matches = exact
        else:
            return None, None, f"ambiguous: {len(matches)} rows match '{target or metric}'"

    row = matches[0]
    old_raw = row[col_old] if col_old < len(row) else None
    new_raw = row[col_new] if col_new < len(row) else None

    old, _ = parse_number(old_raw)
    new, _ = parse_number(new_raw)
    if old is None or new is None:
        return None, None, f"cell unparseable ({old_raw!r}, {new_raw!r})"
    return old["value"], new["value"], None


def _find_col_by_map(col_map, year, wanted_period):
    """
    Return the row-index of the cell for (year, period). col_map entries have
    'col' as 1-based (col 0 is the row label), so we return it directly as the
    index into the row list. If wanted_period is None, take the first year match.
    """
    # first try to match both year and period
    if wanted_period:
        for cm in col_map:
            if cm["year"] == year and cm.get("period") == wanted_period:
                return cm["col"]
    # fall back to year-only (first match) — covers balance sheets (period=None)
    for cm in col_map:
        if cm["year"] == year:
            return cm["col"]
    return None


def _wanted_period(pc):
    """Infer which period the claim means from its wording. None = unspecified."""
    w = (pc.get("claim_text") or "").lower()
    if "nine month" in w or "year to date" in w or "ytd" in w or "year-to-date" in w:
        return "nine_months"
    if "three month" in w or "quarter" in w or "q1" in w or "q2" in w or "q3" in w or "q4" in w:
        return "three_months"
    if "six month" in w:
        return "six_months"
    if "annual" in w or "full year" in w or "fiscal year" in w or "year ended" in w:
        return "year"
    return None


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
# ---------------- LLM fallbacks ----------------
def _llm_extract(pc, hit):
    """Ask Gemini to pull the two values from a table's structured rows."""
    return _llm_extract_from_text(
        pc,
        [{"text": _table_to_text(hit)}],
    )


def _table_to_text(hit):
    headers = hit.get("headers") or []
    rows = hit.get("rows") or []
    lines = [" | ".join(str(x) for x in headers)]
    for r in rows:
        lines.append(" | ".join(str(x) for x in r))
    return "\n".join(lines)


def _llm_extract_from_text(pc, hits):
    """
    Last-resort extraction of the two comparison values via Gemini.
    Returns (old, new, reason_if_failed). Fails soft to (None, None, reason).
    """
    try:
        from google import genai
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    except Exception as e:
        return None, None, f"LLM unavailable: {type(e).__name__}"

    evidence = "\n\n".join(h.get("text", "") for h in hits)
    prompt = (
        "From the evidence, extract the two numeric values needed to check this claim. "
        "Return ONLY JSON: {\"old\": <number or null>, \"new\": <number or null>}. "
        "Use full numbers (expand $10B to 10000000000).\n\n"
        f"Claim: metric={pc.get('metric')}, segment={pc.get('segment')}, "
        f"start={pc.get('start_period')}, end={pc.get('end_period')}\n\n"
        f"Evidence:\n{evidence}"
    )
    try:
        resp = client.models.generate_content(model=fc_desc_model(), contents=prompt)
        import json as _json
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("```"))
        data = _json.loads(raw)
        old = data.get("old")
        new = data.get("new")
        if old is None or new is None:
            return None, None, "LLM returned null value(s)"
        return float(old), float(new), None
    except Exception as e:
        return None, None, f"LLM extract error: {type(e).__name__}"


def fc_desc_model():
    # reuse the claim parser's model name
    from claim_parser import DESC_MODEL
    return DESC_MODEL


if __name__ == "__main__":
    import sys, json
    claim = sys.argv[1] if len(sys.argv) > 1 else "Revenue grew 25% in 2025"
    doc = sys.argv[2] if len(sys.argv) > 2 else "Apple_Financial_10_K"
    results = verify_claim(claim, doc)
    for r in results:
        print(f"\n=== {r['claim_text']} -> {r['verdict']} ===")
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))