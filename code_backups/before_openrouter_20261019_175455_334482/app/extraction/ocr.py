from __future__ import annotations

import base64
import time

import json
import os
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

import pymupdf
from dotenv import load_dotenv
from google import genai
from google.genai import types
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

DEFAULT_DPI = 200

MIN_MEANINGFUL_CHARS = 50

# OCR output below this is considered suspiciously empty.
MIN_OCR_OUTPUT_CHARS = 3

OCR_CACHE_VERSION = 2

# DEFAULT_GEMINI_MODEL = os.getenv(
#     "GEMINI_VISION_MODEL",
#     "gemini-flash-latest",
# )

DEFAULT_GEMINI_MODEL = os.getenv(
    "GEMINI_OCR_MODEL",
    "gemini-3.5-flash-lite",
)

DEFAULT_PROVIDERS = (
    "gemini",
    "cloud_vision",
)

ALLOWED_PROVIDERS = {
    "gemini",
    "cloud_vision",
}


# =========================================================
# Gemini structured-output schemas
# =========================================================


class OCRBlock(BaseModel):
    """
    One visually detected text block.

    Gemini boxes use:

        [ymin, xmin, ymax, xmax]

    normalized to 0-1000.
    """

    text: str

    box_2d: list[int] = Field(
        default_factory=list,
        description=(
            "Bounding box [ymin, xmin, ymax, xmax], "
            "normalized from 0 to 1000."
        ),
    )


class GeminiOCRResponse(BaseModel):
    """
    Structured response returned by Gemini Vision.
    """

    text: str = Field(
        description=(
            "Complete visible document text in natural reading order."
        )
    )

    blocks: list[OCRBlock] = Field(
        default_factory=list
    )


# =========================================================
# Public API
# =========================================================


def should_run_ocr(
    extracted_text: str | None,
    *,
    min_meaningful_chars: int = MIN_MEANINGFUL_CHARS,
) -> bool:
    """
    Decide whether normal PDF text extraction is weak enough
    to justify OCR.

    OCR is needed when:

    - there is no extracted text
    - there are too few meaningful characters
    - extracted content appears mostly to be symbols/noise
    """

    if not extracted_text:
        return True

    text = str(extracted_text).strip()

    if not text:
        return True

    meaningful = _meaningful_char_count(
        text
    )

    if meaningful < min_meaningful_chars:
        return True

    visible = [
        char
        for char in text
        if not char.isspace()
    ]

    if not visible:
        return True

    alphanumeric = sum(
        char.isalnum()
        for char in visible
    )

    ratio = (
        alphanumeric
        / len(visible)
    )

    # Broken PDF extraction often produces lots of odd
    # glyphs/symbols.
    if ratio < 0.25:
        return True

    return False


