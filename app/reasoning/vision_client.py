from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Literal
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field


load_dotenv()


# =========================================================
# Configuration
# =========================================================

PROCESSED_ROOT = Path("data/processed")

CACHE_VERSION = 1

# DEFAULT_MODEL = os.getenv(
#     "GEMINI_VISION_MODEL",
#     "gemini-flash-latest",
# )
# DEFAULT_MODEL = os.getenv(
#     "GEMINI_CHART_ANALYSIS_MODEL",
#     "gemini-3.5-flash-lite",
# )

PRIMARY_MODEL = os.getenv(
    "GEMINI_CHART_ANALYSIS_MODEL",
    "gemini-3.5-flash-lite",
)


FALLBACK_MODEL = os.getenv(
    "GEMINI_CHART_ANALYSIS_FALLBACK_MODEL",
    "gemini-3.5-flash",
)
ChartType = Literal[
    "bar",
    "line",
    "pie",
    "donut",
    "area",
    "waterfall",
    "combo",
    "other_financial_chart",
    "unknown",
]


ValueSource = Literal[
    "explicit_label",
    "estimated_from_axis",
    "unreadable",
]


# =========================================================
# Structured Gemini output
# =========================================================


class AxisInfo(BaseModel):
    """
    Information about one visible chart axis.

    Pie/donut charts may not have axes.
    """

    label: str | None = None

    unit: str | None = None

    values: list[str] = Field(
        default_factory=list
    )

class ExtractedChartPoint(BaseModel):
    """
    One visual data point extracted directly from the chart.
    """

    series_name: str | None = Field(
        default=None,
        description=(
            "Visible series or legend name. "
            "For a single-series chart with no legend, use the "
            "chart title when it describes the series."
        ),
    )

    metric: str | None = Field(
        default=None,
        description=(
            "Financial metric such as revenue, net income, "
            "margin, expenses, or cash flow. Null if unclear."
        ),
    )

    segment: str | None = Field(
        default=None,
        description=(
            "Business/geographic segment if clearly visible. "
            "Null if unclear."
        ),
    )

    period: str | None = Field(
        default=None,
        description=(
            "Time period such as 2024, Q1 2026, or June 2026."
        ),
    )

    category: str | None = Field(
        default=None,
        description=(
            "Non-time category represented by this point."
        ),
    )

    value: float | None = Field(
        default=None,
        description=(
            "Numeric chart value. Use null if it cannot "
            "be read reliably."
        ),
    )

    display_value: str | None = Field(
        default=None,
        description=(
            "Exact visible value label such as '$11B' or '25%'."
        ),
    )

    value_source: ValueSource = Field(
        description=(
            "explicit_label if the number is printed, "
            "estimated_from_axis if visually estimated, "
            "unreadable if not reliable."
        )
    )


class RawChartExtraction(BaseModel):
    """
    Simple schema Gemini produces directly.
    """

    chart_type: ChartType

    title: str | None = None

    display_unit: str | None = None

    normalized_unit: str | None = None

    x_axis: AxisInfo | None = None

    y_axis: AxisInfo | None = None

    legend: list[str] = Field(
        default_factory=list
    )

    data_points: list[ExtractedChartPoint] = Field(
        default_factory=list,
        description=(
            "One entry for every visible bar, line point, "
            "pie slice, or other graphical data mark."
        ),
    )

    warnings: list[str] = Field(
        default_factory=list
    )

class ChartPoint(BaseModel):
    """
    One extracted data point.
    """

    # For time-based charts.
    period: str | None = None

    # For categorical charts / pie slices.
    category: str | None = None

    # Numeric value.
    #
    # MUST be None when the value cannot be read
    # reliably.
    value: float | None = None

    # Exact visible text if available.
    #
    # Example:
    # "$11B"
    # "25%"
    display_value: str | None = None

    value_source: ValueSource = (
        "unreadable"
    )


class ChartSeries(BaseModel):
    """
    One chart series.

    Examples:
        International
        North America
        Revenue
        Operating Margin
    """

    name: str | None = None

    # Financial metric represented by this series.
    #
    # Example:
    # revenue
    # operating margin
    # net income
    #
    # Leave None if unclear.
    metric: str | None = None

    # Optional segment.
    #
    # Example:
    # international
    # north america
    #
    # Leave None if unclear.
    segment: str | None = None

    values: list[ChartPoint] = Field(
        default_factory=list
    )


class ChartAnalysisResponse(BaseModel):
    """
    Complete structured interpretation of one chart.
    """

    chart_type: ChartType

    title: str | None = None

    # Unit exactly as displayed.
    #
    # Example:
    # "$ billions"
    display_unit: str | None = None

    # Normalized representation when clearly identifiable.
    #
    # Example:
    # "USD billion"
    normalized_unit: str | None = None

    x_axis: AxisInfo | None = None

    y_axis: AxisInfo | None = None

    legend: list[str] = Field(
        default_factory=list
    )

    series: list[ChartSeries] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )


# =========================================================
# Public API
# =========================================================


