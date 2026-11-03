"""Customer PDF page/render budgets, checked before expensive processing."""
from __future__ import annotations

import math
import os


class PDFLimitError(ValueError):
    pass


def check_pages(pdf):
    pages = int(os.getenv("CUSTOMER_MAX_PAGES", "500"))
    pixels = int(os.getenv("CUSTOMER_MAX_PAGE_PIXELS", "25000000"))
    dimension = int(os.getenv("CUSTOMER_MAX_RENDER_DIMENSION", "10000"))
    dpi = 300  # Conservative extraction budget, above the current 200-DPI default.
    if not pdf.is_pdf or pdf.needs_pass or not 1 <= pdf.page_count <= pages:
        raise PDFLimitError(f"Use an unlocked PDF with 1 to {pages} pages.")
    for index in range(pdf.page_count):
        rect = pdf.load_page(index).rect
        width, height = float(rect.width), float(rect.height)
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            raise PDFLimitError(f"PDF page {index+1} has invalid dimensions.")
        w, h = math.ceil(width*dpi/72), math.ceil(height*dpi/72)
        if max(w, h) > dimension or w*h > pixels:
            raise PDFLimitError(f"PDF page {index+1} exceeds the processing size limit. Crop or resize oversized pages before uploading.")


def check_pdf(data):
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        check_pages(pdf)