def ocr_page(
    document_id: str,
    page_number: int,
    *,
    extracted_text: str | None = None,
    force: bool = False,
    overwrite: bool = False,
    dpi: int = DEFAULT_DPI,
    providers: Sequence[str] = DEFAULT_PROVIDERS,
) -> dict[str, Any]:
    """
    OCR one page using a provider fallback chain.

    Default order:

        1. Gemini Vision
        2. Google Cloud Vision

    Normal PDF text extraction always comes before this function.

    Parameters
    ----------
    document_id:
        Example:
            Apple_Financial_10_K

    page_number:
        1-based page number.

    extracted_text:
        Existing PyMuPDF text for this page.

        If omitted, this function attempts to read it from:

            data/processed/<document_id>/pages.json

    force:
        Run OCR even when normal PDF extraction is sufficient.

        Useful for testing.

    overwrite:
        Ignore a cached OCR result and call an OCR provider again.

    providers:
        Provider order.

        Default:
            ("gemini", "cloud_vision")

        For testing only Gemini:
            ("gemini",)

        For testing Cloud Vision:
            ("cloud_vision",)
    """

    if page_number < 1:
        raise ValueError(
            "Page numbers start at 1."
        )

    providers = _validate_providers(
        providers
    )

    # -----------------------------------------------------
    # Obtain normal PDF extraction first
    # -----------------------------------------------------

    if extracted_text is None:
        extracted_text = get_extracted_page_text(
            document_id,
            page_number,
        )

    needs_ocr = should_run_ocr(
        extracted_text
    )

    # -----------------------------------------------------
    # Strong native PDF text means OCR is unnecessary.
    # -----------------------------------------------------

    if not force and not needs_ocr:

        return {
            "success": True,
            "status": "skipped",

            "document_id": document_id,
            "page_number": page_number,

            "provider": "pdf_text",

            "reason": (
                "Existing PDF text is sufficient; "
                "OCR was not required."
            ),

            "text": None,
            "blocks": [],

            "evidence": None,

            "requires_unknown": False,

            "meaningful_characters": (
                _meaningful_char_count(
                    extracted_text or ""
                )
            ),
        }

    # -----------------------------------------------------
    # Cache
    # -----------------------------------------------------

    cache = _load_cache(
        document_id
    )

    cached = (
        cache
        .get("pages", {})
        .get(str(page_number))
    )

    if cached and not overwrite:

        result = dict(
            cached
        )

        result["status"] = "cached"

        return result

    # -----------------------------------------------------
    # Render only this required page.
    # -----------------------------------------------------

    try:
        image_path = ensure_page_rendered(
            document_id=document_id,
            page_number=page_number,
            dpi=dpi,
        )

    except Exception as exc:

        return _failed_result(
            document_id=document_id,
            page_number=page_number,
            attempts=[],
            reason=(
                "The PDF page could not be rendered."
            ),
            exc=exc,
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
    # Provider fallback chain
    # -----------------------------------------------------

    attempts = []

    for provider in providers:

        try:

            # if provider == "gemini":

            #     provider_result = (
            #         _gemini_ocr(
            #             image_path=image_path,
            #             width_px=width_px,
            #             height_px=height_px,
            #         )
            #     )
            if provider == "gemini":

                provider_result = _run_gemini_with_retry(
                    image_path=image_path,
                    width_px=width_px,
                    height_px=height_px,
                )

            elif provider == "cloud_vision":

                provider_result = (
                    _cloud_vision_ocr(
                        image_path=image_path,
                        width_px=width_px,
                        height_px=height_px,
                    )
                )

            else:
                continue

            text = (
                provider_result
                .get("text", "")
                .strip()
            )

            # Provider technically returned successfully,
            # but did not provide useful evidence.
            if not _usable_ocr_text(text):

                attempts.append(
                    {
                        "provider": provider,
                        "success": False,
                        "reason": (
                            "Provider returned "
                            "insufficient OCR text."
                        ),
                    }
                )

                continue

            # -------------------------------------------------
            # Successful OCR
            # -------------------------------------------------

            result = _build_success_result(
                document_id=document_id,
                page_number=page_number,
                provider=provider,
                image_path=image_path,
                width_px=width_px,
                height_px=height_px,
                dpi=dpi,
                text=text,
                blocks=provider_result.get(
                    "blocks",
                    [],
                ),
                confidence=provider_result.get(
                    "confidence"
                ),
                attempts=attempts,
            )

            # Cache successful provider result.
            cache.setdefault(
                "pages",
                {},
            )[str(page_number)] = result

            _save_cache(
                document_id,
                cache,
            )

            return result

        except Exception as exc:

            attempts.append(
                {
                    "provider": provider,
                    "success": False,
                    "error_type": (
                        type(exc).__name__
                    ),
                    "reason": str(
                        exc
                    ),
                }
            )

            # Continue to next provider.
            continue

    # -----------------------------------------------------
    # Every OCR provider failed.
    # -----------------------------------------------------

    return _failed_result(
        document_id=document_id,
        page_number=page_number,
        attempts=attempts,
        reason=(
            "All configured OCR providers failed "
            "or returned insufficient text."
        ),
        image_path=str(image_path),
    )


def ocr_pages(
    document_id: str,
    page_numbers: Iterable[int],
    *,
    force: bool = False,
    overwrite: bool = False,
    dpi: int = DEFAULT_DPI,
    providers: Sequence[str] = DEFAULT_PROVIDERS,
) -> dict[str, Any]:
    """
    OCR multiple requested pages.

    Failure on one page never stops another page.
    """

    results = []

    for page_number in page_numbers:

        try:

            result = ocr_page(
                document_id=document_id,
                page_number=int(page_number),
                force=force,
                overwrite=overwrite,
                dpi=dpi,
                providers=providers,
            )

        except Exception as exc:

            result = _failed_result(
                document_id=document_id,
                page_number=int(page_number),
                attempts=[],
                reason=(
                    "Unexpected OCR pipeline failure."
                ),
                exc=exc,
            )

        results.append(
            result
        )

    return {
        "document_id": document_id,

        "results": results,

        "ocr_count": sum(
            result.get("status")
            == "ocr"
            for result in results
        ),

        "cached_count": sum(
            result.get("status")
            == "cached"
            for result in results
        ),

        "skipped_count": sum(
            result.get("status")
            == "skipped"
            for result in results
        ),

        "failed_count": sum(
            not result.get(
                "success",
                False,
            )
            for result in results
        ),
    }


# =========================================================
# Gemini Vision OCR
# =========================================================


# def _gemini_ocr(
#     *,
#     image_path: Path,
#     width_px: int,
#     height_px: int,
# ) -> dict[str, Any]:
#     """
#     Extract document text with Gemini Vision.

#     Gemini returns:

#         text
#         blocks
#             text
#             normalized box

#     We convert normalized boxes to image-pixel coordinates.
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

#     image_bytes = image_path.read_bytes()

#     prompt = """
# You are performing OCR on a page from a financial document.

# Your task is transcription, not summarization.

# Rules:

# 1. Extract all clearly visible text from the page.
# 2. Preserve the natural reading order.
# 3. Preserve numbers, percentages, currencies, dates, headings,
#    labels, and financial values exactly as they appear.
# 4. Do not calculate anything.
# 5. Do not summarize anything.
# 6. Do not infer text that is not visible.
# 7. If a character or number cannot be read reliably, do not invent it.
# 8. Include text from tables and charts when it is clearly readable.
# 9. Return the complete page transcription in `text`.
# 10. Also return useful text regions in `blocks`.

# For each block, box_2d must be:

# [ymin, xmin, ymax, xmax]

# with coordinates normalized from 0 to 1000.

# If there is no readable text, return an empty string and an empty
# block list.
# """.strip()

#     # response = client.models.generate_content(
#     #     model=DEFAULT_GEMINI_MODEL,

#     #     contents=[
#     #         types.Part.from_bytes(
#     #             data=image_bytes,
#     #             mime_type="image/png",
#     #         ),
#     #         prompt,
#     #     ],

#     #     config=types.GenerateContentConfig(
#     #         temperature=0.0,

#     #         response_mime_type=(
#     #             "application/json"
#     #         ),

#     #         response_schema=(
#     #             GeminiOCRResponse
#     #         ),
#     #     ),
#     # )

#     interaction = client.interactions.create(
#     model=DEFAULT_GEMINI_MODEL,

#     input=[
#         {
#             "type": "image",
#             "data": encoded_image,
#             "mime_type": "image/png",
#         },
#         {
#             "type": "text",
#             "text": prompt,
#         },
#     ],

#     response_format={
#         "type": "text",
#         "mime_type": "application/json",
#         "schema": GeminiOCRResponse.model_json_schema(),
#     },

#     store=False,
# )

#     raw = interaction.output_text

#     # parsed = response.parsed

#     parsed = GeminiOCRResponse.model_validate_json(
#         raw
#     )

#     if parsed is None:

#         if not response.text:
#             raise RuntimeError(
#                 "Gemini returned an empty OCR response."
#             )

#         parsed = (
#             GeminiOCRResponse
#             .model_validate_json(
#                 response.text
#             )
#         )

#     elif not isinstance(
#         parsed,
#         GeminiOCRResponse,
#     ):

#         parsed = (
#             GeminiOCRResponse
#             .model_validate(
#                 parsed
#             )
#         )

#     blocks = []

#     for block in parsed.blocks:

#         bbox = _gemini_box_to_pixels(
#             block.box_2d,
#             width_px=width_px,
#             height_px=height_px,
#         )

#         blocks.append(
#             {
#                 "text": block.text,

#                 "bbox": bbox,

#                 "bbox_space": (
#                     "image_pixels"
#                 ),

#                 # Gemini does not provide a calibrated
#                 # OCR confidence score.
#                 "confidence": None,
#             }
#         )

#     return {
#         "provider": "gemini",

#         "text": parsed.text,

#         "blocks": blocks,

#         # Do not invent a confidence score.
#         "confidence": None,
#     }

def _gemini_ocr(
    *,
    image_path: Path,
    width_px: int,
    height_px: int,
) -> dict[str, Any]:
    """
    Extract OCR text using Gemini Vision through the Interactions API.
    """

    api_key = os.getenv("GOOGLE_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not configured."
        )

    client = genai.Client(
        api_key=api_key
    )

    # ---------------------------------------------
    # Read and base64-encode the PNG
    # ---------------------------------------------

    image_bytes = image_path.read_bytes()

    encoded_image = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    # ---------------------------------------------
    # OCR prompt
    # ---------------------------------------------

    prompt = """
You are performing OCR on one page from a financial document.

This is a transcription task, not a summarization task.

Rules:

1. Extract all clearly visible text.
2. Preserve natural reading order.
3. Preserve financial numbers exactly as visible.
4. Preserve currencies, percentages, dates, years,
   headings, labels, and financial values.
5. Do not perform calculations.
6. Do not summarize.
7. Do not infer unreadable or missing text.
8. Do not invent numbers.
9. Include readable text from tables.
10. Include readable labels and values from charts.

Return:

- `text`: the complete readable page transcription
- `blocks`: useful visible text regions

For every block:

box_2d = [ymin, xmin, ymax, xmax]

Coordinates must be normalized between 0 and 1000.

If there is no readable text:

text = ""
blocks = []
""".strip()

    # ---------------------------------------------
    # Gemini Interactions API
    # ---------------------------------------------

    interaction = client.interactions.create(
        model=DEFAULT_GEMINI_MODEL,

        input=[
            {
                "type": "image",
                "data": encoded_image,
                "mime_type": "image/png",
            },
            {
                "type": "text",
                "text": prompt,
            },
        ],

        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": GeminiOCRResponse.model_json_schema(),
        },

        store=False,
    )

    # ---------------------------------------------
    # Read response
    # ---------------------------------------------

    raw = interaction.output_text

    if not raw:
        raise RuntimeError(
            "Gemini returned an empty OCR response."
        )

    try:
        parsed = GeminiOCRResponse.model_validate_json(
            raw
        )

    except Exception as exc:
        raise RuntimeError(
            "Gemini returned invalid structured OCR output."
        ) from exc

    # ---------------------------------------------
    # Convert boxes from 0-1000 → image pixels
    # ---------------------------------------------

    blocks = []

    for block in parsed.blocks:

        bbox = _gemini_box_to_pixels(
            block.box_2d,
            width_px=width_px,
            height_px=height_px,
        )

        blocks.append(
            {
                "text": block.text,
                "bbox": bbox,
                "bbox_space": "image_pixels",

                # Gemini does not provide a calibrated
                # OCR confidence value.
                "confidence": None,
            }
        )

    return {
        "provider": "gemini",
        "text": parsed.text.strip(),
        "blocks": blocks,
        "confidence": None,
    }
