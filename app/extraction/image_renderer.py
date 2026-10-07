from __future__ import annotations
import json
from pathlib import Path
from typing import Iterable, Any
# import fitz
import pymupdf

DOCUMENTS_ROOT = Path("data/documents")
PAGES_ROOT = Path("data/pages")

DEFAULT_DPI = 200




def render_pages(
    document_id: str,
    page_numbers: Iterable[int],
    *,
    dpi: int = DEFAULT_DPI,
    overwrite: bool = False,
    pdf_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Render only the requested PDF pages to PNG.

    Example:
        render_pages(
            "Apple_Financial_10_K",
            [1, 5, 52]
        )

    Produces:
        data/pages/Apple_Financial_10_K/
            page_001.png
            page_005.png
            page_052.png
            pages_manifest.json

    Page numbers are 1-based.

    Existing rendered pages are reused unless overwrite=True.

    A failure on one page does NOT stop other pages from rendering.
    """

    if dpi <= 0:
        raise ValueError("dpi must be greater than 0.")

    requested_pages = _normalize_page_numbers(page_numbers)

    source_pdf = _resolve_pdf_path(
        document_id=document_id,
        pdf_path=pdf_path,
    )

    output_dir = PAGES_ROOT / document_id
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path = (
        output_dir / "pages_manifest.json"
    )

    manifest = _load_manifest(
        manifest_path=manifest_path,
        document_id=document_id,
        source_pdf=source_pdf,
        dpi=dpi,
    )

    result = {
        "document_id": document_id,
        "source_pdf": str(source_pdf),
        "dpi": dpi,
        "requested_pages": requested_pages,
        "rendered": [],
        "reused": [],
        "errors": [],
    }

    if not requested_pages:
        return result

    try:
        document = pymupdf.open(
            str(source_pdf)
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not open PDF: {source_pdf}. "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        page_count = document.page_count

        manifest["page_count"] = page_count
        manifest["dpi"] = dpi

        for page_number in requested_pages:

            try:
                page_result = _render_single_page(
                    document=document,
                    document_id=document_id,
                    page_number=page_number,
                    page_count=page_count,
                    output_dir=output_dir,
                    dpi=dpi,
                    overwrite=overwrite,
                )

                status = page_result["status"]

                if status == "rendered":
                    result["rendered"].append(
                        page_result
                    )

                elif status == "reused":
                    result["reused"].append(
                        page_result
                    )

                # Save/update this page in manifest.
                manifest["pages"][
                    str(page_number)
                ] = {
                    key: value
                    for key, value
                    in page_result.items()
                    if key != "status"
                }

            except Exception as exc:

                error = {
                    "page_number": page_number,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }

                result["errors"].append(
                    error
                )

                # Record failure but continue with other pages.
                manifest.setdefault(
                    "errors",
                    []
                ).append(
                    error
                )

        _save_manifest(
            manifest_path,
            manifest,
        )

    finally:
        document.close()

    result["manifest_path"] = str(
        manifest_path
    )

    result["page_count"] = (
        manifest.get("page_count")
    )

    return result


def render_page(
    document_id: str,
    page_number: int,
    *,
    dpi: int = DEFAULT_DPI,
    overwrite: bool = False,
    pdf_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Convenience wrapper for rendering exactly one page.

    Example:
        result = render_page(
            "Apple_Financial_10_K",
            52
        )
    """

    result = render_pages(
        document_id=document_id,
        page_numbers=[page_number],
        dpi=dpi,
        overwrite=overwrite,
        pdf_path=pdf_path,
    )

    if result["errors"]:
        return {
            "success": False,
            **result["errors"][0],
        }

    if result["rendered"]:
        return {
            "success": True,
            **result["rendered"][0],
        }

    if result["reused"]:
        return {
            "success": True,
            **result["reused"][0],
        }

    return {
        "success": False,
        "page_number": page_number,
        "message": "Page was not rendered.",
    }


def get_rendered_page_path(
    document_id: str,
    page_number: int,
) -> Path | None:
    """
    Return a rendered page path if it already exists.

    Does NOT render anything.
    """

    page_path = (
        PAGES_ROOT
        / document_id
        / _page_filename(page_number)
    )

    if page_path.exists():
        return page_path

    return None


def ensure_page_rendered(
    document_id: str,
    page_number: int,
    *,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """
    Useful later for OCR, chart detection, and Streamlit.

    If the page already exists:
        return it

    Otherwise:
        render it
        return the resulting PNG path
    """

    existing = get_rendered_page_path(
        document_id,
        page_number,
    )

    if existing is not None:
        return existing

    result = render_page(
        document_id=document_id,
        page_number=page_number,
        dpi=dpi,
    )

    if not result.get("success"):
        raise RuntimeError(
            f"Could not render page {page_number}: "
            f"{result.get('message')}"
        )

    return Path(
        result["image_path"]
    )


# =========================================================
# Internal rendering
# =========================================================


def _render_single_page(
    *,
    document: pymupdf.Document,
    document_id: str,
    page_number: int,
    page_count: int,
    output_dir: Path,
    dpi: int,
    overwrite: bool,
) -> dict[str, Any]:
    """
    Render one 1-based PDF page.
    """

    if page_number < 1:
        raise ValueError(
            "Page numbers start at 1."
        )

    if page_number > page_count:
        raise ValueError(
            f"Page {page_number} does not exist. "
            f"Document contains {page_count} pages."
        )

    output_path = (
        output_dir
        / _page_filename(page_number)
    )

    # -----------------------------------------
    # Reuse existing render
    # -----------------------------------------

    if (
        output_path.exists()
        and not overwrite
    ):
        pixmap_info = _image_info(
            output_path
        )

        return {
            "status": "reused",
            "document_id": document_id,
            "page_number": page_number,
            "image_path": str(output_path),
            "dpi": dpi,
            **pixmap_info,
        }

    # PyMuPDF indexes pages from zero.
    page = document.load_page(
        page_number - 1
    )

    # PDF coordinate units use 72 points per inch.
    zoom = dpi / 72.0

    matrix = pymupdf.Matrix(
        zoom,
        zoom,
    )

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False,
    )

    pixmap.save(
        str(output_path)
    )

    return {
        "status": "rendered",
        "document_id": document_id,
        "page_number": page_number,
        "image_path": str(output_path),
        "dpi": dpi,
        "width_px": pixmap.width,
        "height_px": pixmap.height,
        "page_width_pt": round(
            page.rect.width,
            2,
        ),
        "page_height_pt": round(
            page.rect.height,
            2,
        ),
    }


# =========================================================
# PDF location
# =========================================================


def _resolve_pdf_path(
    *,
    document_id: str,
    pdf_path: str | Path | None,
) -> Path:
    """
    Support both document layouts currently relevant
    to this project.

    Preferred structure:

        data/documents/<document_id>/original.pdf

    Existing ZIP/sample structure:

        data/documents/<document_id>.pdf
    """

    if pdf_path is not None:

        explicit = Path(
            pdf_path
        )

        if not explicit.exists():
            raise FileNotFoundError(
                f"PDF not found: {explicit}"
            )

        return explicit

    candidates = [
        DOCUMENTS_ROOT
        / document_id
        / "original.pdf",

        DOCUMENTS_ROOT
        / f"{document_id}.pdf",
    ]

    for candidate in candidates:

        if candidate.exists():
            return candidate

    searched = "\n".join(
        f"  - {path}"
        for path in candidates
    )

    raise FileNotFoundError(
        f"Could not find PDF for "
        f"document_id='{document_id}'.\n"
        f"Searched:\n{searched}"
    )


# =========================================================
# Manifest
# =========================================================


def _load_manifest(
    *,
    manifest_path: Path,
    document_id: str,
    source_pdf: Path,
    dpi: int,
) -> dict[str, Any]:

    if manifest_path.exists():

        try:
            with manifest_path.open(
                "r",
                encoding="utf-8",
            ) as file:

                manifest = json.load(
                    file
                )

            if isinstance(
                manifest,
                dict,
            ):
                manifest.setdefault(
                    "pages",
                    {},
                )

                manifest.setdefault(
                    "errors",
                    [],
                )

                return manifest

        except (
            json.JSONDecodeError,
            OSError,
        ):
            pass

    return {
        "document_id": document_id,
        "source_pdf": str(
            source_pdf
        ),
        "dpi": dpi,
        "page_count": None,
        "pages": {},
        "errors": [],
    }


def _save_manifest(
    path: Path,
    manifest: dict[str, Any],
) -> None:
    """
    Write through a temporary file to reduce the chance
    of leaving a corrupted manifest if writing fails.
    """

    temporary = path.with_suffix(
        ".tmp"
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            manifest,
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


def _normalize_page_numbers(
    page_numbers: Iterable[int],
) -> list[int]:

    normalized = []

    seen = set()

    for value in page_numbers:

        if isinstance(value, bool):
            raise TypeError(
                "Page numbers must be integers."
            )

        try:
            page_number = int(value)

        except (
            TypeError,
            ValueError,
        ) as exc:

            raise TypeError(
                f"Invalid page number: {value}"
            ) from exc

        if page_number < 1:
            raise ValueError(
                f"Invalid page number: "
                f"{page_number}. "
                "Pages start at 1."
            )

        if page_number not in seen:

            seen.add(
                page_number
            )

            normalized.append(
                page_number
            )

    return normalized


def _page_filename(
    page_number: int,
) -> str:

    return (
        f"page_{page_number:03d}.png"
    )


def _image_info(
    image_path: Path,
) -> dict[str, int]:
    """
    Read PNG dimensions using PyMuPDF so we don't need
    another image dependency such as Pillow.
    """

    image = pymupdf.Pixmap(
        str(image_path)
    )

    try:
        return {
            "width_px": image.width,
            "height_px": image.height,
        }

    finally:
        image = None