from __future__ import annotations

import csv
import html
import io
import json
from pilot.store import decisions, get_job


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
        citations = "; ".join(f"{e.get('document_id')} / PDF page {e.get('page')} / {e.get('source_evidence_id') or e.get('id')}" for e in item.get("evidence", []))
        decision = reviewed.get(i, {})
        rows.append({"claim": item.get("claim_text"), "verdict": item.get("verdict"), "explanation": item.get("reason"),
                     "claimed": comp.get("claimed"), "actual": comp.get("actual"), "unit": comp.get("unit"),
                     "formula": calc.get("formula"), "source_values": json.dumps(calc.get("raw_inputs") or {}),
                     "normalized_values": json.dumps(calc.get("inputs") or {}), "citations": citations,
                     "decision": decision.get("decision", "unreviewed"), "review_note": decision.get("note", ""),
                     "reviewer": decision.get("reviewer", ""), "reviewed_at": decision.get("updated", "")})
    stream = io.StringIO(newline="")
    fields = list(rows[0]) if rows else ["claim", "verdict", "explanation"]
    writer = csv.DictWriter(stream, fieldnames=fields)
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
    readable += "".join(cards) + "<h2>Source versions</h2><pre>" + esc(json.dumps(payload.get("versions", {}), indent=2)) + "</pre></html>"
    snapshot = {"review": job, "results": result, "decisions": reviewed}
    return stream.getvalue(), readable, json.dumps(snapshot, indent=2, ensure_ascii=False)