# =========================================================
# Google Cloud Vision fallback
# =========================================================


def _cloud_vision_ocr(
    *,
    image_path: Path,
    width_px: int,
    height_px: int,
) -> dict[str, Any]:
    """
    Use Google Cloud Vision DOCUMENT_TEXT_DETECTION.

    This is the backup OCR provider.

    Cloud Vision provides:
        - full text
        - blocks
        - bounding boxes
        - block confidence
    """

    from google.cloud import vision

    client = _create_cloud_vision_client()

    image_bytes = (
        image_path.read_bytes()
    )

    image = vision.Image(
        content=image_bytes
    )

    response = (
        client.document_text_detection(
            image=image
        )
    )

    if response.error.message:
        raise RuntimeError(
            response.error.message
        )

    annotation = (
        response.full_text_annotation
    )

    text = (
        annotation.text or ""
    ).strip()

    blocks = []

    block_confidences = []

    for page in annotation.pages:

        for block in page.blocks:

            block_text = (
                _cloud_block_text(
                    block
                )
            )

            bbox = (
                _cloud_vertices_to_bbox(
                    block.bounding_box.vertices,
                    width_px=width_px,
                    height_px=height_px,
                )
            )

            confidence = (
                float(block.confidence)
                if block.confidence
                is not None
                else None
            )

            if (
                confidence is not None
                and confidence > 0
            ):
                block_confidences.append(
                    confidence
                )

            blocks.append(
                {
                    "text": block_text,

                    "bbox": bbox,

                    "bbox_space": (
                        "image_pixels"
                    ),

                    "confidence": (
                        confidence
                    ),
                }
            )

    confidence = (
        round(
            mean(
                block_confidences
            ),
            4,
        )
        if block_confidences
        else None
    )

    return {
        "provider": (
            "cloud_vision"
        ),

        "text": text,

        "blocks": blocks,

        "confidence": confidence,
    }


