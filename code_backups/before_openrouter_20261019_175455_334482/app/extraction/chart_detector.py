from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Literal

import pymupdf
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field


try:
    from .image_renderer import ensure_page_rendered
except ImportError:
    from image_renderer import ensure_page_rendered


load_dotenv()


# =========================================================
# Configuration
# =========================================================

PROCESSED_ROOT = Path("data/processed")
CHARTS_ROOT = Path("data/charts")

DEFAULT_DPI = 200

# DEFAULT_MODEL = os.getenv(
#     "GEMINI_VISION_MODEL",
#     "gemini-flash-latest",
# )

DEFAULT_MODEL = os.getenv(
    "GEMINI_CHART_DETECTION_MODEL",
    "gemini-3.5-flash-lite",
)

# Ignore very small regions such as icons or legends.
DEFAULT_MIN_AREA_RATIO = 0.04

CHART_CACHE_VERSION = 1


ChartType = Literal[
    "bar",
    "line",
    "pie",
    "donut",
    "area",
    "waterfall",
    "combo",
    "other_financial_chart",
]


# =========================================================
# Gemini schemas
# =========================================================


class ChartCandidate(BaseModel):
    """
    One chart region detected by Gemini.

    Gemini coordinates:

        [ymin, xmin, ymax, xmax]

    normalized from 0 to 1000.
    """

    chart_type: ChartType

    title: str = Field(
        default="",
        description=(
            "Visible chart title if clearly readable. "
            "Empty string if there is no visible title."
        ),
    )

    box_2d: list[int] = Field(
        description=(
            "Bounding box [ymin, xmin, ymax, xmax] "
            "normalized between 0 and 1000."
        )
    )


class ChartDetectionResponse(BaseModel):
    charts: list[ChartCandidate] = Field(
        default_factory=list
    )


# =========================================================
# Public API
# =========================================================


def detect_charts(
    document_id: str,
    page_numbers: Iterable[int],
    *,
    overwrite: bool = False,
    dpi: int = DEFAULT_DPI,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
) -> dict[str, Any]:
    """
    Detect charts only on explicitly requested pages.

    This function does NOT scan the whole document automatically.

    Example:

        detect_charts(
            "Apple_Financial_10_K",
            [5, 10, 15]
        )
    """

    pages = _normalize_page_numbers(
        page_numbers
    )

    results = []

    for page_number in pages:

        try:
            result = detect_charts_on_page(
                document_id=document_id,
                page_number=page_number,
                overwrite=overwrite,
                dpi=dpi,
                min_area_ratio=min_area_ratio,
            )

        except Exception as exc:
            result = {
                "success": False,
                "status": "failed",

                "document_id": document_id,
                "page_number": page_number,

                "charts": [],

                "error_type": (
                    type(exc).__name__
                ),

                "reason": str(exc),
            }

        results.append(
            result
        )

    all_charts = []

    for result in results:
        all_charts.extend(
            result.get(
                "charts",
                [],
            )
        )

    return {
        "document_id": document_id,

        "requested_pages": pages,

        "results": results,

        "charts": all_charts,

        "chart_count": len(
            all_charts
        ),

        "detected_pages": sum(
            bool(
                result.get("charts")
            )
            for result in results
        ),

        "no_chart_pages": sum(
            result.get("status")
            == "no_charts"
            for result in results
        ),

        "cached_pages": sum(
            result.get("status")
            == "cached"
            for result in results
        ),

        "failed_pages": sum(
            not result.get(
                "success",
                False,
            )
            for result in results
        ),
    }


