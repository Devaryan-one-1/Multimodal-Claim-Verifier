"""One evidence-aware interpretation call; source addresses and arithmetic stay local."""
from __future__ import annotations

import copy
import json
import re

from services.model_gateway import structured
from services.verification_errors import ParseValidationError
from verification.interpretation_contract import SCHEMA, SYSTEM, FIELD_GUIDE, EXAMPLES
from verification.reporting_periods import explicit_dates, explicit_period, explicit_quarters
from verification.grounded_numeric import _mapping, _chart_period
from verification.claim_integrity import quantities

COMPARATIVE = {"growth", "absolute_change", "percentage_points", "cagr", "direction"}


class InterpretationResponse:
    """Gateway schema interface using the exact JSON contract tested offline."""
    @classmethod
    def model_json_schema(cls):
        return copy.deepcopy(SCHEMA)

    @classmethod
    def model_validate(cls, value):
        from jsonschema import Draft202012Validator
        Draft202012Validator(SCHEMA).validate(value)
        result = cls()
        result.data = copy.deepcopy(value)
        return result


def period_descriptor(raw, *, kind=None, year=None, context="", document_id=None):
    raw = str(raw or "")
    dates = [day for day, _ in explicit_dates(raw)]
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", raw))
    if len(dates) > 1 or len(years) > 1:
        return None
    if year is not None and years and str(year) not in years:
        return None
    year = str(year or next(iter(years), ""))
    if not re.fullmatch(r"(?:19|20)\d{2}", year):
        return None
    aliases = {"year": "annual", "twelve_months": "annual", "three_months": "quarter"}
    kind = aliases.get(kind, kind)
    try:
        stated = explicit_period(raw) or (explicit_period(context) if not kind else None)
    except ValueError:
        stated = None  # Mixed-duration headers must be resolved per column.
    if stated:
        if kind and kind != stated[0]:
            return None
        kind = stated[0]
    # Date-only columns are balances only with explicit balance-sheet context.
    if not kind and dates and re.search(r"balance sheets?|financial position", context, re.I):
        kind = "point_in_time"
    quarters = explicit_quarters(raw)
    if len(quarters) > 1:
        return None
    quarter = next(iter(quarters), None)
    key = dates[0] if dates else year + (f"-Q{quarter}" if quarter else "")
    kind = kind or "unspecified"
    return {"key": f"{kind}:{key}", "year": int(year), "date": dates[0] if dates else None,
            "period_type": kind, "quarter": quarter, "raw": raw, "document_id": document_id}


def candidate(item):
    """Serialize original evidence, preserving row/column indexes and context."""
    record = {k: copy.deepcopy(item.get(k)) for k in (
        "id", "document_id", "company", "page", "type", "title", "section", "caption",
        "headers", "rows", "column_map", "row_units", "unit", "units", "facts",
        "content", "candidate_context", "context", "column_headers") if item.get(k) is not None}
    context = " ".join(str(item.get(k) or "") for k in ("title", "section", "context", "column_headers"))
    periods = {}
    if item.get("type") == "table":
        headers, rows = item.get("headers") or [], item.get("rows") or []
        width = max([len(headers), *(len(row) for row in rows)], default=0)
        mappings = _mapping(item, width)
        # Date/year headers are usable even for older indexes with no column map.
        if not mappings:
            mappings = [{"col": index, "raw": raw} for index, raw in enumerate(headers) if index]
        for mapping in mappings:
            column = mapping.get("col")
            if not isinstance(column, int) or not 0 < column < width:
                continue
            raw = mapping.get("raw") or (headers[column] if column < len(headers) else mapping.get("year"))
            descriptor = period_descriptor(raw, kind=mapping.get("period"), year=mapping.get("year"),
                                           context=context, document_id=item["document_id"])
            if descriptor:
                entry = periods.setdefault(descriptor["key"], dict(descriptor, columns=[]))
                entry["columns"].append(column)
    elif item.get("type") == "chart":
        for fact in item.get("facts") or []:
            try:
                raw = _chart_period(fact, item)
            except ValueError:
                continue
            descriptor = period_descriptor(raw, context=context, document_id=item["document_id"])
            if descriptor:
                periods[descriptor["key"]] = descriptor
    else:
        # These are available labels, not automatic metric-to-value bindings.
        # The later source selector still validates the exact sentence/number.
        text = str(item.get("content") or "")
        spans = [(match.start(), match.end(), match.group()) for _, match in explicit_dates(text)]
        spans += [(match.start(), match.end(), match.group()) for match in re.finditer(r"\b(?:19|20)\d{2}\b", text)
                  if not any(start <= match.start() < end for start, end, _ in spans)]
        for start, end, raw in spans:
            descriptor = period_descriptor(raw, context=text[max(0, start-70):end+20], document_id=item["document_id"])
            if descriptor:
                periods[descriptor["key"]] = descriptor
    record["periods"] = periods
    return record