def _create_cloud_vision_client():
    """
    Create a Google Cloud Vision client.

    Supports two deployment styles.

    LOCAL DEVELOPMENT
    -----------------
    GOOGLE_APPLICATION_CREDENTIALS points to a JSON file.

    VERCEL / SERVERLESS
    -------------------
    GOOGLE_CLOUD_VISION_CREDENTIALS_JSON contains the
    service-account JSON itself.

    If neither custom option exists, Google ADC is attempted.
    """

    from google.cloud import vision

    raw_credentials = os.getenv(
        "GOOGLE_CLOUD_VISION_CREDENTIALS_JSON"
    )

    if raw_credentials:

        from google.oauth2 import (
            service_account
        )

        try:
            info = json.loads(
                raw_credentials
            )

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GOOGLE_CLOUD_VISION_CREDENTIALS_JSON "
                "contains invalid JSON."
            ) from exc

        credentials = (
            service_account
            .Credentials
            .from_service_account_info(
                info
            )
        )

        return (
            vision.ImageAnnotatorClient(
                credentials=credentials
            )
        )

    # Otherwise Cloud Vision will use normal
    # Application Default Credentials.
    return vision.ImageAnnotatorClient()


# =========================================================
# Cloud Vision conversion helpers
# =========================================================


def _cloud_block_text(
    block,
) -> str:
    """
    Reconstruct block text from Cloud Vision words/symbols.
    """

    paragraphs = []

    for paragraph in block.paragraphs:

        words = []

        for word in paragraph.words:

            word_text = "".join(
                symbol.text
                for symbol
                in word.symbols
            )

            if word_text:
                words.append(
                    word_text
                )

        paragraph_text = " ".join(
            words
        ).strip()

        if paragraph_text:
            paragraphs.append(
                paragraph_text
            )

    return "\n".join(
        paragraphs
    )


def _cloud_vertices_to_bbox(
    vertices,
    *,
    width_px: int,
    height_px: int,
) -> list[int]:
    """
    Convert Cloud Vision polygon vertices into:

        [x1, y1, x2, y2]
    """

    xs = []
    ys = []

    for vertex in vertices:

        x = int(
            getattr(
                vertex,
                "x",
                0,
            )
            or 0
        )

        y = int(
            getattr(
                vertex,
                "y",
                0,
            )
            or 0
        )

        xs.append(
            max(
                0,
                min(
                    x,
                    width_px,
                ),
            )
        )

        ys.append(
            max(
                0,
                min(
                    y,
                    height_px,
                ),
            )
        )

    if not xs or not ys:

        return [
            0,
            0,
            width_px,
            height_px,
        ]

    return [
        min(xs),
        min(ys),
        max(xs),
        max(ys),
    ]


# =========================================================
# Unified success result
# =========================================================


