"""Extract chart images and select printed points for numerical verification."""
from __future__ import annotations

from pathlib import Path

from services.model_gateway import CHART_EXTRACTION_MODEL as CHART_MODEL
from verification.evidence_context import has_printed_chart_value
from verification.grounded_numeric import NumericValidationError, Selection, _local_selection


CHART_OPERATIONS = {"level", "growth", "absolute_change", "percentage_points", "direction", "cagr"}


def extract_chart_evidence(chart: dict) -> dict:
    """Read one cropped image and create a chart evidence record.

    Accepts chart detector metadata, or a record with chart_id, document_id,
    page, and image_path. The analysis retains estimates for inspection.
    The facts contain only points with printed numeric labels.
    """
    from reasoning.vision_client import _build_facts, read_chart_image

    image_path = Path(chart["image_path"])
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    evidence = dict(chart.get("evidence") or {})
    source_id = evidence.get("id") or chart["chart_id"]
    document_id = chart["document_id"]
    page = chart.get("page") or chart["page_number"]

    analysis = read_chart_image(
        image_path=image_path, chart=chart, model=CHART_MODEL, purpose="chart_extraction",
    )
    details = analysis.model_dump(mode="json")
    facts = [
        dict(fact, evidence_id=source_id)
        for fact in _build_facts(analysis=analysis, chart=chart)
        if has_printed_chart_value(fact)
    ]
    evidence.update(
        id=source_id, document_id=document_id, page=page, type="chart",
        chart_type=analysis.chart_type, image_path=str(image_path),
        title=analysis.title or chart.get("title") or "",
        unit=analysis.display_unit or (analysis.y_axis.unit if analysis.y_axis else None)
        or analysis.normalized_unit or "",
        x_axis=details["x_axis"], y_axis=details["y_axis"],
        facts=facts, analysis=details, extraction_method="chart-vision-v1",
    )
    call = getattr(analysis, "_gateway_call", {})
    evidence["model_provenance"] = {
        "analysis": [call] if call else [],
        "selected_analysis_model": call.get("actual_model", CHART_MODEL),
    }
    return evidence


def select_chart_operands(claim: dict, evidence: list[dict]) -> Selection:
    """Return unambiguous fact addresses for an interpreted level or change claim."""
    if claim.get("operation") not in CHART_OPERATIONS:
        return Selection(applicable=False, reason="This claim has no supported chart operation.")

    allowed = claim.get("meaning_source_ids")
    charts = {}
    for item in evidence:
        if item.get("type") != "chart" or (allowed is not None and item["id"] not in allowed):
            continue
        if item["id"] in charts:
            raise NumericValidationError("Chart evidence ids must be unique.")
        # Preserve fact indexes on saved evidence, including any estimated points.
        facts = [fact if has_printed_chart_value(fact) else dict(fact, value_source="unreadable")
                 for fact in item.get("facts") or []]
        charts[item["id"]] = dict(item, facts=facts)

    selection = _local_selection(claim, list(charts.values()))
    if selection is None:
        return Selection(applicable=False, reason="No unambiguous matching printed chart points.")

    if len(selection.operands) == 2:
        old, new = selection.operands
        old_fact = charts[old.evidence_id]["facts"][old.fact_index]
        new_fact = charts[new.evidence_id]["facts"][new.fact_index]
        fields = ("series_name", "segment", "unit")
        if old.evidence_id != new.evidence_id or any(old_fact.get(key) != new_fact.get(key) for key in fields):
            return Selection(applicable=False, reason="The printed chart points belong to different charts, series, or units.")

    return selection
