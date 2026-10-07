import json
from typing import Any


ALLOWED_VERDICTS = {
    "SUPPORTED",
    "CONTRADICTED",
    "PARTIALLY_SUPPORTED",
    "UNKNOWN",
}


GROUNDING_SYSTEM_PROMPT = """
You are an evidence-grounded financial claim verification system.

Your job is to determine whether a financial claim is supported by
evidence retrieved from a financial document.

You MUST follow these rules.

==================================================
EVIDENCE RULES
==================================================

1. Use ONLY the evidence provided in the prompt.

2. Do NOT use outside knowledge.

3. Do NOT invent:
   - numbers
   - dates
   - percentages
   - financial values
   - table values
   - evidence
   - page numbers
   - evidence IDs

4. Do NOT assume missing information.

5. Absence of evidence does NOT mean that a claim is false.

6. If there is not enough evidence to determine whether the claim is
   true or false, return UNKNOWN.

7. If relevant evidence conflicts and the conflict cannot be resolved
   from the supplied evidence, return UNKNOWN.

==================================================
NUMERICAL REASONING RULES
==================================================

8. A deterministic Python calculation may be provided.

9. When a deterministic calculation is provided, treat its arithmetic
   result as authoritative.

10. Do NOT replace a deterministic calculation with your own arithmetic.

11. You may question the applicability of the deterministic calculation
    ONLY if the supplied evidence clearly shows that the calculation used:

    - the wrong metric
    - the wrong financial segment
    - the wrong time period
    - the wrong unit
    - the wrong underlying evidence values

12. Never override a deterministic result merely because your own
    calculation produces a different number.

13. Distinguish carefully between:

    - percentage growth
    - percentage decline
    - percentage-point change
    - absolute change
    - CAGR
    - margin

Example:

20% margin -> 30% margin

Percentage-point change = 10 percentage points.
Relative percentage increase = 50%.

These are NOT the same thing.

==================================================
VERDICT RULES
==================================================

SUPPORTED:
The supplied evidence clearly agrees with the claim.

CONTRADICTED:
The supplied evidence clearly disagrees with the claim.

PARTIALLY_SUPPORTED:
A claim contains multiple factual components and only some of them
are supported, OR the deterministic comparison explicitly identifies
the claim as partially supported.

UNKNOWN:
There is insufficient, missing, ambiguous, or irreconcilably
conflicting evidence.

==================================================
DETERMINISTIC VERDICT PRIORITY
==================================================

If a deterministic comparison result is supplied:

- Treat that result as the authoritative numerical verdict.
- Do not change SUPPORTED to CONTRADICTED.
- Do not change CONTRADICTED to SUPPORTED.
- Do not change PARTIALLY_SUPPORTED to SUPPORTED.
- Use your reasoning to explain WHY the numerical result follows
  from the supplied evidence.

The only exception is when the supplied evidence clearly demonstrates
that the calculation was performed using the wrong metric, segment,
period, unit, or source values.

==================================================
CITATION RULES
==================================================

Only cite evidence IDs that appear in the provided evidence.

Never create an evidence ID.

Never cite evidence that is irrelevant to the claim.

Prefer direct financial evidence such as tables when available.

==================================================
FINAL BEHAVIOUR
==================================================

Be conservative.

When uncertain, return UNKNOWN rather than guessing.

Your explanation must be based directly on the supplied evidence.
"""


def build_verification_prompt(
    user_claim: str,
    claim_structure: dict[str, Any],
    evidence_hits: list[dict[str, Any]],
    calculation: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    chart_information: list[dict[str, Any]] | None = None,
) -> str:
    """
    Build an evidence-grounded verification prompt using the data
    structures already produced by this project.

    Parameters
    ----------
    user_claim:
        Original claim entered by the user.

    claim_structure:
        Structured result from claim_parser.py.

    evidence_hits:
        Reranked retrieval hits from vector_store.py / reranker.py.

    calculation:
        Deterministic result from financial_calculations.py.

    comparison:
        Result from comparison.py.

    chart_information:
        Structured chart evidence. Day 7 can populate this.
        For now this will normally be [].
    """

    chart_information = chart_information or []

    formatted_evidence = _format_evidence(evidence_hits)

    valid_ids = _collect_evidence_ids(evidence_hits)

#     return f"""
# Verify the financial claim below.

# ==================================================
# USER CLAIM
# ==================================================

# {user_claim}


# ==================================================
# PARSED CLAIM STRUCTURE
# ==================================================

# {_json(claim_structure)}


# ==================================================
# RETRIEVED DOCUMENT EVIDENCE
# ==================================================

# {formatted_evidence}


# ==================================================
# CHART INFORMATION
# ==================================================

