"""Prepare and resume one scope check for the marked cells of an atomic claim."""
from agents.table import footnote_requests
from agents.text import check_footnote_scope
from verification.source_conflicts import DECISIVE
from verification.table_notes import linked_note


def _scope_passages(source, address):
    kind = source["type"]
    if kind == "table":
        label = source["rows"][address["row_index"]][0]
        parents = [p for r in (source.get("candidate_context") or {}).get("row_context", [])
                   if r["row_index"] == address["row_index"] for p in r.get("parent_row_labels", [])]
        return [label, *parents, source.get("title"), source.get("caption")]
    if kind == "chart":
        point = source["facts"][address["fact_index"]]
        return [source.get("title"), source.get("caption"), point.get("series_name"),
                point.get("segment"), point.get("metric")]
    return [address.get("scope_quote")]


def prepare(claim, findings, catalogs):
    entries, notes, questions = [], [], []
    seen_notes = {}
    for kind, group in findings.items():
        for n, proof in enumerate(group):
            if proof["verdict"] not in DECISIVE:
                continue
            sources = {e["id"]: e for e in proof["evidence"]}
            entry = dict(kind=kind, finding=n, notes=[], alternatives=[])
            calc = proof.get("calculation") or {}
            for role, address in calc.get("source_inputs", {}).items():
                source = sources[address["evidence_id"]]
                requests = footnote_requests(source, address, claim.get("metric")) if kind == "table" else []
                if not requests:
                    passages = [p for p in _scope_passages(source, address) if p]
                    entry["alternatives"].append(dict(evidence_id=source["id"], value=address["raw"], passages=passages))
                for req in requests:
                    key = (req["table_id"], req["row"], req["column"], req["marker"])
                    if key not in seen_notes:
                        meta = (calc.get("input_metadata") or {}).get(role, {})
                        req["amount"] = (f"{meta['currency']} {address['normalized_value']:,.12g}"
                                         if meta.get("currency") else req["raw"])
                        req["note"] = linked_note(req, source, catalogs[source["document_id"]])
                        req["table"] = source
                        seen_notes[key] = len(notes)
                        notes.append(req)
                    entry["notes"].append(seen_notes[key])
            entries.append(entry)
    if not notes:
        return None
    for req in notes:
        req["answer"] = None
        if req["note"]:
            req["answer"] = len(questions)
            passages = [req["note"]["quote"]]
            if req.get("reference"):
                passages.insert(0, req["reference"]["quote"])
            questions.append(dict(index=len(questions), question=req["question"], label=req["label"],
                                  value=req["raw"], passages=passages))
    for entry in entries:
        entry["answers"] = []
        for alternative in entry.pop("alternatives"):
            entry["answers"].append(len(questions))
            questions.append(dict(index=len(questions), question="Does this alternative figure explicitly match the claim's scope?",
                                  **alternative))
    return dict(status="pending", questions=questions, entries=entries, notes=notes)


def checkpoint_state(state):
    """Persist each cited record once, including when several cells share the same table."""
    evidence, findings, notes = {}, {}, []
    for kind, group in state["findings"].items():
        findings[kind] = []
        for proof in group:
            if proof["verdict"] not in DECISIVE:
                # Entry indices must survive a JSON round trip.
                findings[kind].append(dict(verdict=proof["verdict"]))
                continue
            saved = dict(proof)
            for item in saved.pop("evidence"):
                if item["id"] in saved["evidence_ids"]:
                    evidence[item["id"]] = item
            fusion = saved.get("fused_evidence") or {}
            saved["fused_evidence"] = {"requires_unknown": bool(fusion.get("requires_unknown"))}
            findings[kind].append(saved)
    for req in state["plan"]["notes"]:
        saved = dict(req)
        table = saved.pop("table")
        evidence[table["id"]] = table
        if saved["note"]:
            saved["note"] = dict(saved["note"])
            citation = saved["note"].pop("citation")
            evidence[citation["id"]] = citation
        notes.append(saved)
    return dict(counts=state["counts"], findings=findings, evidence=evidence, plan=dict(state["plan"], notes=notes))


def restore_state(saved):
    evidence = saved["evidence"]
    findings = {kind: [dict(p, evidence=[evidence[k] for k in p.get("evidence_ids", [])])
                       for p in group] for kind, group in saved["findings"].items()}
    notes = []
    for req in saved["plan"]["notes"]:
        note = req["note"]
        notes.append(dict(req, table=evidence[req["table_id"]],
                          note=dict(note, citation=evidence[note["evidence_id"]]) if note else None))
    return dict(counts=saved["counts"], findings=findings, outcomes={}, plan=dict(saved["plan"], notes=notes))


def apply_scope(claim, plan, findings, save):
    if plan["status"] == "pending":
        plan["answers"] = check_footnote_scope(claim, plan["questions"])
        plan["status"] = "answered"
        save()
    answers = plan["answers"]
    eligible = {kind: [] for kind in findings}
    for entry in plan["entries"]:
        indices = [plan["notes"][n]["answer"] for n in entry["notes"]] + entry["answers"]
        if indices and all(i is not None and answers[i]["scope"] == "matches" for i in indices):
            kind = entry["kind"]
            proof = findings[kind][entry["finding"]]
            scopes = {}
            for n in entry["notes"]:
                req = plan["notes"][n]
                scopes.setdefault(req["table_id"], []).append(req["note"]["quote"])
            eligible[kind].append(dict(proof, confirmed_footnotes=scopes))
    return eligible


def explain(result, plan):
    citations = {e["id"]: e for e in result["evidence"]}
    trace = []
    for req in plan["notes"]:
        table, note = req["table"], req["note"]
        citations[table["id"]] = table
        scope = plan["answers"][req["answer"]]["scope"] if note else "unclear"
        if note:
            citations[note["evidence_id"]] = note["citation"]
        ref = req.get("reference")
        if ref:
            citations[ref["evidence_id"]] = ref["citation"]
        trace.append(dict(table_id=table["id"], page=table["page"], row=req["row"], column=req["column"],
                          title=table.get("title") or "Table", amount=req["amount"],
                          label=req["label"], raw=req["raw"], unit=req["unit"], marker=req["marker"],
                          question=req["question"], scope=scope, note_id=note["evidence_id"] if note else None,
                          reference_id=ref["evidence_id"] if ref else None,
                          note_page=note["page"] if note else None, quote=note["quote"] if note else ""))
    result.update(evidence=list(citations.values()), evidence_ids=list(citations), footnote_checks=trace)
    if result["verdict"] == "UNKNOWN" and not result.get("source_resolution") and not result.get("comparison"):
        if not any(check["verdict"] in DECISIVE for check in result["evidence_checks"].values()):
            result["reason"] = "The cited evidence does not establish a figure for the requested scope."
            result["calculation_unavailable_reason"] = result["reason"]