def analyze_chart(
    chart: dict[str, Any],
    *,
    overwrite: bool = False,
    page_image_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Analyze one chart produced by chart_detector.py.

    Primary input:
        cropped chart image

    Optional fallback:
        full page image

    Expected chart input:

        {
            "chart_id": "chart_012_001",
            "document_id": "...",
            "page": 12,
            "chart_type": "bar",
            "title": "...",
            "bbox": [...],
            "image_path": "data/charts/...png"
        }

    Returns:

        structured chart data
        +
        normalized facts
    """

    chart_id = chart.get(
        "chart_id"
    )

    if not chart_id:
        raise ValueError(
            "chart must contain chart_id."
        )

    document_id = chart.get(
        "document_id"
    )

    if not document_id:
        raise ValueError(
            "chart must contain document_id."
        )

    page = (
        chart.get("page")
        or chart.get("page_number")
    )

    image_path = chart.get(
        "image_path"
    )

    if not image_path:
        raise ValueError(
            "chart must contain image_path."
        )

    image_path = Path(
        image_path
    )

    if not image_path.exists():
        raise FileNotFoundError(
            f"Chart image not found: "
            f"{image_path}"
        )

    # -----------------------------------------------------
    # Image fingerprint
    # -----------------------------------------------------

    image_hash = _file_hash(
        image_path
    )

    # -----------------------------------------------------
    # Cache
    # -----------------------------------------------------

    cache = _load_cache(
        document_id
    )

    cached = (
        cache
        .get("charts", {})
        .get(chart_id)
    )

    # if (
    #     cached
    #     and not overwrite
    #     and cached.get("model")
    #     == DEFAULT_MODEL
    #     and cached.get("image_hash")
    #     == image_hash
    # ):

    #     result = dict(
    #         cached
    #     )

    #     result["status"] = "cached"

    #     return result
    if (
        cached
        and not overwrite
        and cached.get(
            "primary_model"
        ) == PRIMARY_MODEL
        and cached.get(
            "fallback_model"
        ) == FALLBACK_MODEL
        and cached.get(
            "image_hash"
        ) == image_hash
    ):
        result = dict(
            cached
        )

        result["status"] = "cached"

        return result

    # # -----------------------------------------------------
    # # Analyze cropped chart first
    # # -----------------------------------------------------

    # attempts = []

    # try:

    #     analysis = _analyze_with_retry(
    #         image_path=image_path,
    #         chart=chart,
    #         full_page=False,
    #     )

    #     attempts.append(
    #         {
    #             "source": "chart_crop",
    #             "success": True,
    #         }
    #     )

    # except Exception as exc:

    #     analysis = None

    #     attempts.append(
    #         {
    #             "source": "chart_crop",
    #             "success": False,
    #             "error_type": (
    #                 type(exc).__name__
    #             ),
    #             "reason": str(exc),
    #         }
    #     )

    # -----------------------------------------------------
    # Analyze cropped chart
    #
    # Strategy:
    #
    # 1. Cheap model first
    # 2. Stronger model only when the cheap model produces
    #    no usable numerical values
    # -----------------------------------------------------

    attempts = []

    analysis = None
    selected_model = None


    # =====================================================
    # PASS 1 — Primary / cheap model
    # =====================================================

    try:

        primary_analysis = _analyze_with_retry(
            image_path=image_path,
            chart=chart,
            full_page=False,
            model=PRIMARY_MODEL,
        )

        attempts.append(
            {
                "source": "chart_crop",
                "model": PRIMARY_MODEL,
                "success": True,

                "readable_values": (
                    _readable_value_count(
                        primary_analysis
                    )
                ),
            }
        )

        analysis = primary_analysis
        selected_model = PRIMARY_MODEL


    except Exception as exc:

        attempts.append(
            {
                "source": "chart_crop",
                "model": PRIMARY_MODEL,
                "success": False,

                "error_type": (
                    type(exc).__name__
                ),

                "reason": str(exc),
            }
        )


    # =====================================================
    # PASS 2 — Stronger model fallback
    #
    # Only run this when:
    #
    # - primary failed
    # OR
    # - primary saw the chart but extracted zero values
    # =====================================================

    needs_stronger_model = (
        analysis is None
        or _readable_value_count(
            analysis
        ) == 0
    )


    if (
        needs_stronger_model
        and FALLBACK_MODEL
        and FALLBACK_MODEL != PRIMARY_MODEL
    ):

        try:

            fallback_analysis = (
                _analyze_with_retry(
                    image_path=image_path,
                    chart=chart,
                    full_page=False,
                    model=FALLBACK_MODEL,
                )
            )

            fallback_count = (
                _readable_value_count(
                    fallback_analysis
                )
            )

            attempts.append(
                {
                    "source": "chart_crop",
                    "model": FALLBACK_MODEL,
                    "success": True,

                    "readable_values": (
                        fallback_count
                    ),
                }
            )

            # ---------------------------------------------
            # Use fallback only if it actually improves
            # the numerical extraction.
            # ---------------------------------------------

            current_count = (
                _readable_value_count(
                    analysis
                )
                if analysis
                else 0
            )

            if (
                analysis is None
                or fallback_count
                > current_count
            ):
                analysis = (
                    fallback_analysis
                )

                selected_model = (
                    FALLBACK_MODEL
                )


        except Exception as exc:

            attempts.append(
                {
                    "source": "chart_crop",
                    "model": FALLBACK_MODEL,
                    "success": False,

                    "error_type": (
                        type(exc).__name__
                    ),

                    "reason": str(exc),
                }
            )

    # -----------------------------------------------------
    # Optional whole-page fallback
    #
    # Use only when:
    #   - crop failed
    #   - OR crop produced no readable values
    # -----------------------------------------------------

    needs_page_fallback = (
        analysis is None
        or not _has_readable_values(
            analysis
        )
    )

    if (
        needs_page_fallback
        and page_image_path is not None
    ):

        full_page = Path(
            page_image_path
        )

        if full_page.exists():

            try:

                # page_analysis = (
                #     _analyze_with_retry(
                #         image_path=full_page,
                #         chart=chart,
                #         full_page=True,
                #     )
                # )
                page_model = (
                    FALLBACK_MODEL
                    or PRIMARY_MODEL
                )

                page_analysis = _analyze_with_retry(
                    image_path=full_page,
                    chart=chart,
                    full_page=True,
                    model=page_model,
                )

                # attempts.append(
                #     {
                #         "source": "full_page",
                #         "success": True,
                #     }
                # )
                attempts.append(
                    {
                        "source": "full_page",
                        "model": page_model,
                        "success": True,
                    }
                )

                # Prefer page result only if it actually
                # provides more useful values.
                if (
                    analysis is None
                    or _readable_value_count(
                        page_analysis
                    )
                    >
                    _readable_value_count(
                        analysis
                    )
                ):
                    analysis = (
                        page_analysis
                    )

            except Exception as exc:

                attempts.append(
                    {
                        "source": "full_page",
                        "success": False,
                        "error_type": (
                            type(exc).__name__
                        ),
                        "reason": str(exc),
                    }
                )

    # -----------------------------------------------------
    # Both analysis routes failed
    # -----------------------------------------------------

    if analysis is None:

        return {
            "success": False,

            "status": "failed",

            "document_id": (
                document_id
            ),

            "chart_id": (
                chart_id
            ),

            "page": page,

            "analysis": None,

            # Important:
            # preserve chart detection but do not invent
            # financial values.
            "facts": [],

            "attempts": attempts,

            "reason": (
                "Chart interpretation failed."
            ),
        }
    # -----------------------------------------------------
    # Analysis quality warning
    # -----------------------------------------------------

    if (
        analysis.chart_type
        in {
            "bar",
            "line",
            "area",
            "waterfall",
            "combo",
            "pie",
            "donut",
        }
        and not analysis.series
    ):
        analysis.warnings.append(
            "Chart was recognized but no data series "
            "were extracted."
        )
    # -----------------------------------------------------
    # Convert structured chart into fusion facts
    # -----------------------------------------------------

    facts = _build_facts(
        analysis=analysis,
        chart=chart,
    )
    readable_value_count = (
        _readable_value_count(
            analysis
        )
    )

    usable_for_numeric_verification = (
        readable_value_count > 0
    )

    result = {
        "success": True,

        # "status": "analyzed",
        "status": (
            "analyzed"
            if usable_for_numeric_verification
            else "analyzed_no_values"
        ),
        "usable_for_numeric_verification": (
            usable_for_numeric_verification
        ),

        "document_id": (
            document_id
        ),

        "chart_id": (
            chart_id
        ),

        "page": page,

        # "model": (
        #     DEFAULT_MODEL
        # ),
        "primary_model": (
            PRIMARY_MODEL
        ),

        "fallback_model": (
            FALLBACK_MODEL
        ),

        "selected_model": (
            selected_model
        ),

        "image_path": str(
            image_path
        ),

        "image_hash": (
            image_hash
        ),

        "analysis": (
            analysis.model_dump(
                mode="json"
            )
        ),

        "facts": facts,

        # "readable_value_count": (
        #     _readable_value_count(
        #         analysis
        #     )
        # ),

        "readable_value_count": (
            readable_value_count
        ),

        "attempts": attempts,
    }

    # -----------------------------------------------------
    # Separate chart-analysis cache
    # -----------------------------------------------------

    cache.setdefault(
        "charts",
        {},
    )[chart_id] = result

    _save_cache(
        document_id,
        cache,
    )

    return result


def analyze_charts(
    charts: list[dict[str, Any]],
    *,
    overwrite: bool = False,
    page_images: dict[int, str | Path] | None = None,
) -> dict[str, Any]:
    """
    Analyze multiple detected charts.

    One chart failure does not stop other charts.
    """

    page_images = (
        page_images
        or {}
    )

    results = []

    for chart in charts:

        page = (
            chart.get("page")
            or chart.get(
                "page_number"
            )
        )

        try:

            result = analyze_chart(
                chart,
                overwrite=overwrite,
                page_image_path=(
                    page_images.get(
                        page
                    )
                ),
            )

        except Exception as exc:

            result = {
                "success": False,

                "status": "failed",

                "chart_id": (
                    chart.get(
                        "chart_id"
                    )
                ),

                "page": page,

                "facts": [],

                "reason": str(
                    exc
                ),

                "error_type": (
                    type(exc).__name__
                ),
            }

        results.append(
            result
        )

    all_facts = []

    for result in results:

        all_facts.extend(
            result.get(
                "facts",
                [],
            )
        )

    return {
        "results": results,

        "facts": all_facts,

        "chart_count": len(
            charts
        ),

        "analyzed_count": sum(
            result.get("success")
            is True
            for result in results
        ),

        "failed_count": sum(
            result.get("success")
            is False
            for result in results
        ),
    }


# =========================================================
# Gemini analysis
# =========================================================


# def _analyze_with_gemini(
#     *,
#     image_path: Path,
#     chart: dict[str, Any],
#     full_page: bool,
# ) -> ChartAnalysisResponse:
def _analyze_with_gemini(
    *,
    image_path: Path,
    chart: dict[str, Any],
    full_page: bool,
    model: str,
) -> ChartAnalysisResponse:
    """
    Read financial chart data using Gemini Vision.
    """

    api_key = os.getenv(
        "GOOGLE_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not configured."
        )

    client = genai.Client(
        api_key=api_key
    )

    image_bytes = (
        image_path.read_bytes()
    )

    encoded_image = (
        base64.b64encode(
            image_bytes
        )
        .decode("utf-8")
    )

    prompt = _build_prompt(
        chart=chart,
        full_page=full_page,
    )

    interaction = (
        client.interactions.create(
            # model=DEFAULT_MODEL,
            model=model,
            # input=[
            #     {
            #         "type": "image",
            #         "mime_type": (
            #             "image/png"
            #         ),
            #         "data": (
            #             encoded_image
            #         ),
            #     },
            #     {
            #         "type": "text",
            #         "text": prompt,
            #     },
            # ],

            input=[
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "image",
                    "mime_type": "image/png",
                    "data": encoded_image,
                    "resolution": "high",
                },
            ],

            response_format={
                "type": "text",

                "mime_type": (
                    "application/json"
                ),

                # "schema": (
                #     ChartAnalysisResponse
                #     .model_json_schema()
                # ),
                "schema": (
                    RawChartExtraction
                    .model_json_schema()
                ),
            },

            store=False,
        )
    )

    raw = (
        interaction.output_text
    )


    if not raw:
        raise RuntimeError(
            "Gemini returned an empty "
            "chart-analysis response."
        )
    print("\n" + "=" * 60)
    print(f"RAW CHART RESPONSE — {model}")
    print("=" * 60)
    print(raw)

    try:

        # return (
        #     ChartAnalysisResponse
        #     .model_validate_json(
        #         raw
        #     )
        # )
        extracted = (
            RawChartExtraction
            .model_validate_json(
                raw
            )
        )

        print("\nPARSED DATA POINTS:")

        print(
            json.dumps(
                [
                    point.model_dump()
                    for point
                    in extracted.data_points
                ],
                indent=2,
            )
        )

        return _normalize_raw_extraction(
            extracted
        )

    except Exception as exc:

        raise RuntimeError(
            "Gemini returned invalid "
            "structured chart data."
        ) from exc


# def _analyze_with_retry(
#     *,
#     image_path: Path,
#     chart: dict[str, Any],
#     full_page: bool,
#     max_attempts: int = 3,
# ) -> ChartAnalysisResponse:
#     """
#     Retry only temporary Gemini errors.
#     """

#     last_error = None

#     for attempt in range(
#         max_attempts
#     ):

#         try:

#             return (
#                 _analyze_with_gemini(
#                     image_path=image_path,
#                     chart=chart,
#                     full_page=full_page,
#                 )
#             )

#         except Exception as exc:

#             last_error = exc

#             error_text = (
#                 str(exc).upper()
#             )

#             transient = (
#                 "503" in error_text
#                 or "UNAVAILABLE"
#                 in error_text
#                 or "429"
#                 in error_text
#                 or "RESOURCE_EXHAUSTED"
#                 in error_text
#             )

#             if not transient:
#                 raise

#             if (
#                 attempt
#                 == max_attempts - 1
#             ):
#                 break

#             delay = 2 ** attempt

#             print(
#                 "Gemini chart analysis "
#                 "temporarily unavailable. "
#                 f"Retrying in {delay}s..."
#             )

#             time.sleep(
#                 delay
#             )

#     raise RuntimeError(
#         "Gemini chart analysis failed "
#         f"after retries: {last_error}"
#     )

# def _analyze_with_retry(
#     *,
#     image_path: Path,
#     chart: dict[str, Any],
#     full_page: bool,
#     max_attempts: int = 3,
# ) -> ChartAnalysisResponse:

def _analyze_with_retry(
    *,
    image_path: Path,
    chart: dict[str, Any],
    full_page: bool,
    model: str,
    max_attempts: int = 3,
) -> ChartAnalysisResponse:
    last_error = None

    for attempt in range(max_attempts):

        try:
            # return _analyze_with_gemini(
            #     image_path=image_path,
            #     chart=chart,
            #     full_page=full_page,
            # )
            return _analyze_with_gemini(
                image_path=image_path,
                chart=chart,
                full_page=full_page,
                model=model,
            )
        except Exception as exc:

            last_error = exc

            error_text = str(exc)
            error_upper = error_text.upper()

            # -----------------------------------------
            # Daily/project quota exhaustion
            #
            # Retrying immediately is pointless.
            # -----------------------------------------

            quota_exhausted = (
                "REQUESTS PER DAY" in error_upper
                or "PER DAY" in error_upper
                or "FREE TIER" in error_upper
            )

            if quota_exhausted:
                # raise RuntimeError(
                #     "Gemini daily quota exhausted for "
                #     f"{DEFAULT_MODEL}: {exc}"
                # ) from exc
                raise RuntimeError(
                    f"Gemini chart analysis failed for {model} "
                    f"after retries: {last_error}"
                )

            # -----------------------------------------
            # Temporary errors
            # -----------------------------------------

            transient = (
                "503" in error_upper
                or "UNAVAILABLE" in error_upper
                or "429" in error_upper
                or "RESOURCE_EXHAUSTED" in error_upper
            )

            if not transient:
                raise

            if attempt == max_attempts - 1:
                break

            delay = 2 ** attempt

            print(
                "Gemini chart analysis temporarily unavailable. "
                f"Retrying in {delay}s..."
            )

            time.sleep(delay)

    raise RuntimeError(
        "Gemini chart analysis failed after retries: "
        f"{last_error}"
    )

def _normalize_raw_extraction(
    raw: RawChartExtraction,
) -> ChartAnalysisResponse:
    """
    Convert Gemini's flat visual extraction into the richer
    series-based structure used by the rest of the project.
    """

    groups: dict[
        tuple[
            str | None,
            str | None,
            str | None,
        ],
        list[ChartPoint],
    ] = {}

    for point in raw.data_points:

        # ---------------------------------------------
        # A chart does NOT need a legend to have
        # a series.
        # ---------------------------------------------

        series_name = (
            point.series_name
            or raw.title
            or (
                raw.y_axis.label
                if raw.y_axis
                else None
            )
        )

        key = (
            series_name,
            point.metric,
            point.segment,
        )

        groups.setdefault(
            key,
            [],
        ).append(
            ChartPoint(
                period=point.period,
                category=point.category,
                value=point.value,
                display_value=(
                    point.display_value
                ),
                value_source=(
                    point.value_source
                ),
            )
        )

    series = []

    for (
        series_name,
        metric,
        segment,
    ), values in groups.items():

        series.append(
            ChartSeries(
                name=series_name,
                metric=metric,
                segment=segment,
                values=values,
            )
        )

    return ChartAnalysisResponse(
        chart_type=raw.chart_type,
        title=raw.title,
        display_unit=raw.display_unit,
        normalized_unit=raw.normalized_unit,
        x_axis=raw.x_axis,
        y_axis=raw.y_axis,
        legend=raw.legend,
        series=series,
        warnings=raw.warnings,
    )

# =========================================================
# Prompt
# =========================================================

# ==================================================
# SERIES RULES
# ==================================================

# For each series:

# - preserve its visible name
# - identify the financial metric when clearly possible
# - identify the financial segment when clearly possible
# - otherwise use null

# Example:

# Series name:
# "International"

# Metric:
# "revenue"

# Segment:
# "international"

# Do NOT infer metric or segment if the chart does not support it.

# ==================================================
# SERIES AND DATA-POINT RULES
# ==================================================

# A "series" means the data represented by visible graphical marks
# such as bars, lines, points, areas, or pie slices.

# A legend is NOT required for a series to exist.

# IMPORTANT:

# If visible bars, lines, points, areas, or pie slices exist,
# do NOT return an empty series list merely because there is no legend.

# For a single-series chart with no legend:

# - create exactly one series
# - use the chart title as the series name when the title clearly
#   describes the measured data
# - otherwise use the y-axis label when appropriate
# - otherwise set the series name to null

# Example:

# Title:
# International Revenue

# Bars:
# 2024 = 10
# 2025 = 11

# Correct output conceptually:

# series = [
#     {{
#         "name": "International Revenue",
#         "metric": "revenue",
#         "segment": "international",
#         "values": [
#             {{
#                 "period": "2024",
#                 "value": 10,
#                 "display_value": "10",
#                 "value_source": "explicit_label"
#             }},
#             {{
#                 "period": "2025",
#                 "value": 11,
#                 "display_value": "11",
#                 "value_source": "explicit_label"
#             }}
#         ]
#     }}
# ]
# For every visible x-axis year/category that has a corresponding
# graphical data mark, create a ChartPoint.

# If an exact number is printed above, inside, beside, or near the
# corresponding bar/point:

#     value_source = "explicit_label"

# If no exact number is printed but the value can reasonably be read
# from the axis:

#     value_source = "estimated_from_axis"

# If the graphical mark exists but its value cannot be reliably read:

#     value = null
#     value_source = "unreadable"

# Do not omit visible bars merely because their values are difficult
# to read.

def _build_prompt(
    *,
    chart: dict[str, Any],
    full_page: bool,
) -> str:

    detected_type = (
        chart.get(
            "chart_type"
        )
        or "unknown"
    )

    title_hint = (
        chart.get("title")
        or ""
    )

    bbox = (
        chart.get("bbox")
    )

    if full_page:

        target_instruction = f"""
You are viewing the COMPLETE PDF page.

Analyze ONLY the chart corresponding to this previously detected
chart:

Detected chart type: {detected_type}
Detected title: {title_hint or "unknown"}
Detected bounding box in page-image pixels: {bbox}

Ignore other tables, text, charts, or graphics on the page.
"""

    else:

        target_instruction = f"""
You are viewing a CROPPED image containing one financial chart.

Detector chart type:
{detected_type}

Detector title:
{title_hint or "unknown"}
"""

    return f"""
You are extracting structured financial data from a chart.

{target_instruction}

This is a data-extraction task.

Do NOT summarize the chart.

Do NOT use outside knowledge.

Do NOT calculate values that are not visually supported.

==================================================
REQUIRED INFORMATION
==================================================

Extract when visible:

- chart type
- chart title
- x-axis label
- x-axis values
- y-axis label
- y-axis unit
- legend
- data series
- years / periods
- categories
- numerical values

==================================================
NUMERICAL RULES
==================================================

For each numerical data point:

1. If the exact value is explicitly printed on the chart:

   value_source = "explicit_label"

2. If no explicit value is printed but the value can reasonably be
   read from the axis/grid:

   value_source = "estimated_from_axis"

3. If the value cannot be read reliably:

   value = null
   value_source = "unreadable"

NEVER guess an unreadable number.

NEVER convert an unreadable point into zero.

==================================================
UNIT RULES
==================================================

Preserve the visible unit exactly in:

display_unit

Examples:

"$ billions"
"€ millions"
"%"

When clearly identifiable, also provide a normalized form in:

normalized_unit

Examples:

"USD billion"
"EUR million"
"percent"

Do not invent a currency that is not visible or clearly indicated.


==================================================
DATA-POINT EXTRACTION
==================================================

Find every visible graphical data mark in the chart.

Examples include:

- each bar in a bar chart
- each point in a line chart
- each slice in a pie chart
- each segment in a stacked bar
- each bar and line point in a combo chart

Create one data_points entry for every visible data mark.

A legend is NOT required.

For a single-series bar chart with:

2024 -> 10
2025 -> 11

you must return two data points.

If an exact value is visibly printed:

value_source = "explicit_label"

If no value is printed but it can reasonably be measured
from the axis:

value_source = "estimated_from_axis"

If it cannot be read reliably:

value = null
value_source = "unreadable"

Do not return an empty data_points list when clearly visible
bars, points, or slices exist unless none of their values can
be interpreted at all.


==================================================
PIE / DONUT RULES
==================================================

Pie and donut charts may have:

x_axis = null
y_axis = null

Represent slices using:

category
value

==================================================
IMPORTANT
==================================================

Return only information visible or reasonably measurable from the
provided image.

If something is unclear, use null or an empty list rather than
inventing it.
""".strip()


# =========================================================
# Convert chart analysis into Evidence Fusion facts
# =========================================================


def _build_facts(
    *,
    analysis: ChartAnalysisResponse,
    chart: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Convert visual chart values into the fact structure already
    supported by evidence_fusion.py.

    Example:

        {
            "metric": "revenue",
            "segment": "international",
            "period": "2025",
            "value": 11,
            "unit": "USD billion",
            "value_source": "explicit_label"
        }
    """

    facts = []

    # unit = (
    #     analysis.normalized_unit
    #     or analysis.display_unit
    # )

    # Values retain the chart's displayed scale; the unit must retain it too.
    unit = (
        analysis.display_unit
        or (
            analysis.y_axis.unit
            if analysis.y_axis
            else None
        )
        or analysis.normalized_unit
    )
    for series in analysis.series:

        for point in series.values:

            # Unreadable points NEVER become evidence numbers.
            if point.value is None:
                continue

            fact = {
                "metric": (
                    series.metric
                ),

                "segment": (
                    series.segment
                ),

                "period": (
                    point.period
                ),

                "category": (
                    point.category
                ),

                "value": (
                    point.value
                ),

                "display_value": (
                    point.display_value
                ),

                "unit": unit,

                "value_source": (
                    point.value_source
                ),

                "chart_id": (
                    chart.get(
                        "chart_id"
                    )
                ),

                "page": (
                    chart.get("page")
                    or chart.get(
                        "page_number"
                    )
                ),

                "evidence_id": (
                    chart
                    .get(
                        "evidence",
                        {},
                    )
                    .get(
                        "evidence_id"
                    )
                ),
            }

            facts.append(
                fact
            )

    return facts


# =========================================================
# Analysis quality helpers
# =========================================================


def _has_readable_values(
    analysis: ChartAnalysisResponse,
) -> bool:

    return (
        _readable_value_count(
            analysis
        )
        > 0
    )


def _readable_value_count(
    analysis: ChartAnalysisResponse,
) -> int:

    count = 0

    for series in analysis.series:

        for point in series.values:

            if point.value is not None:
                count += 1

    return count


# =========================================================
# Cache
# =========================================================


def _cache_path(
    document_id: str,
) -> Path:

    return (
        PROCESSED_ROOT
        / document_id
        / "chart_analysis.json"
    )


def _load_cache(
    document_id: str,
) -> dict[str, Any]:

    path = _cache_path(
        document_id
    )

    if path.exists():

        try:

            with path.open(
                "r",
                encoding="utf-8",
            ) as file:

                data = json.load(
                    file
                )

            if isinstance(
                data,
                dict,
            ):

                data.setdefault(
                    "charts",
                    {},
                )

                return data

        except (
            OSError,
            json.JSONDecodeError,
        ):
            pass

    return {
        "cache_version": (
            CACHE_VERSION
        ),

        "document_id": (
            document_id
        ),

        "charts": {},
    }


def _save_cache(
    document_id: str,
    cache: dict[str, Any],
) -> None:

    path = _cache_path(
        document_id
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = (
        path.with_suffix(
            ".tmp"
        )
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            cache,
            file,
            indent=2,
            ensure_ascii=False,
        )

    temporary.replace(
        path
    )


# =========================================================
# Utilities
# =========================================================


def _file_hash(
    path: Path,
) -> str:
    """
    Hash chart image so the cache is invalidated if the crop changes.
    """

    digest = hashlib.sha256()

    with path.open(
        "rb"
    ) as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(
                chunk
            )

    return digest.hexdigest()
# from __future__ import annotations

# import base64
# import hashlib
# import json
# import os
# import time
# from pathlib import Path
# from typing import Any, Literal
# from dotenv import load_dotenv
# from google import genai
# from pydantic import BaseModel, Field


# load_dotenv()


# # =========================================================
# # Configuration
# # =========================================================

# PROCESSED_ROOT = Path("data/processed")

# CACHE_VERSION = 1

# # DEFAULT_MODEL = os.getenv(
# #     "GEMINI_VISION_MODEL",
# #     "gemini-flash-latest",
# # )
# # DEFAULT_MODEL = os.getenv(
# #     "GEMINI_CHART_ANALYSIS_MODEL",
# #     "gemini-3.5-flash-lite",
# # )

# PRIMARY_MODEL = os.getenv(
#     "GEMINI_CHART_ANALYSIS_MODEL",
#     "gemini-3.5-flash-lite",
# )


# FALLBACK_MODEL = os.getenv(
#     "GEMINI_CHART_ANALYSIS_FALLBACK_MODEL",
#     "gemini-3.5-flash",
# )
# ChartType = Literal[
#     "bar",
#     "line",
#     "pie",
#     "donut",
#     "area",
#     "waterfall",
#     "combo",
#     "other_financial_chart",
#     "unknown",
# ]


# ValueSource = Literal[
#     "explicit_label",
#     "estimated_from_axis",
#     "unreadable",
# ]


# # =========================================================
# # Structured Gemini output
# # =========================================================


# class AxisInfo(BaseModel):
#     """
#     Information about one visible chart axis.

#     Pie/donut charts may not have axes.
#     """

#     label: str | None = None

#     unit: str | None = None

#     values: list[str] = Field(
#         default_factory=list
#     )

# class ExtractedChartPoint(BaseModel):
#     """
#     One visual data point extracted directly from the chart.
#     """

#     series_name: str | None = Field(
#         default=None,
#         description=(
#             "Visible series or legend name. "
#             "For a single-series chart with no legend, use the "
#             "chart title when it describes the series."
#         ),
#     )

#     metric: str | None = Field(
#         default=None,
#         description=(
#             "Financial metric such as revenue, net income, "
#             "margin, expenses, or cash flow. Null if unclear."
#         ),
#     )

#     segment: str | None = Field(
#         default=None,
#         description=(
#             "Business/geographic segment if clearly visible. "
#             "Null if unclear."
#         ),
#     )

#     period: str | None = Field(
#         default=None,
#         description=(
#             "Time period such as 2024, Q1 2026, or June 2026."
#         ),
#     )

#     category: str | None = Field(
#         default=None,
#         description=(
#             "Non-time category represented by this point."
#         ),
#     )

#     value: float | None = Field(
#         default=None,
#         description=(
#             "Numeric chart value. Use null if it cannot "
#             "be read reliably."
#         ),
#     )

#     display_value: str | None = Field(
#         default=None,
#         description=(
#             "Exact visible value label such as '$11B' or '25%'."
#         ),
#     )

#     value_source: ValueSource = Field(
#         description=(
#             "explicit_label if the number is printed, "
#             "estimated_from_axis if visually estimated, "
#             "unreadable if not reliable."
#         )
#     )


# class RawChartExtraction(BaseModel):
#     """
#     Simple schema Gemini produces directly.
#     """

#     chart_type: ChartType

#     title: str | None = None

#     display_unit: str | None = None

#     normalized_unit: str | None = None

#     x_axis: AxisInfo | None = None

#     y_axis: AxisInfo | None = None

#     legend: list[str] = Field(
#         default_factory=list
#     )

#     data_points: list[ExtractedChartPoint] = Field(
#         default_factory=list,
#         description=(
#             "One entry for every visible bar, line point, "
#             "pie slice, or other graphical data mark."
#         ),
#     )

#     warnings: list[str] = Field(
#         default_factory=list
#     )

# class ChartPoint(BaseModel):
#     """
#     One extracted data point.
#     """

#     # For time-based charts.
#     period: str | None = None

#     # For categorical charts / pie slices.
#     category: str | None = None

#     # Numeric value.
#     #
#     # MUST be None when the value cannot be read
#     # reliably.
#     value: float | None = None

#     # Exact visible text if available.
#     #
#     # Example:
#     # "$11B"
#     # "25%"
#     display_value: str | None = None

#     value_source: ValueSource = (
#         "unreadable"
#     )


# class ChartSeries(BaseModel):
#     """
#     One chart series.

#     Examples:
#         International
#         North America
#         Revenue
#         Operating Margin
#     """

#     name: str | None = None

#     # Financial metric represented by this series.
#     #
#     # Example:
#     # revenue
#     # operating margin
#     # net income
#     #
#     # Leave None if unclear.
#     metric: str | None = None

#     # Optional segment.
#     #
#     # Example:
#     # international
#     # north america
#     #
#     # Leave None if unclear.
#     segment: str | None = None

#     values: list[ChartPoint] = Field(
#         default_factory=list
#     )


# class ChartAnalysisResponse(BaseModel):
#     """
#     Complete structured interpretation of one chart.
#     """

#     chart_type: ChartType

#     title: str | None = None

#     # Unit exactly as displayed.
#     #
#     # Example:
#     # "$ billions"
#     display_unit: str | None = None

#     # Normalized representation when clearly identifiable.
#     #
#     # Example:
#     # "USD billion"
#     normalized_unit: str | None = None

#     x_axis: AxisInfo | None = None

#     y_axis: AxisInfo | None = None

#     legend: list[str] = Field(
#         default_factory=list
#     )

#     series: list[ChartSeries] = Field(
#         default_factory=list
#     )

#     warnings: list[str] = Field(
#         default_factory=list
#     )


# # =========================================================
# # Public API
# # =========================================================


# def analyze_chart(
#     chart: dict[str, Any],
#     *,
#     overwrite: bool = False,
#     page_image_path: str | Path | None = None,
# ) -> dict[str, Any]:
#     """
#     Analyze one chart produced by chart_detector.py.

#     Primary input:
#         cropped chart image

#     Optional fallback:
#         full page image

#     Expected chart input:

#         {
#             "chart_id": "chart_012_001",
#             "document_id": "...",
#             "page": 12,
#             "chart_type": "bar",
#             "title": "...",
#             "bbox": [...],
#             "image_path": "data/charts/...png"
#         }

#     Returns:

#         structured chart data
#         +
#         normalized facts
#     """

#     chart_id = chart.get(
#         "chart_id"
#     )

#     if not chart_id:
#         raise ValueError(
#             "chart must contain chart_id."
#         )

#     document_id = chart.get(
#         "document_id"
#     )

#     if not document_id:
#         raise ValueError(
#             "chart must contain document_id."
#         )

#     page = (
#         chart.get("page")
#         or chart.get("page_number")
#     )

#     image_path = chart.get(
#         "image_path"
#     )

#     if not image_path:
#         raise ValueError(
#             "chart must contain image_path."
#         )

#     image_path = Path(
#         image_path
#     )

#     if not image_path.exists():
#         raise FileNotFoundError(
#             f"Chart image not found: "
#             f"{image_path}"
#         )

#     # -----------------------------------------------------
#     # Image fingerprint
#     # -----------------------------------------------------

#     image_hash = _file_hash(
#         image_path
#     )

#     # -----------------------------------------------------
#     # Cache
#     # -----------------------------------------------------

#     cache = _load_cache(
#         document_id
#     )

#     cached = (
#         cache
#         .get("charts", {})
#         .get(chart_id)
#     )

#     # if (
#     #     cached
#     #     and not overwrite
#     #     and cached.get("model")
#     #     == DEFAULT_MODEL
#     #     and cached.get("image_hash")
#     #     == image_hash
#     # ):

#     #     result = dict(
#     #         cached
#     #     )

#     #     result["status"] = "cached"

#     #     return result
#     if (
#         cached
#         and not overwrite
#         and cached.get(
#             "primary_model"
#         ) == PRIMARY_MODEL
#         and cached.get(
#             "fallback_model"
#         ) == FALLBACK_MODEL
#         and cached.get(
#             "image_hash"
#         ) == image_hash
#     ):
#         result = dict(
#             cached
#         )

#         result["status"] = "cached"

#         return result

#     # # -----------------------------------------------------
#     # # Analyze cropped chart first
#     # # -----------------------------------------------------

#     # attempts = []

#     # try:

#     #     analysis = _analyze_with_retry(
#     #         image_path=image_path,
#     #         chart=chart,
#     #         full_page=False,
#     #     )

#     #     attempts.append(
#     #         {
#     #             "source": "chart_crop",
#     #             "success": True,
#     #         }
#     #     )

#     # except Exception as exc:

#     #     analysis = None

#     #     attempts.append(
#     #         {
#     #             "source": "chart_crop",
#     #             "success": False,
#     #             "error_type": (
#     #                 type(exc).__name__
#     #             ),
#     #             "reason": str(exc),
#     #         }
#     #     )

#     # -----------------------------------------------------
#     # Analyze cropped chart
#     #
#     # Strategy:
#     #
#     # 1. Cheap model first
#     # 2. Stronger model only when the cheap model produces
#     #    no usable numerical values
#     # -----------------------------------------------------

#     attempts = []

#     analysis = None
#     selected_model = None


#     # =====================================================
#     # PASS 1 — Primary / cheap model
#     # =====================================================

#     try:

#         primary_analysis = _analyze_with_retry(
#             image_path=image_path,
#             chart=chart,
#             full_page=False,
#             model=PRIMARY_MODEL,
#         )

#         attempts.append(
#             {
#                 "source": "chart_crop",
#                 "model": PRIMARY_MODEL,
#                 "success": True,

#                 "readable_values": (
#                     _readable_value_count(
#                         primary_analysis
#                     )
#                 ),
#             }
#         )

#         analysis = primary_analysis
#         selected_model = PRIMARY_MODEL


#     except Exception as exc:

#         attempts.append(
#             {
#                 "source": "chart_crop",
#                 "model": PRIMARY_MODEL,
#                 "success": False,

#                 "error_type": (
#                     type(exc).__name__
#                 ),

#                 "reason": str(exc),
#             }
#         )


#     # =====================================================
#     # PASS 2 — Stronger model fallback
#     #
#     # Only run this when:
#     #
#     # - primary failed
#     # OR
#     # - primary saw the chart but extracted zero values
#     # =====================================================

#     needs_stronger_model = (
#         analysis is None
#         or _readable_value_count(
#             analysis
#         ) == 0
#     )


#     if (
#         needs_stronger_model
#         and FALLBACK_MODEL
#         and FALLBACK_MODEL != PRIMARY_MODEL
#     ):

#         try:

#             fallback_analysis = (
#                 _analyze_with_retry(
#                     image_path=image_path,
#                     chart=chart,
#                     full_page=False,
#                     model=FALLBACK_MODEL,
#                 )
#             )

#             fallback_count = (
#                 _readable_value_count(
#                     fallback_analysis
#                 )
#             )

#             attempts.append(
#                 {
#                     "source": "chart_crop",
#                     "model": FALLBACK_MODEL,
#                     "success": True,

#                     "readable_values": (
#                         fallback_count
#                     ),
#                 }
#             )

#             # ---------------------------------------------
#             # Use fallback only if it actually improves
#             # the numerical extraction.
#             # ---------------------------------------------

#             current_count = (
#                 _readable_value_count(
#                     analysis
#                 )
#                 if analysis
#                 else 0
#             )

#             if (
#                 analysis is None
#                 or fallback_count
#                 > current_count
#             ):
#                 analysis = (
#                     fallback_analysis
#                 )

#                 selected_model = (
#                     FALLBACK_MODEL
#                 )


#         except Exception as exc:

#             attempts.append(
#                 {
#                     "source": "chart_crop",
#                     "model": FALLBACK_MODEL,
#                     "success": False,

#                     "error_type": (
#                         type(exc).__name__
#                     ),

#                     "reason": str(exc),
#                 }
#             )

#     # -----------------------------------------------------
#     # Optional whole-page fallback
#     #
#     # Use only when:
#     #   - crop failed
#     #   - OR crop produced no readable values
#     # -----------------------------------------------------

#     needs_page_fallback = (
#         analysis is None
#         or not _has_readable_values(
#             analysis
#         )
#     )

#     if (
#         needs_page_fallback
#         and page_image_path is not None
#     ):

#         full_page = Path(
#             page_image_path
#         )

#         if full_page.exists():

#             try:

#                 # page_analysis = (
#                 #     _analyze_with_retry(
#                 #         image_path=full_page,
#                 #         chart=chart,
#                 #         full_page=True,
#                 #     )
#                 # )
#                 page_model = (
#                     FALLBACK_MODEL
#                     or PRIMARY_MODEL
#                 )

#                 page_analysis = _analyze_with_retry(
#                     image_path=full_page,
#                     chart=chart,
#                     full_page=True,
#                     model=page_model,
#                 )

#                 # attempts.append(
#                 #     {
#                 #         "source": "full_page",
#                 #         "success": True,
#                 #     }
#                 # )
#                 attempts.append(
#                     {
#                         "source": "full_page",
#                         "model": page_model,
#                         "success": True,
#                     }
#                 )

#                 # Prefer page result only if it actually
#                 # provides more useful values.
#                 if (
#                     analysis is None
#                     or _readable_value_count(
#                         page_analysis
#                     )
#                     >
#                     _readable_value_count(
#                         analysis
#                     )
#                 ):
#                     analysis = (
#                         page_analysis
#                     )

#             except Exception as exc:

#                 attempts.append(
#                     {
#                         "source": "full_page",
#                         "success": False,
#                         "error_type": (
#                             type(exc).__name__
#                         ),
#                         "reason": str(exc),
#                     }
#                 )

#     # -----------------------------------------------------
#     # Both analysis routes failed
#     # -----------------------------------------------------

#     if analysis is None:

#         return {
#             "success": False,

#             "status": "failed",

#             "document_id": (
#                 document_id
#             ),

#             "chart_id": (
#                 chart_id
#             ),

#             "page": page,

#             "analysis": None,

#             # Important:
#             # preserve chart detection but do not invent
#             # financial values.
#             "facts": [],

#             "attempts": attempts,

#             "reason": (
#                 "Chart interpretation failed."
#             ),
#         }
#     # -----------------------------------------------------
#     # Analysis quality warning
#     # -----------------------------------------------------

#     if (
#         analysis.chart_type
#         in {
#             "bar",
#             "line",
#             "area",
#             "waterfall",
#             "combo",
#             "pie",
#             "donut",
#         }
#         and not analysis.series
#     ):
#         analysis.warnings.append(
#             "Chart was recognized but no data series "
#             "were extracted."
#         )
#     # -----------------------------------------------------
#     # Convert structured chart into fusion facts
#     # -----------------------------------------------------

#     facts = _build_facts(
#         analysis=analysis,
#         chart=chart,
#     )
#     readable_value_count = (
#         _readable_value_count(
#             analysis
#         )
#     )

#     usable_for_numeric_verification = (
#         readable_value_count > 0
#     )

#     result = {
#         "success": True,

#         # "status": "analyzed",
#         "status": (
#             "analyzed"
#             if usable_for_numeric_verification
#             else "analyzed_no_values"
#         ),
#         "usable_for_numeric_verification": (
#             usable_for_numeric_verification
#         ),

#         "document_id": (
#             document_id
#         ),

#         "chart_id": (
#             chart_id
#         ),

#         "page": page,

#         # "model": (
#         #     DEFAULT_MODEL
#         # ),
#         "primary_model": (
#             PRIMARY_MODEL
#         ),

#         "fallback_model": (
#             FALLBACK_MODEL
#         ),

#         "selected_model": (
#             selected_model
#         ),

#         "image_path": str(
#             image_path
#         ),

#         "image_hash": (
#             image_hash
#         ),

#         "analysis": (
#             analysis.model_dump(
#                 mode="json"
#             )
#         ),

#         "facts": facts,

#         # "readable_value_count": (
#         #     _readable_value_count(
#         #         analysis
#         #     )
#         # ),

#         "readable_value_count": (
#             readable_value_count
#         ),

#         "attempts": attempts,
#     }

#     # -----------------------------------------------------
#     # Separate chart-analysis cache
#     # -----------------------------------------------------

#     cache.setdefault(
#         "charts",
#         {},
#     )[chart_id] = result

#     _save_cache(
#         document_id,
#         cache,
#     )

#     return result


# def analyze_charts(
#     charts: list[dict[str, Any]],
#     *,
#     overwrite: bool = False,
#     page_images: dict[int, str | Path] | None = None,
# ) -> dict[str, Any]:
#     """
#     Analyze multiple detected charts.

#     One chart failure does not stop other charts.
#     """

#     page_images = (
#         page_images
#         or {}
#     )

#     results = []

#     for chart in charts:

#         page = (
#             chart.get("page")
#             or chart.get(
#                 "page_number"
#             )
#         )

#         try:

#             result = analyze_chart(
#                 chart,
#                 overwrite=overwrite,
#                 page_image_path=(
#                     page_images.get(
#                         page
#                     )
#                 ),
#             )

#         except Exception as exc:

#             result = {
#                 "success": False,

#                 "status": "failed",

#                 "chart_id": (
#                     chart.get(
#                         "chart_id"
#                     )
#                 ),

#                 "page": page,

#                 "facts": [],

#                 "reason": str(
#                     exc
#                 ),

#                 "error_type": (
#                     type(exc).__name__
#                 ),
#             }

#         results.append(
#             result
#         )

#     all_facts = []

#     for result in results:

#         all_facts.extend(
#             result.get(
#                 "facts",
#                 [],
#             )
#         )

#     return {
#         "results": results,

#         "facts": all_facts,

#         "chart_count": len(
#             charts
#         ),

#         "analyzed_count": sum(
#             result.get("success")
#             is True
#             for result in results
#         ),

#         "failed_count": sum(
#             result.get("success")
#             is False
#             for result in results
#         ),
#     }


# # =========================================================
# # Gemini analysis
# # =========================================================


# # def _analyze_with_gemini(
# #     *,
# #     image_path: Path,
# #     chart: dict[str, Any],
# #     full_page: bool,
# # ) -> ChartAnalysisResponse:
# def _analyze_with_gemini(
#     *,
#     image_path: Path,
#     chart: dict[str, Any],
#     full_page: bool,
#     model: str,
# ) -> ChartAnalysisResponse:
#     """
#     Read financial chart data using Gemini Vision.
#     """

#     api_key = os.getenv(
#         "GOOGLE_API_KEY"
#     )

#     if not api_key:
#         raise RuntimeError(
#             "GOOGLE_API_KEY is not configured."
#         )

#     client = genai.Client(
#         api_key=api_key
#     )

#     image_bytes = (
#         image_path.read_bytes()
#     )

#     encoded_image = (
#         base64.b64encode(
#             image_bytes
#         )
#         .decode("utf-8")
#     )

#     prompt = _build_prompt(
#         chart=chart,
#         full_page=full_page,
#     )

#     interaction = (
#         client.interactions.create(
#             # model=DEFAULT_MODEL,
#             model=model,
#             # input=[
#             #     {
#             #         "type": "image",
#             #         "mime_type": (
#             #             "image/png"
#             #         ),
#             #         "data": (
#             #             encoded_image
#             #         ),
#             #     },
#             #     {
#             #         "type": "text",
#             #         "text": prompt,
#             #     },
#             # ],

#             input=[
#                 {
#                     "type": "text",
#                     "text": prompt,
#                 },
#                 {
#                     "type": "image",
#                     "mime_type": "image/png",
#                     "data": encoded_image,
#                     "resolution": "high",
#                 },
#             ],

#             response_format={
#                 "type": "text",

#                 "mime_type": (
#                     "application/json"
#                 ),

#                 # "schema": (
#                 #     ChartAnalysisResponse
#                 #     .model_json_schema()
#                 # ),
#                 "schema": (
#                     RawChartExtraction
#                     .model_json_schema()
#                 ),
#             },

#             store=False,
#         )
#     )

#     raw = (
#         interaction.output_text
#     )


#     if not raw:
#         raise RuntimeError(
#             "Gemini returned an empty "
#             "chart-analysis response."
#         )
#     print("\n" + "=" * 60)
#     print(f"RAW CHART RESPONSE — {model}")
#     print("=" * 60)
#     print(raw)

#     try:

#         # return (
#         #     ChartAnalysisResponse
#         #     .model_validate_json(
#         #         raw
#         #     )
#         # )
#         extracted = (
#             RawChartExtraction
#             .model_validate_json(
#                 raw
#             )
#         )

#         print("\nPARSED DATA POINTS:")

#         print(
#             json.dumps(
#                 [
#                     point.model_dump()
#                     for point
#                     in extracted.data_points
#                 ],
#                 indent=2,
#             )
#         )

#         return _normalize_raw_extraction(
#             extracted
#         )

#     except Exception as exc:

#         raise RuntimeError(
#             "Gemini returned invalid "
#             "structured chart data."
#         ) from exc


# # def _analyze_with_retry(
# #     *,
# #     image_path: Path,
# #     chart: dict[str, Any],
# #     full_page: bool,
# #     max_attempts: int = 3,
# # ) -> ChartAnalysisResponse:
# #     """
# #     Retry only temporary Gemini errors.
# #     """

# #     last_error = None

# #     for attempt in range(
# #         max_attempts
# #     ):

# #         try:

# #             return (
# #                 _analyze_with_gemini(
# #                     image_path=image_path,
# #                     chart=chart,
# #                     full_page=full_page,
# #                 )
# #             )

# #         except Exception as exc:

# #             last_error = exc

# #             error_text = (
# #                 str(exc).upper()
# #             )

# #             transient = (
# #                 "503" in error_text
# #                 or "UNAVAILABLE"
# #                 in error_text
# #                 or "429"
# #                 in error_text
# #                 or "RESOURCE_EXHAUSTED"
# #                 in error_text
# #             )

# #             if not transient:
# #                 raise

# #             if (
# #                 attempt
# #                 == max_attempts - 1
# #             ):
# #                 break

# #             delay = 2 ** attempt

# #             print(
# #                 "Gemini chart analysis "
# #                 "temporarily unavailable. "
# #                 f"Retrying in {delay}s..."
# #             )

# #             time.sleep(
# #                 delay
# #             )

# #     raise RuntimeError(
# #         "Gemini chart analysis failed "
# #         f"after retries: {last_error}"
# #     )

# # def _analyze_with_retry(
# #     *,
# #     image_path: Path,
# #     chart: dict[str, Any],
# #     full_page: bool,
# #     max_attempts: int = 3,
# # ) -> ChartAnalysisResponse:

# def _analyze_with_retry(
#     *,
#     image_path: Path,
#     chart: dict[str, Any],
#     full_page: bool,
#     model: str,
#     max_attempts: int = 3,
# ) -> ChartAnalysisResponse:
#     last_error = None

#     for attempt in range(max_attempts):

#         try:
#             # return _analyze_with_gemini(
#             #     image_path=image_path,
#             #     chart=chart,
#             #     full_page=full_page,
#             # )
#             return _analyze_with_gemini(
#                 image_path=image_path,
#                 chart=chart,
#                 full_page=full_page,
#                 model=model,
#             )
#         except Exception as exc:

#             last_error = exc

#             error_text = str(exc)
#             error_upper = error_text.upper()

#             # -----------------------------------------
#             # Daily/project quota exhaustion
#             #
#             # Retrying immediately is pointless.
#             # -----------------------------------------

#             quota_exhausted = (
#                 "REQUESTS PER DAY" in error_upper
#                 or "PER DAY" in error_upper
#                 or "FREE TIER" in error_upper
#             )

#             if quota_exhausted:
#                 # raise RuntimeError(
#                 #     "Gemini daily quota exhausted for "
#                 #     f"{DEFAULT_MODEL}: {exc}"
#                 # ) from exc
#                 raise RuntimeError(
#                     f"Gemini chart analysis failed for {model} "
#                     f"after retries: {last_error}"
#                 )

#             # -----------------------------------------
#             # Temporary errors
#             # -----------------------------------------

#             transient = (
#                 "503" in error_upper
#                 or "UNAVAILABLE" in error_upper
#                 or "429" in error_upper
#                 or "RESOURCE_EXHAUSTED" in error_upper
#             )

#             if not transient:
#                 raise

#             if attempt == max_attempts - 1:
#                 break

#             delay = 2 ** attempt

#             print(
#                 "Gemini chart analysis temporarily unavailable. "
#                 f"Retrying in {delay}s..."
#             )

#             time.sleep(delay)

#     raise RuntimeError(
#         "Gemini chart analysis failed after retries: "
#         f"{last_error}"
#     )

# def _normalize_raw_extraction(
#     raw: RawChartExtraction,
# ) -> ChartAnalysisResponse:
#     """
#     Convert Gemini's flat visual extraction into the richer
#     series-based structure used by the rest of the project.
#     """

#     groups: dict[
#         tuple[
#             str | None,
#             str | None,
#             str | None,
#         ],
#         list[ChartPoint],
#     ] = {}

#     for point in raw.data_points:

#         # ---------------------------------------------
#         # A chart does NOT need a legend to have
#         # a series.
#         # ---------------------------------------------

#         series_name = (
#             point.series_name
#             or raw.title
#             or (
#                 raw.y_axis.label
#                 if raw.y_axis
#                 else None
#             )
#         )

#         key = (
#             series_name,
#             point.metric,
#             point.segment,
#         )

#         groups.setdefault(
#             key,
#             [],
#         ).append(
#             ChartPoint(
#                 period=point.period,
#                 category=point.category,
#                 value=point.value,
#                 display_value=(
#                     point.display_value
#                 ),
#                 value_source=(
#                     point.value_source
#                 ),
#             )
#         )

#     series = []

#     for (
#         series_name,
#         metric,
#         segment,
#     ), values in groups.items():

#         series.append(
#             ChartSeries(
#                 name=series_name,
#                 metric=metric,
#                 segment=segment,
#                 values=values,
#             )
#         )

#     return ChartAnalysisResponse(
#         chart_type=raw.chart_type,
#         title=raw.title,
#         display_unit=raw.display_unit,
#         normalized_unit=raw.normalized_unit,
#         x_axis=raw.x_axis,
#         y_axis=raw.y_axis,
#         legend=raw.legend,
#         series=series,
#         warnings=raw.warnings,
#     )

# # =========================================================
# # Prompt
# # =========================================================

# # ==================================================
# # SERIES RULES
# # ==================================================

# # For each series:

# # - preserve its visible name
# # - identify the financial metric when clearly possible
# # - identify the financial segment when clearly possible
# # - otherwise use null

# # Example:

# # Series name:
# # "International"

# # Metric:
# # "revenue"

# # Segment:
# # "international"

# # Do NOT infer metric or segment if the chart does not support it.

# # ==================================================
# # SERIES AND DATA-POINT RULES
# # ==================================================

# # A "series" means the data represented by visible graphical marks
# # such as bars, lines, points, areas, or pie slices.

# # A legend is NOT required for a series to exist.

# # IMPORTANT:

# # If visible bars, lines, points, areas, or pie slices exist,
# # do NOT return an empty series list merely because there is no legend.

# # For a single-series chart with no legend:

# # - create exactly one series
# # - use the chart title as the series name when the title clearly
# #   describes the measured data
# # - otherwise use the y-axis label when appropriate
# # - otherwise set the series name to null

# # Example:

# # Title:
# # International Revenue

# # Bars:
# # 2024 = 10
# # 2025 = 11

# # Correct output conceptually:

# # series = [
# #     {{
# #         "name": "International Revenue",
# #         "metric": "revenue",
# #         "segment": "international",
# #         "values": [
# #             {{
# #                 "period": "2024",
# #                 "value": 10,
# #                 "display_value": "10",
# #                 "value_source": "explicit_label"
# #             }},
# #             {{
# #                 "period": "2025",
# #                 "value": 11,
# #                 "display_value": "11",
# #                 "value_source": "explicit_label"
# #             }}
# #         ]
# #     }}
# # ]
# # For every visible x-axis year/category that has a corresponding
# # graphical data mark, create a ChartPoint.

# # If an exact number is printed above, inside, beside, or near the
# # corresponding bar/point:

# #     value_source = "explicit_label"

# # If no exact number is printed but the value can reasonably be read
# # from the axis:

# #     value_source = "estimated_from_axis"

# # If the graphical mark exists but its value cannot be reliably read:

# #     value = null
# #     value_source = "unreadable"

# # Do not omit visible bars merely because their values are difficult
# # to read.

# def _build_prompt(
#     *,
#     chart: dict[str, Any],
#     full_page: bool,
# ) -> str:

#     detected_type = (
#         chart.get(
#             "chart_type"
#         )
#         or "unknown"
#     )

#     title_hint = (
#         chart.get("title")
#         or ""
#     )

#     bbox = (
#         chart.get("bbox")
#     )

#     if full_page:

#         target_instruction = f"""
# You are viewing the COMPLETE PDF page.

# Analyze ONLY the chart corresponding to this previously detected
# chart:

# Detected chart type: {detected_type}
# Detected title: {title_hint or "unknown"}
# Detected bounding box in page-image pixels: {bbox}

# Ignore other tables, text, charts, or graphics on the page.
# """

#     else:

#         target_instruction = f"""
# You are viewing a CROPPED image containing one financial chart.

# Detector chart type:
# {detected_type}

# Detector title:
# {title_hint or "unknown"}
# """

#     return f"""
# You are extracting structured financial data from a chart.

# {target_instruction}

# This is a data-extraction task.

# Do NOT summarize the chart.

# Do NOT use outside knowledge.

# Do NOT calculate values that are not visually supported.

# ==================================================
# REQUIRED INFORMATION
# ==================================================

# Extract when visible:

# - chart type
# - chart title
# - x-axis label
# - x-axis values
# - y-axis label
# - y-axis unit
# - legend
# - data series
# - years / periods
# - categories
# - numerical values

# ==================================================
# NUMERICAL RULES
# ==================================================

# For each numerical data point:

# 1. If the exact value is explicitly printed on the chart:

#    value_source = "explicit_label"

# 2. If no explicit value is printed but the value can reasonably be
#    read from the axis/grid:

#    value_source = "estimated_from_axis"

# 3. If the value cannot be read reliably:

#    value = null
#    value_source = "unreadable"

# NEVER guess an unreadable number.

# NEVER convert an unreadable point into zero.

# ==================================================
# UNIT RULES
# ==================================================

# Preserve the visible unit exactly in:

# display_unit

# Examples:

# "$ billions"
# "€ millions"
# "%"

# When clearly identifiable, also provide a normalized form in:

# normalized_unit

# Examples:

# "USD billion"
# "EUR million"
# "percent"

# Do not invent a currency that is not visible or clearly indicated.


# ==================================================
# DATA-POINT EXTRACTION
# ==================================================

# Find every visible graphical data mark in the chart.

# Examples include:

# - each bar in a bar chart
# - each point in a line chart
# - each slice in a pie chart
# - each segment in a stacked bar
# - each bar and line point in a combo chart

# Create one data_points entry for every visible data mark.

# A legend is NOT required.

# For a single-series bar chart with:

# 2024 -> 10
# 2025 -> 11

# you must return two data points.

# If an exact value is visibly printed:

# value_source = "explicit_label"

# If no value is printed but it can reasonably be measured
# from the axis:

# value_source = "estimated_from_axis"

# If it cannot be read reliably:

# value = null
# value_source = "unreadable"

# Do not return an empty data_points list when clearly visible
# bars, points, or slices exist unless none of their values can
# be interpreted at all.


# ==================================================
# PIE / DONUT RULES
# ==================================================

# Pie and donut charts may have:

# x_axis = null
# y_axis = null

# Represent slices using:

# category
# value

# ==================================================
# IMPORTANT
# ==================================================

# Return only information visible or reasonably measurable from the
# provided image.

# If something is unclear, use null or an empty list rather than
# inventing it.
# """.strip()


# # =========================================================
# # Convert chart analysis into Evidence Fusion facts
# # =========================================================


# def _build_facts(
#     *,
#     analysis: ChartAnalysisResponse,
#     chart: dict[str, Any],
# ) -> list[dict[str, Any]]:
#     """
#     Convert visual chart values into the fact structure already
#     supported by evidence_fusion.py.

#     Example:

#         {
#             "metric": "revenue",
#             "segment": "international",
#             "period": "2025",
#             "value": 11,
#             "unit": "USD billion",
#             "value_source": "explicit_label"
#         }
#     """

#     facts = []

#     # unit = (
#     #     analysis.normalized_unit
#     #     or analysis.display_unit
#     # )

#     unit = (
#         analysis.normalized_unit
#         or analysis.display_unit
#         or (
#             analysis.y_axis.unit
#             if analysis.y_axis
#             else None
#         )
#     )
#     for series in analysis.series:

#         for point in series.values:

#             # Unreadable points NEVER become evidence numbers.
#             if point.value is None:
#                 continue

#             fact = {
#                 "metric": (
#                     series.metric
#                 ),

#                 "segment": (
#                     series.segment
#                 ),

#                 "period": (
#                     point.period
#                 ),

#                 "category": (
#                     point.category
#                 ),

#                 "value": (
#                     point.value
#                 ),

#                 "display_value": (
#                     point.display_value
#                 ),

#                 "unit": unit,

#                 "value_source": (
#                     point.value_source
#                 ),

#                 "chart_id": (
#                     chart.get(
#                         "chart_id"
#                     )
#                 ),

#                 "page": (
#                     chart.get("page")
#                     or chart.get(
#                         "page_number"
#                     )
#                 ),

#                 "evidence_id": (
#                     chart
#                     .get(
#                         "evidence",
#                         {},
#                     )
#                     .get(
#                         "evidence_id"
#                     )
#                 ),
#             }

#             facts.append(
#                 fact
#             )

#     return facts


# # =========================================================
# # Analysis quality helpers
# # =========================================================


# def _has_readable_values(
#     analysis: ChartAnalysisResponse,
# ) -> bool:

#     return (
#         _readable_value_count(
#             analysis
#         )
#         > 0
#     )


# def _readable_value_count(
#     analysis: ChartAnalysisResponse,
# ) -> int:

#     count = 0

#     for series in analysis.series:

#         for point in series.values:

#             if point.value is not None:
#                 count += 1

#     return count


# # =========================================================
# # Cache
# # =========================================================


# def _cache_path(
#     document_id: str,
# ) -> Path:

#     return (
#         PROCESSED_ROOT
#         / document_id
#         / "chart_analysis.json"
#     )


# def _load_cache(
#     document_id: str,
# ) -> dict[str, Any]:

#     path = _cache_path(
#         document_id
#     )

#     if path.exists():

#         try:

#             with path.open(
#                 "r",
#                 encoding="utf-8",
#             ) as file:

#                 data = json.load(
#                     file
#                 )

#             if isinstance(
#                 data,
#                 dict,
#             ):

#                 data.setdefault(
#                     "charts",
#                     {},
#                 )

#                 return data

#         except (
#             OSError,
#             json.JSONDecodeError,
#         ):
#             pass

#     return {
#         "cache_version": (
#             CACHE_VERSION
#         ),

#         "document_id": (
#             document_id
#         ),

#         "charts": {},
#     }


# def _save_cache(
#     document_id: str,
#     cache: dict[str, Any],
# ) -> None:

#     path = _cache_path(
#         document_id
#     )

#     path.parent.mkdir(
#         parents=True,
#         exist_ok=True,
#     )

#     temporary = (
#         path.with_suffix(
#             ".tmp"
#         )
#     )

#     with temporary.open(
#         "w",
#         encoding="utf-8",
#     ) as file:

#         json.dump(
#             cache,
#             file,
#             indent=2,
#             ensure_ascii=False,
#         )

#     temporary.replace(
#         path
#     )


# # =========================================================
# # Utilities
# # =========================================================


# def _file_hash(
#     path: Path,
# ) -> str:
#     """
#     Hash chart image so the cache is invalidated if the crop changes.
#     """

#     digest = hashlib.sha256()

#     with path.open(
#         "rb"
#     ) as file:

#         while True:

#             chunk = file.read(
#                 1024 * 1024
#             )

#             if not chunk:
#                 break

#             digest.update(
#                 chunk
#             )

#     return digest.hexdigest()