# {_json(chart_information)}


# ==================================================
# DETERMINISTIC NUMERICAL CALCULATION
# ==================================================

# {_json(calculation) if calculation else "No deterministic calculation available."}


# ==================================================
# DETERMINISTIC COMPARISON
# ==================================================

# {_json(comparison) if comparison else "No deterministic comparison available."}


# ==================================================
# VALID EVIDENCE IDS
# ==================================================

# {_json(valid_ids)}


# ==================================================
# YOUR TASK
# ==================================================

# Determine whether the user's claim is:

# SUPPORTED
# CONTRADICTED
# PARTIALLY_SUPPORTED
# UNKNOWN

# Remember:

# - Use ONLY the supplied evidence.
# - Do not invent numbers.
# - Do not assume missing information.
# - Insufficient evidence = UNKNOWN.
# - Irreconcilably conflicting evidence = UNKNOWN.
# - Evidence clearly disagreeing with the claim = CONTRADICTED.
# - Partial support = PARTIALLY_SUPPORTED.
# - Deterministic numerical results are authoritative unless the supplied
#   evidence clearly proves the wrong metric, segment, period, unit, or
#   source values were used.
# - Only use evidence IDs listed under VALID EVIDENCE IDS.

# Explain the verdict using the provided evidence and calculation only.
# """.strip()

    return f"""
    Verify the following financial claim using only the supplied
    document evidence.

    ==================================================
    USER CLAIM
    ==================================================

    {user_claim}


    ==================================================
    PARSED CLAIM
    ==================================================

    {_json(claim_structure)}


    ==================================================
    DOCUMENT EVIDENCE
    ==================================================

    {formatted_evidence}


    ==================================================
    CHART INFORMATION
    ==================================================

    {_json(chart_information)}


    ==================================================
    DETERMINISTIC CALCULATION
    ==================================================

    {_json(calculation) if calculation else "No deterministic calculation available."}


    ==================================================
    DETERMINISTIC COMPARISON
    ==================================================

    {_json(comparison) if comparison else "No deterministic comparison available."}


    ==================================================
    VALID EVIDENCE IDS
    ==================================================

    {_json(valid_ids)}


    Determine the appropriate verdict using only the information above.

    Remember:

    - Use only the supplied document evidence.
    - Do not use outside knowledge.
    - Do not invent numbers.
    - Do not assume missing information.
    - Insufficient evidence means UNKNOWN.
    - Irreconcilably conflicting evidence means UNKNOWN.
    - Evidence clearly disagreeing with the claim means CONTRADICTED.
    - Partial support means PARTIALLY_SUPPORTED.
    - Deterministic numerical results are authoritative.
    - Cite only evidence IDs from VALID EVIDENCE IDS.
    """.strip()


def _format_evidence(
    evidence_hits: list[dict[str, Any]],
) -> str:
    """
    Format reranked retrieval hits while preserving the metadata
    required for traceability.
    """

    if not evidence_hits:
        return "NO EVIDENCE RETRIEVED."

    blocks = []

    for index, hit in enumerate(evidence_hits, start=1):

        evidence_id = (
            hit.get("source_evidence_id")
            or hit.get("evidence_id")
            or hit.get("chunk_id")
        )

        page = hit.get("page")
        evidence_type = hit.get("type", "unknown")
        chunk_id = hit.get("chunk_id")
        table_id = hit.get("table_id")
        text = hit.get("text") or hit.get("content") or ""

        block = [
            f"EVIDENCE #{index}",
            f"Evidence ID: {evidence_id}",
            f"Page: {page}",
            f"Type: {evidence_type}",
        ]

        if chunk_id:
            block.append(f"Chunk ID: {chunk_id}")

        if table_id:
            block.append(f"Table ID: {table_id}")

        block.extend(
            [
                "",
                "Content:",
                text,
            ]
        )

        blocks.append("\n".join(block))

    return "\n\n----------------------------------------\n\n".join(blocks)


def _collect_evidence_ids(
    evidence_hits: list[dict[str, Any]],
) -> list[str]:
    """
    Return the evidence IDs that the model is legally allowed to cite.
    """

    ids = []

    for hit in evidence_hits:
        evidence_id = (
            hit.get("source_evidence_id")
            or hit.get("evidence_id")
            or hit.get("chunk_id")
        )

        if evidence_id and evidence_id not in ids:
            ids.append(evidence_id)

    return ids


def _json(value: Any) -> str:
    """
    Pretty-print structured values without losing Unicode characters.
    """

    return json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
# import json
# from typing import Any


# VERIFICATION_SYSTEM_PROMPT = """
# You are a financial document claim verification assistant.

# Your task is to evaluate a user's financial claim using ONLY the
# evidence provided to you.