def detect_charts_on_page(
    document_id: str,
    page_number: int,
    *,
    overwrite: bool = False,
    dpi: int = DEFAULT_DPI,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
) -> dict[str, Any]:
    """
    Detect chart regions on one page.

    Responsibilities:

        - render page if necessary
        - ask Gemini where charts are
        - classify common financial chart types
        - discard tiny detections
        - save chart crops
        - create basic chart evidence
        - cache result

    It DOES NOT extract chart values.
    """

    if page_number < 1:
        raise ValueError(
            "Page numbers start at 1."
        )

    if not (
        0.0
        < min_area_ratio
        < 1.0
    ):
        raise ValueError(
            "min_area_ratio must be "
            "between 0 and 1."
        )

    # -----------------------------------------------------
    # Check cache
    # -----------------------------------------------------

    cache = _load_cache(
        document_id
    )

    cached = (
        cache
        .get("pages", {})
        .get(str(page_number))
    )

    if (
        cached
        and not overwrite
        and cached.get("model")
        == DEFAULT_MODEL
        and cached.get("dpi")
        == dpi
        and cached.get(
            "min_area_ratio"
        )
        == min_area_ratio
    ):
        result = dict(
            cached
        )

        result["status"] = (
            "cached"
        )

        return result

    # -----------------------------------------------------
    # Render page only when required.
    # -----------------------------------------------------

    image_path = ensure_page_rendered(
        document_id=document_id,
        page_number=page_number,
        dpi=dpi,
    )

    image_path = Path(
        image_path
    )

    width_px, height_px = (
        _image_dimensions(
            image_path
        )
    )

    # -----------------------------------------------------
    # Gemini chart detection
    # -----------------------------------------------------

    detected = _detect_with_retry(
        image_path=image_path,
    )

    charts = []

    rejected_tiny = 0

    output_directory = (
        CHARTS_ROOT
        / document_id
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -----------------------------------------------------
    # Convert candidate detections
    # -----------------------------------------------------

    for candidate in detected.charts:

        bbox = _normalized_box_to_pixels(
            candidate.box_2d,
            width_px=width_px,
            height_px=height_px,
        )

        area_ratio = _bbox_area_ratio(
            bbox,
            width_px=width_px,
            height_px=height_px,
        )

        # Remove tiny icons / legend fragments / noise.
        if area_ratio < min_area_ratio:
            rejected_tiny += 1
            continue

        charts.append(
            {
                "chart_type": (
                    candidate.chart_type
                ),

                "title": (
                    candidate.title.strip()
                ),

                "bbox": bbox,

                "bbox_space": (
                    "image_pixels"
                ),

                "area_ratio": round(
                    area_ratio,
                    4,
                ),
            }
        )

    # -----------------------------------------------------
    # Remove near-duplicate boxes
    # -----------------------------------------------------

    charts = _deduplicate_regions(
        charts
    )

    # -----------------------------------------------------
    # Assign IDs + save crops
    # -----------------------------------------------------

    final_charts = []

    for index, chart in enumerate(
        charts,
        start=1,
    ):

        chart_id = (
            f"chart_"
            f"{page_number:03d}_"
            f"{index:03d}"
        )

        crop_path = (
            output_directory
            / f"{chart_id}.png"
        )

        _crop_image(
            source_path=image_path,
            bbox=chart["bbox"],
            destination=crop_path,
        )

        evidence_id = (
            f"chart_evidence_"
            f"{document_id}_"
            f"{page_number:03d}_"
            f"{index:03d}"
        )

        title = (
            chart["title"]
            or "Untitled chart"
        )

        evidence = {
            "id": evidence_id,

            "evidence_id": (
                evidence_id
            ),

            "document_id": (
                document_id
            ),

            "page": page_number,

            "page_number": (
                page_number
            ),

            "type": "chart",

            "chart_id": (
                chart_id
            ),

            "chart_type": (
                chart["chart_type"]
            ),

            "content": (
                f"Detected "
                f"{chart['chart_type']} "
                f"chart: {title}"
            ),

            "bbox": (
                chart["bbox"]
            ),

            "bbox_space": (
                "image_pixels"
            ),

            "image_path": str(
                crop_path
            ),

            # vision_client.py will populate this later.
            "facts": [],
        }

        final_charts.append(
            {
                **chart,

                "chart_id": (
                    chart_id
                ),

                "page": (
                    page_number
                ),

                "page_number": (
                    page_number
                ),

                "document_id": (
                    document_id
                ),

                "image_path": str(
                    crop_path
                ),

                "evidence": evidence,

                "facts": [],
            }
        )

    # -----------------------------------------------------
    # Successful "no chart" is still a real result.
    # -----------------------------------------------------

    status = (
        "detected"
        if final_charts
        else "no_charts"
    )

    result = {
        "success": True,

        "status": status,

        "document_id": (
            document_id
        ),

        "page_number": (
            page_number
        ),

        "page_image_path": str(
            image_path
        ),

        "width_px": (
            width_px
        ),

        "height_px": (
            height_px
        ),

        "dpi": dpi,

        "model": (
            DEFAULT_MODEL
        ),

        "min_area_ratio": (
            min_area_ratio
        ),

        "charts": (
            final_charts
        ),

        "chart_count": len(
            final_charts
        ),

        "rejected_tiny_regions": (
            rejected_tiny
        ),
    }

    # -----------------------------------------------------
    # Cache both:
    #
    #   charts found
    #   no charts found
    #
    # We do NOT cache failures.
    # -----------------------------------------------------

    cache.setdefault(
        "pages",
        {},
    )[str(page_number)] = result

    _save_cache(
        document_id,
        cache,
    )

    return result


# =========================================================
# Gemini chart detection
# =========================================================


def _detect_with_gemini(
    *,
    image_path: Path,
) -> ChartDetectionResponse:
    """
    Use Gemini only to locate and classify chart regions.

    Value extraction happens later in vision_client.py.
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
        ).decode(
            "utf-8"
        )
    )

    prompt = """
Detect all genuine financial charts or graphs on this document page.

This task is ONLY chart detection and classification.
Do NOT extract numerical chart values yet.

Detect these chart types:

- bar
- line
- pie
- donut
- area
- waterfall
- combo
- other_financial_chart

A combo chart means a financial chart combining formats such as
bars and lines.

Do NOT detect these as charts:

- tables
- plain text
- logos
- photographs
- decorative graphics
- icons
- legends by themselves
- individual axis labels
- page borders

For every real chart:

1. Classify chart_type.
2. Return the visible chart title if one is clearly present.
3. Return one bounding box covering the COMPLETE chart including:
   - plotting area
   - title
   - axes
   - labels
   - legend when it belongs to the chart

box_2d must be:

[ymin, xmin, ymax, xmax]

Coordinates must be normalized between 0 and 1000.

Do not invent a title.

If the page contains no genuine financial chart, return an empty
charts list.
""".strip()

    interaction = client.interactions.create(
        model=DEFAULT_MODEL,

        input=[
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image",
                "data": encoded_image,
                "mime_type": "image/png",
            },
        ],

        response_format={
            "type": "text",

            "mime_type": (
                "application/json"
            ),

            "schema": (
                ChartDetectionResponse
                .model_json_schema()
            ),
        },

        # Google's image-understanding guidance recommends
        # minimal thinking for object-location tasks.
        # generation_config={
        #     "thinking_level": (
        #         "minimal"
        #     )
        # },

        store=False,
    )

    raw = (
        interaction.output_text
    )

    if not raw:
        raise RuntimeError(
            "Gemini returned an empty "
            "chart-detection response."
        )

    try:
        return (
            ChartDetectionResponse
            .model_validate_json(
                raw
            )
        )

    except Exception as exc:
        raise RuntimeError(
            "Gemini returned invalid "
            "structured chart-detection output."
        ) from exc


def _detect_with_retry(
    *,
    image_path: Path,
    max_attempts: int = 3,
) -> ChartDetectionResponse:
    """
    Retry only temporary Gemini errors.

    We deliberately do NOT fall back to Cloud Vision because
    Cloud Vision is an OCR service, not a financial-chart
    detector.
    """

    last_error = None

    for attempt in range(
        max_attempts
    ):

        try:
            return _detect_with_gemini(
                image_path=image_path
            )

        except Exception as exc:

            last_error = exc

            error_text = (
                str(exc).upper()
            )

            transient = (
                "503" in error_text
                or "UNAVAILABLE"
                in error_text
                or "429"
                in error_text
                or "RESOURCE_EXHAUSTED"
                in error_text
            )

            if not transient:
                raise

            if (
                attempt
                == max_attempts - 1
            ):
                break

            delay = 2 ** attempt

            print(
                "Gemini chart detection "
                f"temporarily unavailable. "
                f"Retrying in {delay}s..."
            )

            time.sleep(
                delay
            )

    raise RuntimeError(
        "Gemini chart detection failed "
        f"after retries: {last_error}"
    )


# =========================================================
# Crop chart images
# =========================================================


def _crop_image(
    *,
    source_path: Path,
    bbox: list[int],
    destination: Path,
) -> None:
    """
    Crop a chart directly with PyMuPDF.

    No Pillow dependency required.
    """

    source = pymupdf.Pixmap(
        str(source_path)
    )

    try:
        x1, y1, x2, y2 = bbox

        clip = pymupdf.IRect(
            x1,
            y1,
            x2,
            y2,
        )

        cropped = pymupdf.Pixmap(
            source,
            source.width,
            source.height,
            clip,
        )

        try:
            cropped.save(
                str(destination)
            )

        finally:
            cropped = None

    finally:
        source = None


# =========================================================
# Bounding boxes
# =========================================================


def _normalized_box_to_pixels(
    box: list[int],
    *,
    width_px: int,
    height_px: int,
) -> list[int]:
    """
    Gemini:

        [ymin, xmin, ymax, xmax]
        range 0-1000

    Our project:

        [x1, y1, x2, y2]
        image pixels
    """

    if len(box) != 4:
        raise ValueError(
            "Chart bbox must contain "
            "exactly four values."
        )

    ymin, xmin, ymax, xmax = [
        max(
            0,
            min(
                int(value),
                1000,
            ),
        )
        for value in box
    ]

    x1 = round(
        xmin / 1000 * width_px
    )

    y1 = round(
        ymin / 1000 * height_px
    )

    x2 = round(
        xmax / 1000 * width_px
    )

    y2 = round(
        ymax / 1000 * height_px
    )

    # Normalize accidental reversed boxes.
    x1, x2 = sorted(
        (x1, x2)
    )

    y1, y2 = sorted(
        (y1, y2)
    )

    return [
        x1,
        y1,
        x2,
        y2,
    ]


def _bbox_area_ratio(
    bbox: list[int],
    *,
    width_px: int,
    height_px: int,
) -> float:

    x1, y1, x2, y2 = bbox

    width = max(
        0,
        x2 - x1,
    )

    height = max(
        0,
        y2 - y1,
    )

    page_area = (
        width_px
        * height_px
    )

    if page_area <= 0:
        return 0.0

    return (
        width
        * height
        / page_area
    )


# =========================================================
# Duplicate-region removal
# =========================================================


def _deduplicate_regions(
    charts: list[dict[str, Any]],
    *,
    iou_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Gemini can occasionally detect almost the same chart twice.

    If two boxes overlap extremely heavily, keep the larger region.
    """

    if len(charts) <= 1:
        return charts

    ordered = sorted(
        charts,

        key=lambda item: (
            _bbox_area(
                item["bbox"]
            )
        ),

        reverse=True,
    )

    kept = []

    for candidate in ordered:

        duplicate = any(
            _intersection_over_union(
                candidate["bbox"],
                existing["bbox"],
            )
            >= iou_threshold

            for existing in kept
        )

        if not duplicate:
            kept.append(
                candidate
            )

    # Restore roughly page-order presentation.
    return sorted(
        kept,

        key=lambda item: (
            item["bbox"][1],
            item["bbox"][0],
        ),
    )


def _bbox_area(
    bbox: list[int],
) -> int:

    x1, y1, x2, y2 = bbox

    return (
        max(0, x2 - x1)
        * max(0, y2 - y1)
    )


def _intersection_over_union(
    a: list[int],
    b: list[int],
) -> float:

    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    intersection_x1 = max(
        ax1,
        bx1,
    )

    intersection_y1 = max(
        ay1,
        by1,
    )

    intersection_x2 = min(
        ax2,
        bx2,
    )

    intersection_y2 = min(
        ay2,
        by2,
    )

    intersection_width = max(
        0,
        intersection_x2
        - intersection_x1,
    )

    intersection_height = max(
        0,
        intersection_y2
        - intersection_y1,
    )

    intersection = (
        intersection_width
        * intersection_height
    )

    union = (
        _bbox_area(a)
        + _bbox_area(b)
        - intersection
    )

    if union <= 0:
        return 0.0

    return (
        intersection
        / union
    )


# =========================================================
# Cache
# =========================================================


def _cache_path(
    document_id: str,
) -> Path:

    return (
        PROCESSED_ROOT
        / document_id
        / "charts.json"
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
                    "pages",
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
            CHART_CACHE_VERSION
        ),

        "document_id": (
            document_id
        ),

        "pages": {},
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


def _image_dimensions(
    image_path: Path,
) -> tuple[int, int]:

    pixmap = pymupdf.Pixmap(
        str(image_path)
    )

    try:
        return (
            pixmap.width,
            pixmap.height,
        )

    finally:
        pixmap = None


def _normalize_page_numbers(
    values: Iterable[int],
) -> list[int]:

    output = []

    seen = set()

    for value in values:

        if isinstance(
            value,
            bool,
        ):
            raise TypeError(
                "Page numbers must be integers."
            )

        try:
            page = int(
                value
            )

        except (
            TypeError,
            ValueError,
        ) as exc:

            raise TypeError(
                f"Invalid page number: {value}"
            ) from exc

        if page < 1:
            raise ValueError(
                "Page numbers start at 1."
            )

        if page not in seen:

            seen.add(
                page
            )

            output.append(
                page
            )

    return output