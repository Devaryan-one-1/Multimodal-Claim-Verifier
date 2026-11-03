from __future__ import annotations

import csv
import html
import io
import json
from decimal import Decimal, InvalidOperation
from pilot.store import decisions, get_job
from services.verification_status import execution_failed

FIELDS = ["review_id", "review_name", "run_status", "atom_index", "execution_status", "claim", "verdict",
          "explanation", "claimed", "actual", "unit", "signed_difference", "difference_unit", "comparison_operator", "tolerance",
          "formula", "source_values", "normalized_values", "input_metadata", "source_addresses", "reporting_period",
          "citations", "source_versions", "decision", "review_note", "reviewer", "reviewed_at"]


def execution_status(item):
    if execution_failed(item):
        return "paused" if item.get("status") == "paused" or item.get("pause") else "failed"
    if item.get("status") in {"queued", "running"}:
        return item["status"]
    return "complete" if item.get("verdict") else "incomplete"


def _signed(value):
    try:
        number = Decimal(str(value).replace(",", ""))
        return format(number, "+") if number.is_finite() else ""
    except InvalidOperation:
        return ""


def safe_cell(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def exports(access, job_id):
    job = get_job(access, job_id)
    reviewed = decisions(access, job_id)
    payload, result = json.loads(job["payload"]), json.loads(job["result"])
    rows = []
    for i, item in enumerate(result.get("results", [])):
        calc = item.get("calculation") or {}
        comp = item.get("comparison") or {}
        parsed = item.get("parsed_claim") or {}
        state = execution_status(item)
        actual = comp.get("actual") if comp.get("actual") is not None else calc.get("value")
        unit = comp.get("unit") or calc.get("result_unit")
        difference = comp.get("difference")
        if difference is None:
            try:
                difference = Decimal(str(comp.get("claimed")))-Decimal(str(actual))
            except InvalidOperation:
                pass
        citations = "; ".join(f"{e.get('document_id')} / PDF page {e.get('page')} / {e.get('source_evidence_id') or e.get('id')}" for e in item.get("evidence", []))
        decision = reviewed.get(i, {})
        rows.append({"review_id": job_id, "review_name": payload.get("name", "Financial review"),
                     "run_status": job["status"], "atom_index": i, "execution_status": state,
                     "claim": item.get("claim_text"), "verdict": item.get("verdict") if state == "complete" else "",
                     "explanation": item.get("reason"), "claimed": comp.get("claimed"),
                     "actual": actual, "unit": unit, "signed_difference": _signed(difference),
                     "difference_unit": "percentage points" if str(unit).lower() in {"percent", "%", "percentage points", "percentage_points"} else unit,
                     "comparison_operator": comp.get("operator"), "tolerance": comp.get("tolerance"),
                     "formula": calc.get("formula"), "source_values": json.dumps(calc.get("raw_inputs") or {}),
                     "normalized_values": json.dumps(calc.get("inputs") or {}),
                     "input_metadata": json.dumps(calc.get("input_metadata") or {}),
                     "source_addresses": json.dumps(calc.get("source_inputs") or {}),
                     "reporting_period": json.dumps({k: parsed[k] for k in ("start_period", "end_period", "period_type", "quarter", "temporal_status") if parsed.get(k) is not None}),
                     "citations": citations, "source_versions": json.dumps(payload.get("versions") or {}),
                     "decision": decision.get("decision", "unreviewed"), "review_note": decision.get("note", ""),
                     "reviewer": decision.get("reviewer", ""), "reviewed_at": decision.get("updated", "")})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    esc = lambda v: html.escape(str(v if v is not None else ""))
    cards = []
    for row in rows:
        cards.append("<article>" + "".join(f"<p><strong>{esc(k.replace('_',' ').title())}:</strong> {esc(v)}</p>" for k, v in row.items()) + "</article>")
    readable = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Financial review</title><style>body{font:16px/1.6 system-ui;max-width:980px;margin:32px auto;padding:16px;color:#172033}article{border:1px solid #ccd3df;padding:20px;margin:16px 0;break-inside:avoid}p{overflow-wrap:anywhere}@media print{body{margin:0}}</style>'
    readable += f"<h1>{esc(payload.get('name','Financial review'))}</h1><p>Run status: {esc(job['status'])}. Exported results: {len(rows)}. Partial runs are incomplete.</p>"
    readable += '<p>Human review is required. Model confidence is not calibrated accuracy. PDF page positions are used for citations.</p>'
    readable += '<p>Signed difference is claimed minus actual. Operational failures have no factual verdict. CSV cells beginning with formula characters are escaped for spreadsheet safety.</p>'
    readable += "".join(cards) + "<h2>Source versions</h2><pre>" + esc(json.dumps(payload.get("versions", {}), indent=2)) + "</pre></html>"
    snapshot = {"review": job, "results": result, "decisions": reviewed, "export_rows": rows}
    return stream.getvalue(), readable, json.dumps(snapshot, indent=2, ensure_ascii=False)