# Rules:

# 1. Do not invent facts, numbers, dates, or evidence.
# 2. Do not use outside knowledge.
# 3. Treat the supplied document evidence as the source of truth.
# 4. Use the supplied numerical calculation when one is available.
# 5. Do not redo arithmetic unnecessarily if a deterministic calculation
#    has already been provided.
# 6. Distinguish percentage changes from percentage-point changes.
# 7. If the evidence clearly supports the claim, return SUPPORTED.
# 8. If the evidence clearly contradicts the claim, return CONTRADICTED.
# 9. If some parts are supported and some parts are contradicted,
#    return PARTIALLY_SUPPORTED.
# 10. If there is not enough evidence to determine the answer,
#     return UNKNOWN.
# 11. Never treat absence of evidence as evidence that a claim is false.
# 12. Cite the evidence IDs provided in the evidence context.
# 13. Keep the explanation concise and tied directly to the evidence.
# """


# def build_verification_prompt(
#     *,
#     user_claim: str,
#     claim_structure: dict[str, Any],
#     retrieved_text: list[dict[str, Any]],
#     relevant_tables: list[dict[str, Any]],
#     chart_information: list[dict[str, Any]] | None = None,
#     numerical_calculations: list[dict[str, Any]] | None = None,
# ) -> str:
#     """
#     Build the complete evidence-grounded prompt for claim verification.

#     Every input is serialized explicitly so the model can distinguish
#     claim information from document evidence and calculations.
#     """

#     chart_information = chart_information or []
#     numerical_calculations = numerical_calculations or []

#     return f"""
# Verify the following financial claim.

# ========================
# USER CLAIM
# ========================

# {user_claim}


# ========================
# CLAIM STRUCTURE
# ========================

# {_pretty_json(claim_structure)}


# ========================
# RETRIEVED TEXT EVIDENCE
# ========================

# {_format_text_evidence(retrieved_text)}


# ========================
# RELEVANT TABLES
# ========================

# {_format_tables(relevant_tables)}


# ========================
# CHART INFORMATION
# ========================

# {_pretty_json(chart_information)}


# ========================
# NUMERICAL CALCULATIONS
# ========================

# {_pretty_json(numerical_calculations)}


# ========================
# TASK
# ========================

# Determine whether the claim is:

# - SUPPORTED
# - CONTRADICTED
# - PARTIALLY_SUPPORTED
# - UNKNOWN

# Use only the supplied evidence.

# Return exactly this JSON structure:

# {{
#   "verdict": "SUPPORTED | CONTRADICTED | PARTIALLY_SUPPORTED | UNKNOWN",
#   "confidence": 0.0,
#   "reason": "Short evidence-grounded explanation.",
#   "evidence_ids": [
#     "evidence_id"
#   ]
# }}

# Confidence must be a number between 0 and 1.

# Do not invent evidence IDs.

# If the evidence is insufficient, return UNKNOWN rather than guessing.
# """


# def _pretty_json(value: Any) -> str:
#     """
#     Safely serialize structured information for the prompt.
#     """

#     return json.dumps(
#         value,
#         indent=2,
#         ensure_ascii=False,
#         default=str,
#     )


# def _format_text_evidence(
#     evidence: list[dict[str, Any]],
# ) -> str:
#     """
#     Format retrieved text while preserving page and evidence metadata.
#     """

#     if not evidence:
#         return "No text evidence was retrieved."

#     blocks = []

#     for item in evidence:
#         evidence_id = item.get("id") or item.get("evidence_id")
#         page = item.get("page")
#         text = item.get("text") or item.get("content") or ""

#         blocks.append(
#             f"""
# Evidence ID: {evidence_id}
# Page: {page}
# Type: {item.get("type", "text")}

# {text}
# """.strip()
#         )

#     return "\n\n---\n\n".join(blocks)


# def _format_tables(
#     tables: list[dict[str, Any]],
# ) -> str:
#     """
#     Format structured tables in a human-readable way while preserving
#     their metadata.
#     """

#     if not tables:
#         return "No table evidence was retrieved."

#     blocks = []

#     for table in tables:
#         table_id = table.get("table_id")
#         evidence_id = table.get("evidence_id")
#         page = table.get("page")
#         headers = table.get("headers") or []
#         rows = table.get("rows") or []

#         lines = [
#             f"Evidence ID: {evidence_id}",
#             f"Table ID: {table_id}",
#             f"Page: {page}",
#             "",
#             " | ".join(str(h) for h in headers),
#         ]

#         for row in rows:
#             lines.append(
#                 " | ".join(str(cell) for cell in row)
#             )

#         blocks.append("\n".join(lines))

#     return "\n\n---\n\n".join(blocks)