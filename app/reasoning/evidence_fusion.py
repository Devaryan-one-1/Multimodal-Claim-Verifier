from __future__ import annotations
import math
import re
from collections import defaultdict
from typing import Any, Iterable

# ---------------------------------------------------------
# Evidence priority
# ---------------------------------------------------------

EVIDENCE_PRIORITY = {
    "calculation": 100,
    "table": 80,
    "chart": 70,
    "text": 60,
    "paragraph": 60,
    "ocr": 50,
    "caption": 45,
    "footnote": 40,
    "other": 10,
}


SUPPORTED_MODALITIES = {
    "table",
    "chart",
    "text",
    "paragraph",
    "ocr",
    "caption",
    "footnote",
}


def fuse_evidence(
    evidence_hits: list[dict[str, Any]] | None,
    *,
    calculation: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    chart_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Combine all retrieved evidence into one unified evidence object.

    This function is deterministic.

    It does NOT:
        - call Gemini
        - calculate the final verdict
        - invent missing values

    Evidence priority:

        calculation
            ↓
        table
            ↓
        chart
            ↓
        text
            ↓
        OCR

    Conflict rules:

        Higher-priority evidence may resolve disagreement from
        lower-priority evidence.

        Example:

            Table: revenue = 100
            Text:  revenue = 90

        Table wins because table evidence has higher numerical priority.

        However:

            Table A: revenue = 100
            Table B: revenue = 110

        Same-priority evidence disagrees.

        Therefore:

            unresolved_conflict = True
            requires_unknown = True
    """

    evidence_hits = evidence_hits or []
    chart_evidence = chart_evidence or []

    # -----------------------------------------------------
    # Combine normal retrieval hits and chart evidence
    # -----------------------------------------------------

    combined = [
        dict(item)
        for item in evidence_hits
    ]

    # Charts will mostly be populated on Day 7.
    for item in chart_evidence:
        chart = dict(item)

        chart.setdefault(
            "type",
            "chart",
        )

        combined.append(chart)

    # -----------------------------------------------------
    # Normalize evidence
    # -----------------------------------------------------

    normalized = [
        _normalize_evidence(item)
        for item in combined
    ]

    # -----------------------------------------------------
    # Remove duplicate evidence
    # -----------------------------------------------------

    deduplicated, duplicates_removed = _deduplicate(
        normalized
    )

    # -----------------------------------------------------
    # Rank using our modality priority
    # -----------------------------------------------------

    ranked = sorted(
        deduplicated,
        key=_evidence_sort_key,
    )

    # -----------------------------------------------------
    # Split by modality
    # -----------------------------------------------------

    table_items = [
        item
        for item in ranked
        if item["modality"] == "table"
    ]

    chart_items = [
        item
        for item in ranked
        if item["modality"] == "chart"
    ]

    text_items = [
        item
        for item in ranked
        if item["modality"] in {
            "text",
            "paragraph",
        }
    ]

    ocr_items = [
        item
        for item in ranked
        if item["modality"] == "ocr"
    ]

    other_items = [
        item
        for item in ranked
        if item["modality"]
        not in {
            "table",
            "chart",
            "text",
            "paragraph",
            "ocr",
        }
    ]

    # -----------------------------------------------------
    # Detect structured conflicts
    # -----------------------------------------------------

    conflicts = _detect_conflicts(
        ranked
    )

    unresolved_conflict = any(
        not conflict["resolved"]
        for conflict in conflicts
    )

    # -----------------------------------------------------
    # Collect evidence IDs
    # -----------------------------------------------------

    evidence_ids = _unique_in_order(
        item["evidence_id"]
        for item in ranked
        if item.get("evidence_id")
    )

    # -----------------------------------------------------
    # Collect page numbers
    # -----------------------------------------------------

    pages = sorted(
        {
            item["page"]
            for item in ranked
            if isinstance(
                item.get("page"),
                int,
            )
        }
    )

    # -----------------------------------------------------
    # Determine strongest numerical evidence source
    # -----------------------------------------------------

    primary_numerical_source = (
        _primary_numerical_source(
            calculation=calculation,
            ranked_evidence=ranked,
        )
    )

    # -----------------------------------------------------
    # Final unified object
    # -----------------------------------------------------

    return {
        "text_evidence": text_items,
        "table_evidence": table_items,
        "chart_evidence": chart_items,
        "ocr_evidence": ocr_items,
        "other_evidence": other_items,

        "ranked_evidence": ranked,

        "calculation": calculation,
        "comparison": comparison,

        "evidence_ids": evidence_ids,
        "pages": pages,

        "evidence_priority": [
            "calculation",
            "table",
            "chart",
            "text",
            "ocr",
        ],

        "primary_numerical_source": (
            primary_numerical_source
        ),

        "has_conflict": bool(conflicts),

        "conflicts": conflicts,

        "unresolved_conflict": (
            unresolved_conflict
        ),

        # Final reasoning layer should return UNKNOWN
        # when this is True.
        "requires_unknown": (
            unresolved_conflict
        ),

        "duplicates_removed": (
            duplicates_removed
        ),

        "evidence_count": len(ranked),
    }


# =========================================================
# Normalization
# =========================================================

def _normalize_evidence(
    item: dict[str, Any],
) -> dict[str, Any]:

    output = dict(item)

    modality = str(
        item.get("type")
        or item.get("modality")
        or "other"
    ).lower()

    if modality not in SUPPORTED_MODALITIES:
        modality = "other"

    # Your current retriever usually supplies
    # source_evidence_id.
    evidence_id = (
        item.get("source_evidence_id")
        or item.get("evidence_id")
        or item.get("id")
        or item.get("chunk_id")
    )

    content = item.get("text")

    if content is None:
        content = item.get("content")

    if content is None:
        content = ""

    output["evidence_id"] = (
        str(evidence_id)
        if evidence_id is not None
        else None
    )

    output["modality"] = modality

    output["content"] = str(content)

    output["priority"] = (
        EVIDENCE_PRIORITY.get(
            modality,
            EVIDENCE_PRIORITY["other"],
        )
    )

    return output


# =========================================================
# Deduplication
# =========================================================

def _deduplicate(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Remove repeated evidence.

    Duplicate if:

    1. Same evidence ID
    OR
    2. Exactly same normalized content

    If duplicate versions have different modalities,
    keep the higher-priority one.

    Example:

        text representation of table
        +
        actual table evidence

    We keep the table.
    """

    kept = {}

    order = []

    removed = 0

    for item in items:

        key = _dedupe_key(item)

        if key not in kept:

            kept[key] = item

            order.append(key)

            continue

        removed += 1

        existing = kept[key]

        # Lower sort key means better evidence because
        # priority/score are negated.
        if (
            _evidence_sort_key(item)
            <
            _evidence_sort_key(existing)
        ):
            kept[key] = item

    return (
        [
            kept[key]
            for key in order
        ],
        removed,
    )


def _dedupe_key(
    item: dict[str, Any],
):

    evidence_id = item.get(
        "evidence_id"
    )

    if evidence_id:

        return (
            "id",
            str(evidence_id),
        )

    normalized_content = (
        _normalize_text(
            item.get("content")
            or ""
        )
    )

    if normalized_content:

        return (
            "content",
            normalized_content,
        )

    # Last-resort identity
    return (
        "fallback",
        str(
            (
                item.get("page"),
                item.get("table_id"),
                item.get("chart_id"),
                item.get("chunk_id"),
                item.get("type"),
            )
        ),
    )


# =========================================================
# Evidence ranking
# =========================================================

def _evidence_sort_key(
    item: dict[str, Any],
):

    priority = float(
        item.get("priority")
        or 0
    )

    rerank_score = _finite_number(
        item.get("rerank_score"),
        default=float("-inf"),
    )

    retrieval_score = _finite_number(
        item.get("score"),
        default=float("-inf"),
    )

    evidence_id = str(
        item.get("evidence_id")
        or ""
    )

    # sorted() is ascending,
    # therefore negate scores we want descending.
    return (
        -priority,
        -rerank_score,
        -retrieval_score,
        evidence_id,
    )


# =========================================================
# Conflict detection
# =========================================================

def _detect_conflicts(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Detect conflicts when structured facts are available.

    We intentionally DO NOT try to extract semantic facts
    from arbitrary prose here.

    Evidence may optionally contain:

        "facts": [
            {
                "metric": "revenue",
                "segment": "international",
                "period": 2025,
                "value": 100,
                "unit": "USD billion"
            }
        ]

    Chart extraction on Day 7 can produce this structure.

    Tables may also expose structured facts later.

    If facts are unavailable, we do not invent a conflict.
    """

    grouped = defaultdict(list)

    for item in items:

        facts = _structured_facts(
            item
        )

        for fact in facts:

            key = _fact_key(
                fact
            )

            if key is None:
                continue

            value = _fact_value(
                fact
            )

            if value is None:
                continue

            grouped[key].append(
                {
                    "value": value,

                    "raw_value": (
                        fact.get("value")
                    ),

                    "evidence_id": (
                        item.get(
                            "evidence_id"
                        )
                    ),

                    "page": (
                        item.get("page")
                    ),

                    "modality": (
                        item.get(
                            "modality"
                        )
                    ),

                    "priority": (
                        item.get(
                            "priority",
                            0,
                        )
                    ),
                }
            )

    conflicts = []

    for (
        fact_key,
        observations,
    ) in grouped.items():

        distinct_values = (
            _distinct_values(
                observations
            )
        )

        # Everyone agrees.
        if len(distinct_values) <= 1:
            continue

        highest_priority = max(
            observation["priority"]
            for observation
            in observations
        )

        highest_priority_items = [
            observation
            for observation in observations
            if observation["priority"]
            == highest_priority
        ]

        top_values = _distinct_values(
            highest_priority_items
        )

        # -------------------------------------------------
        # Resolve using evidence priority
        # -------------------------------------------------

        # Example:
        #
        # Table = 100
        # Text  = 90
        #
        # Highest priority evidence contains only value 100.
        #
        # Therefore the table resolves the disagreement.
        resolved = (
            len(top_values) == 1
        )

        preferred = None

        if resolved:

            preferred = (
                highest_priority_items[0]
            )

        metric, segment, period, unit = (
            fact_key
        )

        conflicts.append(
            {
                "metric": (
                    metric or None
                ),

                "segment": (
                    segment or None
                ),

                "period": (
                    period or None
                ),

                "unit": (
                    unit or None
                ),

                "observations": (
                    observations
                ),

                "resolved": (
                    resolved
                ),

                "resolution": (
                    "resolved_by_evidence_priority"
                    if resolved
                    else "unresolved"
                ),

                "preferred": (
                    preferred
                ),
            }
        )

    return conflicts


# =========================================================
# Structured facts
# =========================================================

def _structured_facts(
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Accept:

        facts=[...]

    or a single structured fact:

        {
            "metric": "...",
            "period": 2025,
            "value": ...
        }
    """

    facts = item.get(
        "facts"
    )

    if isinstance(facts, list):

        return [
            fact
            for fact in facts
            if isinstance(
                fact,
                dict,
            )
        ]

    if (
        "value" in item
        and any(
            key in item
            for key in (
                "metric",
                "period",
                "segment",
            )
        )
    ):
        return [item]

    return []


def _fact_key(
    fact: dict[str, Any],
):

    metric = _clean_fact_field(
        fact.get("metric")
    )

    segment = _clean_fact_field(
        fact.get("segment")
    )

    period = _clean_fact_field(
        fact.get("period")
        or fact.get("year")
    )

    unit = _clean_fact_field(
        fact.get("unit")
    )

    # We need at least metric + period
    # to confidently compare two values.
    if not metric or not period:
        return None

    return (
        metric,
        segment,
        period,
        unit,
    )


def _fact_value(
    fact: dict[str, Any],
):

    value = fact.get(
        "value"
    )

    if value is None:
        return None

    # Numeric values are ideal.
    if isinstance(
        value,
        (int, float),
    ):

        if math.isfinite(
            float(value)
        ):
            return float(value)

        return None

    text = str(
        value
    ).strip()

    if not text:
        return None

    # Simple numeric strings only.
    #
    # Proper unit normalization belongs in:
    #
    # verification/number_parser.py
    # verification/unit_normalizer.py
    simple = text.replace(
        ",",
        "",
    )

    if re.fullmatch(
        r"[-+]?\d+(?:\.\d+)?",
        simple,
    ):

        try:
            return float(
                simple
            )

        except ValueError:
            pass

    return text.casefold()


def _distinct_values(
    observations,
):

    return {
        observation["value"]
        for observation
        in observations
    }


# =========================================================
# Primary numerical evidence
# =========================================================

def _primary_numerical_source(
    *,
    calculation,
    ranked_evidence,
):
    """
    Follow our agreed priority:

        calculation
        table
        chart
        text
        OCR
    """

    if (
        calculation
        and calculation.get(
            "value"
        )
        is not None
    ):
        return "calculation"

    for modality in (
        "table",
        "chart",
        "text",
        "paragraph",
        "ocr",
    ):

        if any(
            item["modality"]
            == modality
            for item
            in ranked_evidence
        ):

            if modality == "paragraph":
                return "text"

            return modality

    return None


# =========================================================
# Utility functions
# =========================================================

def _normalize_text(
    text,
):

    return " ".join(
        str(text)
        .casefold()
        .split()
    )


def _clean_fact_field(
    value,
):

    if value is None:
        return ""

    return " ".join(
        str(value)
        .casefold()
        .split()
    )


def _unique_in_order(
    values: Iterable[str],
):

    seen = set()

    output = []

    for value in values:

        if value not in seen:

            seen.add(
                value
            )

            output.append(
                value
            )

    return output


def _finite_number(
    value,
    *,
    default,
):

    try:
        number = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default

    if not math.isfinite(
        number
    ):
        return default

    return number