def _build_success_result(
    *,
    document_id: str,
    page_number: int,
    provider: str,
    image_path: Path,
    width_px: int,
    height_px: int,
    dpi: int,
    text: str,
    blocks: list[dict[str, Any]],
    confidence: float | None,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Convert provider-specific OCR into one common evidence format.
    """

    evidence_id = (
        f"ocr_{document_id}_"
        f"page_{page_number:03d}"
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

        "type": "ocr",

        "content": text,
        "text": text,

        "bbox": [
            0,
            0,
            width_px,
            height_px,
        ],

        "bbox_space": (
            "image_pixels"
        ),

        "confidence": (
            confidence
        ),

        "provider": provider,

        "image_path": str(
            image_path
        ),
    }

    return {
        "success": True,

        "status": "ocr",

        "document_id": (
            document_id
        ),

        "page_number": (
            page_number
        ),

        "provider": provider,

        "text": text,

        "blocks": blocks,

        "confidence": (
            confidence
        ),

        "image_path": str(
            image_path
        ),

        "width_px": (
            width_px
        ),

        "height_px": (
            height_px
        ),

        "dpi": dpi,

        "evidence": evidence,

        "provider_attempts": (
            attempts
        ),

        "requires_unknown": False,
    }


# =========================================================
# Existing PDF text
# =========================================================


def get_extracted_page_text(
    document_id: str,
    page_number: int,
) -> str | None:
    """
    Get existing PDF text from:

        data/processed/<document_id>/pages.json
    """

    path = (
        PROCESSED_ROOT
        / document_id
        / "pages.json"
    )

    if not path.exists():
        return None

    try:

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

    except (
        OSError,
        json.JSONDecodeError,
    ):
        return None

    for page in data.get(
        "pages",
        [],
    ):

        if (
            page.get(
                "page_number"
            )
            == page_number
        ):

            return page.get(
                "text"
            )

    return None


def find_weak_text_pages(
    document_id: str,
) -> list[dict[str, Any]]:
    """
    Find document pages where OCR would automatically run.
    """

    path = (
        PROCESSED_ROOT
        / document_id
        / "pages.json"
    )

    if not path.exists():
        return []

    try:

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

    except (
        OSError,
        json.JSONDecodeError,
    ):
        return []

    output = []

    for page in data.get(
        "pages",
        [],
    ):

        text = (
            page.get("text")
            or ""
        )

        if should_run_ocr(
            text
        ):

            output.append(
                {
                    "page_number": (
                        page.get(
                            "page_number"
                        )
                    ),

                    "text_length": len(
                        text
                    ),

                    "meaningful_characters": (
                        _meaningful_char_count(
                            text
                        )
                    ),
                }
            )

    return output


# =========================================================
# Backend diagnostics
# =========================================================


def check_ocr_backends() -> dict[str, Any]:
    """
    Configuration diagnostic.

    Does not make paid API calls.
    """

    gemini_key = bool(
        os.getenv(
            "GOOGLE_API_KEY"
        )
    )

    cloud_json = bool(
        os.getenv(
            "GOOGLE_CLOUD_VISION_CREDENTIALS_JSON"
        )
    )

    cloud_adc_file = bool(
        os.getenv(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
    )

    return {
        "gemini": {
            "configured": (
                gemini_key
            ),

            "model": (
                DEFAULT_GEMINI_MODEL
            ),
        },

        "cloud_vision": {
            "explicit_credentials_configured": (
                cloud_json
                or cloud_adc_file
            ),

            "note": (
                "Cloud Vision may also work through "
                "Application Default Credentials."
            ),
        },
    }


# =========================================================
# Cache
# =========================================================


def _cache_path(
    document_id: str,
) -> Path:

    return (
        PROCESSED_ROOT
        / document_id
        / "ocr.json"
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
            OCR_CACHE_VERSION
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
# Gemini bbox conversion
# =========================================================


def _gemini_box_to_pixels(
    box: list[int],
    *,
    width_px: int,
    height_px: int,
) -> list[int]:
    """
    Gemini object-location coordinates are normalized 0-1000
    and ordered:

        [ymin, xmin, ymax, xmax]

    Convert to our standard:

        [x1, y1, x2, y2]
    """

    if len(box) != 4:

        return [
            0,
            0,
            width_px,
            height_px,
        ]

    ymin, xmin, ymax, xmax = box

    values = [
        max(
            0,
            min(
                int(value),
                1000,
            ),
        )
        for value in (
            ymin,
            xmin,
            ymax,
            xmax,
        )
    ]

    ymin, xmin, ymax, xmax = (
        values
    )

    x1 = round(
        xmin
        / 1000
        * width_px
    )

    y1 = round(
        ymin
        / 1000
        * height_px
    )

    x2 = round(
        xmax
        / 1000
        * width_px
    )

    y2 = round(
        ymax
        / 1000
        * height_px
    )

    return [
        x1,
        y1,
        x2,
        y2,
    ]


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


def _meaningful_char_count(
    text: str,
) -> int:

    return sum(
        char.isalnum()
        for char in str(text)
    )


def _usable_ocr_text(
    text: str,
) -> bool:

    return (
        _meaningful_char_count(
            text
        )
        >= MIN_OCR_OUTPUT_CHARS
    )


def _validate_providers(
    providers: Sequence[str],
) -> tuple[str, ...]:

    output = []

    for provider in providers:

        provider = str(
            provider
        ).strip().lower()

        if (
            provider
            not in ALLOWED_PROVIDERS
        ):

            raise ValueError(
                f"Unknown OCR provider: "
                f"{provider}"
            )

        if provider not in output:
            output.append(
                provider
            )

    if not output:
        raise ValueError(
            "At least one OCR provider "
            "must be configured."
        )

    return tuple(
        output
    )


def _failed_result(
    *,
    document_id: str,
    page_number: int,
    attempts: list[dict[str, Any]],
    reason: str,
    exc: Exception | None = None,
    image_path: str | None = None,
) -> dict[str, Any]:

    if exc is not None:

        attempts = [
            *attempts,

            {
                "success": False,
                "error_type": (
                    type(exc).__name__
                ),
                "reason": str(
                    exc
                ),
            },
        ]

    return {
        "success": False,

        "status": "failed",

        "document_id": (
            document_id
        ),

        "page_number": (
            page_number
        ),

        "provider": None,

        "text": None,

        "blocks": [],

        "confidence": None,

        "evidence": None,

        "image_path": (
            image_path
        ),

        "provider_attempts": (
            attempts
        ),

        "reason": reason,

        # The OCR layer itself does not choose a verdict.
        # This tells the final verification pipeline that
        # it must not invent missing evidence.
        "requires_unknown": True,
    }

def _run_gemini_with_retry(
    *,
    image_path: Path,
    width_px: int,
    height_px: int,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    Retry temporary Gemini failures before falling back
    to the next OCR provider.

    Retry schedule:
        attempt 1 -> immediately
        attempt 2 -> after 1 second
        attempt 3 -> after 2 seconds
    """

    last_error = None

    for attempt in range(max_attempts):

        try:
            return _gemini_ocr(
                image_path=image_path,
                width_px=width_px,
                height_px=height_px,
            )

        except Exception as exc:

            last_error = exc

            error_text = str(exc).upper()

            transient = (
                "503" in error_text
                or "UNAVAILABLE" in error_text
                or "429" in error_text
                or "RESOURCE_EXHAUSTED" in error_text
            )

            # Don't retry programming/schema/auth errors.
            if not transient:
                raise

            # Last attempt -> allow fallback provider to run.
            if attempt == max_attempts - 1:
                break

            delay = 2 ** attempt

            print(
                f"Gemini OCR temporary failure. "
                f"Retrying in {delay}s..."
            )

            time.sleep(delay)

    raise RuntimeError(
        "Gemini OCR failed after retries: "
        f"{last_error}"
    )
# from __future__ import annotation
# import json
# import os
# import re
# from pathlib import Path
# from statistics import mean
# from typing import Any, Iterable
# import pytesseract
# from dotenv import load_dotenv
# from PIL import Image
# from pytesseract import Output

# try :
#     from .image_renderer import ensure_page_rendered
# except ImportError:
#     from image_renderer import ensure_page_rendered


# load_dotenv()


# PROCESSED_ROOT = Path("data/processed")

# DEFAULT_DPI = 200
# DEFAULT_LANGUAGE = "eng"
# DEFAULT_PSM = 3

# # Rough threshold we agreed on.
# MIN_MEANINGFUL_CHARS = 50

# OCR_CACHE_VERSION = 1


# # =========================================================
# # Tesseract configuration
# # =========================================================


# def _configure_tesseract() -> None:
#     """
#     Configure the Tesseract executable.

#     If TESSERACT_CMD exists in .env, use it.

#     Example:

#     TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe
#     """

#     command = os.getenv("TESSERACT_CMD")

#     if command:
#         pytesseract.pytesseract.tesseract_cmd = command


# _configure_tesseract()


# def check_tesseract() -> dict[str, Any]:
#     """
#     Check whether the Tesseract executable is available.

#     Useful for t.py and later Streamlit diagnostics.
#     """

#     try:
#         version = pytesseract.get_tesseract_version()

#         return {
#             "available": True,
#             "version": str(version),
#             "command": pytesseract.pytesseract.tesseract_cmd,
#         }

#     except Exception as exc:
#         return {
#             "available": False,
#             "version": None,
#             "command": pytesseract.pytesseract.tesseract_cmd,
#             "error": f"{type(exc).__name__}: {exc}",
#         }


# # =========================================================
# # OCR decision logic
# # =========================================================


# def should_run_ocr(
#     extracted_text: str | None,
#     *,
#     min_meaningful_chars: int = MIN_MEANINGFUL_CHARS,
# ) -> bool:
#     """
#     Decide whether normal PDF extraction is too weak.

#     OCR runs when:

#         - text is missing
#         - text has fewer than ~50 meaningful alphanumeric characters
#         - text appears to consist mostly of noise/symbols

#     It intentionally does NOT OCR every page.
#     """

#     if not extracted_text:
#         return True

#     text = str(extracted_text).strip()

#     if not text:
#         return True

#     meaningful_chars = _meaningful_char_count(text)

#     if meaningful_chars < min_meaningful_chars:
#         return True

#     # Ignore whitespace when judging whether text is mostly noise.
#     visible = [
#         char
#         for char in text
#         if not char.isspace()
#     ]

#     if not visible:
#         return True

#     alphanumeric = sum(
#         char.isalnum()
#         for char in visible
#     )

#     ratio = alphanumeric / len(visible)

#     # Very low alphanumeric content can indicate broken PDF extraction.
#     if ratio < 0.25:
#         return True

#     return False


# # =========================================================
# # Public OCR functions
# # =========================================================


# def ocr_page(
#     document_id: str,
#     page_number: int,
#     *,
#     extracted_text: str | None = None,
#     force: bool = False,
#     overwrite: bool = False,
#     dpi: int = DEFAULT_DPI,
#     language: str = DEFAULT_LANGUAGE,
#     psm: int = DEFAULT_PSM,
#     timeout: int = 60,
# ) -> dict[str, Any]:
#     """
#     OCR one page.

#     Parameters
#     ----------
#     document_id:
#         Example:
#             Apple_Financial_10_K

#     page_number:
#         1-based PDF page number.

#     extracted_text:
#         Text already extracted from the PDF.

#         If omitted, this function attempts to read the page text
#         from:

#             data/processed/<document_id>/pages.json

#     force:
#         If True, OCR is allowed even when PDF text extraction
#         is already good.

#         This does NOT automatically rerun OCR if a cached OCR
#         result already exists.

#     overwrite:
#         If True, ignore existing cached OCR and rerun Tesseract.

#     Returns
#     -------
#     Structured dictionary containing:

#         status
#         text
#         confidence
#         lines
#         evidence
#         image_path
#     """

#     if page_number < 1:
#         raise ValueError(
#             "page_number must start at 1."
#         )

#     # -----------------------------------------------------
#     # Find existing PDF text if caller didn't provide it
#     # -----------------------------------------------------

#     if extracted_text is None:
#         extracted_text = get_extracted_page_text(
#             document_id,
#             page_number,
#         )

#     meaningful_chars = _meaningful_char_count(
#         extracted_text or ""
#     )

#     needs_ocr = should_run_ocr(
#         extracted_text
#     )

#     # -----------------------------------------------------
#     # Strong PDF text -> do not OCR
#     # -----------------------------------------------------

#     if not force and not needs_ocr:
#         return {
#             "success": True,
#             "status": "skipped",
#             "document_id": document_id,
#             "page_number": page_number,
#             "reason": (
#                 "Existing PDF text is sufficient; OCR was not required."
#             ),
#             "existing_text_length": len(
#                 extracted_text or ""
#             ),
#             "meaningful_characters": meaningful_chars,
#             "text": None,
#             "confidence": None,
#             "lines": [],
#             "evidence": None,
#         }

#     # -----------------------------------------------------
#     # Check cache
#     # -----------------------------------------------------

#     cache = _load_cache(
#         document_id
#     )

#     cache_key = str(page_number)

#     cached = (
#         cache.get("pages", {})
#         .get(cache_key)
#     )

#     if (
#         cached
#         and not overwrite
#         and cached.get("language") == language
#         and cached.get("psm") == psm
#         and cached.get("dpi") == dpi
#     ):
#         result = dict(cached)

#         result["status"] = "cached"
#         result["success"] = True

#         return result

#     # -----------------------------------------------------
#     # Render page
#     # -----------------------------------------------------

#     try:
#         image_path = ensure_page_rendered(
#             document_id=document_id,
#             page_number=page_number,
#             dpi=dpi,
#         )

#     except Exception as exc:
#         return _failure_result(
#             document_id=document_id,
#             page_number=page_number,
#             stage="rendering",
#             exc=exc,
#         )

#     # -----------------------------------------------------
#     # Run Tesseract
#     # -----------------------------------------------------

#     try:
#         result = _perform_ocr(
#             document_id=document_id,
#             page_number=page_number,
#             image_path=Path(image_path),
#             dpi=dpi,
#             language=language,
#             psm=psm,
#             timeout=timeout,
#         )

#     except Exception as exc:
#         return _failure_result(
#             document_id=document_id,
#             page_number=page_number,
#             stage="ocr",
#             exc=exc,
#             image_path=str(image_path),
#         )

#     # -----------------------------------------------------
#     # OCR returned no useful text
#     # -----------------------------------------------------

#     if not result["text"].strip():
#         result["success"] = False
#         result["status"] = "empty"
#         result["reason"] = (
#             "Tesseract completed but returned no useful text."
#         )

#         return result

#     # -----------------------------------------------------
#     # Cache successful OCR
#     # -----------------------------------------------------

#     cached_result = dict(result)

#     # Cache should represent actual OCR, not its retrieval status.
#     cached_result["status"] = "ocr"

#     cache.setdefault(
#         "pages",
#         {},
#     )[cache_key] = cached_result

#     _save_cache(
#         document_id,
#         cache,
#     )

#     return result


# def ocr_pages(
#     document_id: str,
#     page_numbers: Iterable[int],
#     *,
#     force: bool = False,
#     overwrite: bool = False,
#     dpi: int = DEFAULT_DPI,
#     language: str = DEFAULT_LANGUAGE,
#     psm: int = DEFAULT_PSM,
# ) -> dict[str, Any]:
#     """
#     OCR multiple requested pages.

#     One failure does not stop the others.
#     """

#     results = []

#     for page_number in page_numbers:

#         try:
#             result = ocr_page(
#                 document_id=document_id,
#                 page_number=int(page_number),
#                 force=force,
#                 overwrite=overwrite,
#                 dpi=dpi,
#                 language=language,
#                 psm=psm,
#             )

#         except Exception as exc:
#             result = _failure_result(
#                 document_id=document_id,
#                 page_number=int(page_number),
#                 stage="ocr_pages",
#                 exc=exc,
#             )

#         results.append(result)

#     return {
#         "document_id": document_id,
#         "results": results,
#         "ocr_count": sum(
#             result["status"] == "ocr"
#             for result in results
#         ),
#         "cached_count": sum(
#             result["status"] == "cached"
#             for result in results
#         ),
#         "skipped_count": sum(
#             result["status"] == "skipped"
#             for result in results
#         ),
#         "failed_count": sum(
#             not result.get("success", False)
#             for result in results
#         ),
#     }


# # =========================================================
# # Actual Tesseract processing
# # =========================================================


# def _perform_ocr(
#     *,
#     document_id: str,
#     page_number: int,
#     image_path: Path,
#     dpi: int,
#     language: str,
#     psm: int,
#     timeout: int,
# ) -> dict[str, Any]:
#     """
#     Run Tesseract and convert its word-level output into
#     lines + evidence.

#     pytesseract.image_to_data gives us:
#         text
#         confidence
#         bounding boxes
#         block/paragraph/line numbers
#     """

#     config = f"--psm {psm}"

#     with Image.open(image_path) as source_image:
#         image = source_image.convert("RGB")

#         width_px, height_px = image.size

#         data = pytesseract.image_to_data(
#             image,
#             lang=language,
#             config=config,
#             output_type=Output.DICT,
#             timeout=timeout,
#         )

#     words = _extract_words(
#         data
#     )

#     lines = _group_words_into_lines(
#         words
#     )

#     text = "\n".join(
#         line["text"]
#         for line in lines
#         if line["text"].strip()
#     ).strip()

#     valid_confidences = [
#         word["confidence"]
#         for word in words
#         if word["confidence"] is not None
#     ]

#     confidence = (
#         round(
#             mean(valid_confidences),
#             4,
#         )
#         if valid_confidences
#         else None
#     )

#     evidence_id = (
#         f"ocr_{document_id}_"
#         f"page_{page_number:03d}"
#     )

#     evidence = {
#         "id": evidence_id,
#         "evidence_id": evidence_id,
#         "document_id": document_id,

#         "page": page_number,
#         "page_number": page_number,

#         "type": "ocr",

#         "content": text,
#         "text": text,

#         # Entire rendered page.
#         "bbox": [
#             0,
#             0,
#             width_px,
#             height_px,
#         ],

#         # Important: these are image coordinates,
#         # not PDF-point coordinates.
#         "bbox_space": "image_pixels",

#         "confidence": confidence,

#         "image_path": str(
#             image_path
#         ),

#         "language": language,

#         "ocr_engine": "tesseract",
#     }

#     return {
#         "success": True,
#         "status": "ocr",

#         "document_id": document_id,
#         "page_number": page_number,

#         "text": text,
#         "confidence": confidence,

#         "meaningful_characters": (
#             _meaningful_char_count(text)
#         ),

#         "lines": lines,

#         "image_path": str(
#             image_path
#         ),

#         "width_px": width_px,
#         "height_px": height_px,

#         "dpi": dpi,
#         "language": language,
#         "psm": psm,

#         "evidence": evidence,
#     }


# # =========================================================
# # Word / line extraction
# # =========================================================


# def _extract_words(
#     data: dict[str, list],
# ) -> list[dict[str, Any]]:
#     """
#     Convert Tesseract's parallel arrays into structured words.
#     """

#     words = []

#     count = len(
#         data.get("text", [])
#     )

#     for index in range(count):

#         text = str(
#             data["text"][index]
#         ).strip()

#         if not text:
#             continue

#         confidence = _parse_confidence(
#             data.get(
#                 "conf",
#                 [],
#             ),
#             index,
#         )

#         # Tesseract uses -1 for structural rows.
#         if confidence is not None and confidence < 0:
#             confidence = None

#         left = _safe_int(
#             data,
#             "left",
#             index,
#         )

#         top = _safe_int(
#             data,
#             "top",
#             index,
#         )

#         width = _safe_int(
#             data,
#             "width",
#             index,
#         )

#         height = _safe_int(
#             data,
#             "height",
#             index,
#         )

#         words.append(
#             {
#                 "text": text,

#                 # Convert Tesseract 0-100 confidence
#                 # into our 0-1 convention.
#                 "confidence": (
#                     round(
#                         confidence / 100.0,
#                         4,
#                     )
#                     if confidence is not None
#                     else None
#                 ),

#                 "bbox": [
#                     left,
#                     top,
#                     left + width,
#                     top + height,
#                 ],

#                 "bbox_space": (
#                     "image_pixels"
#                 ),

#                 "block": _safe_int(
#                     data,
#                     "block_num",
#                     index,
#                 ),

#                 "paragraph": _safe_int(
#                     data,
#                     "par_num",
#                     index,
#                 ),

#                 "line": _safe_int(
#                     data,
#                     "line_num",
#                     index,
#                 ),
#             }
#         )

#     return words


# def _group_words_into_lines(
#     words: list[dict[str, Any]],
# ) -> list[dict[str, Any]]:
#     """
#     Reconstruct readable lines while preserving bounding boxes.
#     """

#     groups: dict[
#         tuple[int, int, int],
#         list[dict[str, Any]]
#     ] = {}

#     order = []

#     for word in words:

#         key = (
#             word["block"],
#             word["paragraph"],
#             word["line"],
#         )

#         if key not in groups:
#             groups[key] = []
#             order.append(key)

#         groups[key].append(
#             word
#         )

#     lines = []

#     for key in order:

#         line_words = groups[key]

#         text = " ".join(
#             word["text"]
#             for word in line_words
#         )

#         x1 = min(
#             word["bbox"][0]
#             for word in line_words
#         )

#         y1 = min(
#             word["bbox"][1]
#             for word in line_words
#         )

#         x2 = max(
#             word["bbox"][2]
#             for word in line_words
#         )

#         y2 = max(
#             word["bbox"][3]
#             for word in line_words
#         )

#         confidences = [
#             word["confidence"]
#             for word in line_words
#             if word["confidence"] is not None
#         ]

#         line_confidence = (
#             round(
#                 mean(confidences),
#                 4,
#             )
#             if confidences
#             else None
#         )

#         lines.append(
#             {
#                 "text": text,

#                 "bbox": [
#                     x1,
#                     y1,
#                     x2,
#                     y2,
#                 ],

#                 "bbox_space": (
#                     "image_pixels"
#                 ),

#                 "confidence": (
#                     line_confidence
#                 ),

#                 "block": key[0],
#                 "paragraph": key[1],
#                 "line": key[2],
#             }
#         )

#     return lines


# # =========================================================
# # Existing PDF text
# # =========================================================


# def get_extracted_page_text(
#     document_id: str,
#     page_number: int,
# ) -> str | None:
#     """
#     Read existing extracted text from:

#         data/processed/<document_id>/pages.json

#     Your current pages.json structure is:

#         {
#             "document_id": "...",
#             "pages": [
#                 {
#                     "page_number": 1,
#                     "text": "..."
#                 }
#             ]
#         }
#     """

#     path = (
#         PROCESSED_ROOT
#         / document_id
#         / "pages.json"
#     )

#     if not path.exists():
#         return None

#     try:
#         with path.open(
#             "r",
#             encoding="utf-8",
#         ) as file:

#             data = json.load(
#                 file
#             )

#     except (
#         OSError,
#         json.JSONDecodeError,
#     ):
#         return None

#     pages = data.get(
#         "pages",
#         []
#     )

#     for page in pages:

#         if (
#             page.get("page_number")
#             == page_number
#         ):
#             return page.get(
#                 "text"
#             )

#     return None


# def find_weak_text_pages(
#     document_id: str,
#     *,
#     threshold: int = MIN_MEANINGFUL_CHARS,
# ) -> list[dict[str, Any]]:
#     """
#     Find pages that would automatically trigger OCR.

#     Useful for testing and diagnostics.
#     """

#     path = (
#         PROCESSED_ROOT
#         / document_id
#         / "pages.json"
#     )

#     if not path.exists():
#         return []

#     try:
#         with path.open(
#             "r",
#             encoding="utf-8",
#         ) as file:

#             data = json.load(
#                 file
#             )

#     except (
#         OSError,
#         json.JSONDecodeError,
#     ):
#         return []

#     weak_pages = []

#     for page in data.get(
#         "pages",
#         [],
#     ):

#         page_number = page.get(
#             "page_number"
#         )

#         text = page.get(
#             "text"
#         ) or ""

#         count = (
#             _meaningful_char_count(
#                 text
#             )
#         )

#         if should_run_ocr(
#             text,
#             min_meaningful_chars=threshold,
#         ):

#             weak_pages.append(
#                 {
#                     "page_number": (
#                         page_number
#                     ),

#                     "meaningful_characters": (
#                         count
#                     ),

#                     "text_length": len(
#                         text
#                     ),
#                 }
#             )

#     return weak_pages


# # =========================================================
# # Cache
# # =========================================================


# def _cache_path(
#     document_id: str,
# ) -> Path:

#     return (
#         PROCESSED_ROOT
#         / document_id
#         / "ocr.json"
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

#                 cache = json.load(
#                     file
#                 )

#             if isinstance(
#                 cache,
#                 dict,
#             ):
#                 cache.setdefault(
#                     "pages",
#                     {},
#                 )

#                 return cache

#         except (
#             OSError,
#             json.JSONDecodeError,
#         ):
#             pass

#     return {
#         "cache_version": (
#             OCR_CACHE_VERSION
#         ),

#         "document_id": (
#             document_id
#         ),

#         "engine": (
#             "tesseract"
#         ),

#         "language": (
#             DEFAULT_LANGUAGE
#         ),

#         "pages": {},
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

#     temporary = path.with_suffix(
#         ".tmp"
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
# # Utility functions
# # =========================================================


# def _meaningful_char_count(
#     text: str,
# ) -> int:

#     return sum(
#         char.isalnum()
#         for char in str(text)
#     )


# def _parse_confidence(
#     values,
#     index,
# ) -> float | None:

#     try:
#         value = values[index]

#         return float(
#             value
#         )

#     except (
#         IndexError,
#         TypeError,
#         ValueError,
#     ):
#         return None


# def _safe_int(
#     data: dict[str, list],
#     key: str,
#     index: int,
# ) -> int:

#     try:
#         return int(
#             data[key][index]
#         )

#     except (
#         KeyError,
#         IndexError,
#         TypeError,
#         ValueError,
#     ):
#         return 0


# def _failure_result(
#     *,
#     document_id: str,
#     page_number: int,
#     stage: str,
#     exc: Exception,
#     image_path: str | None = None,
# ) -> dict[str, Any]:

#     return {
#         "success": False,
#         "status": "failed",

#         "document_id": (
#             document_id
#         ),

#         "page_number": (
#             page_number
#         ),

#         "stage": stage,

#         "text": None,
#         "confidence": None,
#         "lines": [],
#         "evidence": None,

#         "image_path": (
#             image_path
#         ),

#         "error_type": (
#             type(exc).__name__
#         ),

#         "reason": str(
#             exc
#         ),
#     }