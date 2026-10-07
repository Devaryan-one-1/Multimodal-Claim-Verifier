from __future__ import annotations

from pathlib import Path

import pdfplumber
import pymupdf

from extraction.table_extractor import (
    _clean_table,
    _header_block_above,
    _parse_periods,
)


def actual_page_preview(
    document_id: str,
    page_number: int = 1,
) -> list[dict]:
    """
    Return a real table or text region for trying the viewer.

    This is a UI fixture. It does not retrieve evidence
    for the entered claim or verify the claim.
    """

    path = (
        Path("data/documents")
        / document_id
        / "original.pdf"
    )

    if not path.is_file():
        path = (
            Path("data/documents")
            / f"{document_id}.pdf"
        )

    with pdfplumber.open(path) as pdf:
        if not 1 <= page_number <= len(pdf.pages):
            raise ValueError(
                "The preview page is outside "
                "the uploaded document."
            )

        page = pdf.pages[page_number - 1]

        for index, table in enumerate(page.find_tables()):
            cleaned = _clean_table(table.extract())

            if len(cleaned) < 2:
                continue

            rows = cleaned[1:]

            header_text = _header_block_above(
                page.extract_words(),
                table.bbox,
            )

            _, _, column_map = _parse_periods(
                header_text,
                max(
                    len(row) - 1
                    for row in rows
                ),
            )

            return [
                {
                    "id": (
                        f"preview_table_{document_id}_"
                        f"{page_number}_{index}"
                    ),
                    "document_id": document_id,
                    "page": page_number,
                    "type": "table",
                    "table_id": (
                        f"table_{page_number}_{index + 1}"
                    ),
                    "headers": cleaned[0],
                    "rows": rows,
                    "column_map": column_map,
                    "relevant_row_indices": [0],
                    "bbox": list(table.bbox),
                    "bbox_space": "pdf_points",
                    "page_width_pt": page.width,
                    "page_height_pt": page.height,
                    "page_rotation": page.rotation,
                    "preview_only": True,
                }
            ]

    with pymupdf.open(path) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise ValueError(
                "The preview page is outside "
                "the uploaded document."
            )

        page = pdf.load_page(page_number - 1)

        blocks = [
            block
            for block in page.get_text("blocks")
            if (
                len(block) >= 7
                and block[6] == 0
                and str(block[4]).strip()
            )
        ]

        if not blocks:
            return [
                {
                    "id": (
                        f"preview_{document_id}_{page_number}"
                    ),
                    "document_id": document_id,
                    "page": page_number,
                    "type": "text",
                    "content": (
                        "No extractable text on this page. "
                        "The viewer still displays the actual page."
                    ),
                    "preview_only": True,
                }
            ]

        block = max(
            blocks,
            key=lambda item: len(str(item[4])),
        )

        return [
            {
                "id": (
                    f"preview_{document_id}_{page_number}"
                ),
                "document_id": document_id,
                "page": page_number,
                "type": "text",
                "content": str(block[4]).strip(),
                "bbox": list(block[:4]),
                "bbox_space": "pdf_points",
                "page_width_pt": page.rect.width,
                "page_height_pt": page.rect.height,
                "page_rotation": page.rotation,
                "preview_only": True,
            }
        ]