def _fail(message, reason="invalid_structure"):
    raise ParseValidationError(message, reason=reason)


def _bindings(value, records):
    sources = value["sources"]
    if not sources or len(sources) != len(set(sources)) or any(source not in records for source in sources):
        _fail("Interpretation selected a source outside the retrieved evidence.")
    if len({records[source]["document_id"] for source in sources}) != 1:
        _fail("One interpretation mixes documents; cross-document comparisons are not enabled.")
    bindings = {}
    for field in ("start", "end"):
        key = value[field]
        if key is None:
            continue
        matches = [records[source]["periods"][key] for source in sources if key in records[source]["periods"]]
        if not matches:
            _fail("Interpretation selected a period absent from its cited sources.", "period")
        bindings[field] = matches[0]
    if len(bindings) == 2:
        old, new = bindings["start"], bindings["end"]
        if old["period_type"] != new["period_type"] or old["key"] == new["key"]:
            _fail("Interpretation selected incomparable or identical periods.", "period")
    return bindings


def adapt_response(original, response, records):
    """Validate references/literals, then adapt to the existing Python calculator."""
    status = response["status"]
    if status != "ready":
        if response["atoms"]:
            _fail("A non-ready interpretation cannot contain assertions.")
        if status == "clarification_required":
            if not response["clarification"] or not response["options"]:
                _fail("Clarification must include a specific question and source-backed options.")
            options = []
            for option in response["options"]:
                bindings = _bindings(option, records)
                labels = [bindings[k]["raw"] for k in ("start", "end") if k in bindings]
                context = "; ".join(str(records[s].get("title") or records[s].get("context") or s)[:180]
                                    for s in option["sources"])
                options.append(f"{' to '.join(labels)} ({option['duration']}; {context}; sources: {', '.join(option['sources'])})")
            return {"status": status, "question": response["clarification"], "options": options, "claims": []}
        if response["clarification"] is not None or response["options"]:
            _fail("Only an ambiguous interpretation may ask for clarification.")
        return {"status": status, "claims": []}
    if not 1 <= len(response["atoms"]) <= 8 or response["clarification"] is not None or response["options"]:
        _fail("Interpretation must return one to eight assertions without unrelated clarification.")
    claims = []
    literal_spans = []
    for atom in response["atoms"]:
        bindings = _bindings(atom, records)
        literal, operation = atom["literal"], atom["operation"]
        if literal is not None and (not literal.strip() or not re.search(
                r"(?<![\w.,+\-$€£])" + re.escape(literal) + r"(?![\w%]|\.\d)", original)):
            _fail("Interpretation changed the original claimed quantity.", "quantity")
        if literal:
            literal_spans.extend(m.span() for m in re.finditer(re.escape(literal), original))
        if not literal and operation not in {"direction", "qualitative"}:
            _fail("A numerical assertion requires its original quantity.", "quantity")
        if operation in COMPARATIVE and set(bindings) != {"start", "end"}:
            _fail("A comparison requires both source reporting periods.", "period")
        if operation not in COMPARATIVE and (atom["start"] is not None or (atom["end"] is None and operation != "qualitative")):
            _fail("A dated level requires one end period and no start period.", "period")
        if operation == "ratio" and not atom["denominator_metric"]:
            _fail("A ratio requires the denominator metric.", "metric")
        if operation != "ratio" and atom["denominator_metric"] is not None:
            _fail("Only a ratio can carry a denominator metric.", "metric")
        if atom["unit"] == "percentage_points" and operation not in {"percentage_points", "level"}:
            _fail("Relative growth cannot be interpreted as percentage points.", "operation")
        old, new = bindings.get("start"), bindings.get("end")
        if operation == "cagr" and (not old or not new or old["period_type"] != "annual" or new["year"] <= old["year"]):
            _fail("CAGR requires increasing, comparable annual reporting years.", "period")
        source = records[atom["sources"][0]]
        periods = " to ".join(bindings[k]["raw"] for k in ("start", "end") if k in bindings)
        # The original wording is retained separately; this is an explicit display
        # of the validated interpretation, not a model-generated paraphrase.
        label = f"{atom['metric']}: {literal or atom['direction']} ({operation}; {periods})"
        pc = {"claim_text": label, "original_input": original, "claim_type": "assertion",
              "company": source.get("company"), "metric": atom["metric"],
              "denominator_metric": atom["denominator_metric"], "segment": None,
              "claimed_literal": literal, "operation": operation, "unit": "number" if atom["unit"] == "count" else atom["unit"],
              "comparison_operator": atom["comparison"], "direction": atom["direction"],
              "negated": atom["negated"], "approximate": atom["approximate"],
              "start_period": old["year"] if old else None, "end_period": new["year"] if new else None,
              "period_quote": periods, "period_type": new["period_type"] if new else "unspecified",
              "meaning_source_ids": atom["sources"], "resolved_periods": bindings,
              "interpretation_method": "evidence_llm_v3", "notes": []}
        if old and new:
            pc["inferred_periods"] = {"old": old, "new": new}
        claims.append(pc)
    # Check retention, without a second model rewrite or linguistic heuristics
    # that would force conversational claims back into the legacy parser.
    companies = {record.get("company") for record in records.values() if record.get("company")}
    for start, end, _ in quantities(original, companies):
        if not any(left <= start and end <= right for left, right in literal_spans):
            _fail("Interpretation omitted a stated numerical quantity.", "coverage")
    original_dates = {day for day, _ in explicit_dates(original)}
    selected_dates = {period.get("date") for pc in claims for period in pc["resolved_periods"].values()}
    if not original_dates.issubset(selected_dates):
        _fail("Interpretation omitted an explicitly stated reporting date.", "period")
    return {"status": status, "claims": claims}


def interpret_claims(original, evidence):
    records = {item["id"]: candidate(item) for item in evidence}
    if not records:
        return {"status": "insufficient_evidence", "claims": []}
    # Bound context rather than silently cutting cells or their parent headers.
    payload = json.dumps({"claim": original, "evidence": list(records.values())}, ensure_ascii=False)
    if len(payload) > 100000:
        _fail("Retrieved evidence is too large for one interpretation. Submit fewer claims or select a narrower source.")
    source_guide = ("\nProduction evidence contains original tables, text and chart facts. "
                    "Canonical metric names come from their row labels, titles or text. "
                    "Each source's periods object supplies the exact allowed start/end keys; "
                    "copy those keys, not bare years. Its entries include raw labels and durations. "
                    "For tables, columns are zero-based original column indexes. "
                    "Match the row's parent labels, statement type, company and segment, not just its name. "
                    "Same-page background is context, not a replacement for the cited source's values. "
                    "Only use periods from the sources selected for that atom.")
    result = structured(InterpretationResponse, payload, system=SYSTEM + "\n\n" + FIELD_GUIDE + source_guide,
                        purpose="claim_interpretation", examples=EXAMPLES)
    return adapt_response(original, result.data, records)
