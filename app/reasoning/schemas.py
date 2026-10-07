from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNKNOWN = "UNKNOWN"


class VerificationResult(BaseModel):
    """
    Structured output returned by the LLM reasoning layer.

    Confidence means:
    How confident the system is that this verdict follows from
    the evidence in the uploaded document.
    """

    verdict: Verdict

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that the verdict follows from the supplied "
            "document evidence. Must be between 0 and 1."
        ),
    )

    reason: str = Field(
        min_length=1,
        description=(
            "A concise explanation grounded only in the supplied "
            "document evidence and deterministic calculations."
        ),
    )

    evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Evidence IDs supporting the verdict. IDs must only come "
            "from the evidence supplied to the model."
        ),
    )
# from enum import Enum

# from pydantic import BaseModel, ConfigDict, Field

# class Verdict(str, Enum):
#     SUPPORTED = "SUPPORTED"
#     CONTRADICTED = "CONTRADICTED"
#     PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
#     UNKNOWN = "UNKNOWN"

# class VerificationResult(BaseModel):
#     model_config = ConfigDict(extra="forbid")
#     verdict : Verdict

#     confidence: float = Field(
#         ge = 0.0,
#         le = 1.0,
#         description = (
#             "Confidence that the verdict follows from the supplied "
#             "document evidence. Must be between 0 and 1."
#         ),
#     )

#     reason : str = Field(
#         min_length= 1,
#         description=(
#             "A concise explanation grounded only in the supplied "
#             "document evidence and deterministic calculations."
#         ),
#     )

#     evidence_ids : list[str] = Field(
#         default_factory=list,
#         description=(
#             "A concise explanation grounded only in the supplied "
#             "document evidence and deterministic calculations."
#         ),
#     )