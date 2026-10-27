"""Validate source containers without guessing missing financial values."""
from __future__ import annotations

import pymupdf

from services.document_errors import InvalidPDFError
from services.observability import event


def validate_pdf(file_bytes):
    if not isinstance(file_bytes, bytes) or not file_bytes:
        raise InvalidPDFError("The PDF upload is empty.")
    try:
        with pymupdf.open(stream=file_bytes, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise InvalidPDFError("This PDF is password protected. Upload an unlocked copy.")
            if not pdf.is_pdf or pdf.page_count < 1:
                raise InvalidPDFError("The uploaded file is not a readable PDF with pages.")
    except InvalidPDFError:
        raise
    except Exception as exc:
        raise InvalidPDFError("The uploaded PDF cannot be opened. Check that it is a valid, undamaged PDF.") from exc


def usable_tables(payload, warnings):
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
        raise ValueError("Table extraction returned a malformed result container.")
    accepted = []
    for table in payload["tables"]:
        rows = table.get("rows") if isinstance(table, dict) else None
        headers = table.get("headers") if isinstance(table, dict) else None
        valid = (isinstance(rows, list) and bool(rows) and isinstance(headers, list)
                 and all(isinstance(row, list) and row and
                         all(cell is None or isinstance(cell, (str, int, float)) for cell in row) for row in rows))
        if not valid:
            page = table.get("page") if isinstance(table, dict) else None
            warnings.append(f"Malformed or empty table skipped on page {page}; inspect the source page.")
            event("malformed_table", level="warning", page=page)
            continue
        # Do not pad, reorder, invent headers, or change row/cell addresses.
        accepted.append(table)
    return {**payload, "tables": accepted}


def safe_ocr(function, *args, **kwargs):
    try:
        result = function(*args, **kwargs)
        if not isinstance(result, dict):
            raise ValueError("OCR returned no structured result.")
    except Exception as exc:
        event("ocr_failed", level="warning", error_kind=type(exc).__name__)
        # Stop this processing attempt on an exception. Avoid repeated quota calls.
        raise
    if not result.get("success") or result.get("requires_unknown"):
        event("ocr_unavailable", level="warning")
    return result
