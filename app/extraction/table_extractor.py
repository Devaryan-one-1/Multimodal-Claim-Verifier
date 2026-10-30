import os
import pdfplumber

Storage_root = "data/documents"

GAP_THRESHOLD = 14
# period-type keywords to detect in the header block
PERIOD_TYPES = [
    ("three months", "three_months"),
    ("nine months", "nine_months"),
    ("six months", "six_months"),
    ("twelve months", "twelve_months"),
    ("year ended", "year"),
    ("years ended", "year"),
    ("quarter", "quarter"),
]


def extract_tables(document_id: str) -> dict:
    pdf_path = os.path.join(Storage_root, document_id, "original.pdf")
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF {pdf_path} not present")

    tables_out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            page_number = page_index + 1
            words = page.extract_words()

            for table_index, tbl in enumerate(page.find_tables()):
                cleaned = _clean_table(tbl.extract())
                if not cleaned:
                    continue

                headers = cleaned[0]
                rows = cleaned[1:]

                # # how many numeric data columns does this table have?
                # # (use the widest data row as the column count)
                data_cols = max((len(r) - 1 for r in rows), default=0)

                # # recover period column labels from text above the table
                header_block = _header_block_above(words, tbl.bbox)
                # years, period_types = _parse_periods(header_block)

                # # flag for LLM fallback if we found fewer period labels than columns
                # needs_llm_header = len(years) < data_cols or data_cols == 0

                # tables_out.append({
                #     "page": page_number,
                #     "table_id": f"table_{page_number}_{table_index + 1}",
                #     "bbox": list(tbl.bbox),
                #     "headers": headers,                 # the raw first extracted row
                #     "rows": rows,
                #     "column_headers": {                 # recovered period labels
                #         "years": years,
                #         "period_types": period_types,
                #         "raw": header_block,
                #     },
                #     "data_columns": data_cols,
                #     "needs_llm_header": needs_llm_header,
                # })
                header_block = _header_block_above(words, tbl.bbox)
                years, period_types, column_map = _parse_periods(header_block, data_cols)

                needs_llm_header = len(years) != data_cols or data_cols == 0

                # is this real financial data, or navigation (TOC)?
                is_data = _looks_like_data(headers, rows, years)

                tables_out.append({
                    "page": page_number,
                    "table_id": f"table_{page_number}_{table_index + 1}",
                    "bbox": list(tbl.bbox),
                    "headers": headers,
                    "rows": rows,
                    "column_headers": {
                        "years": years,
                        "period_types": period_types,
                        "raw": header_block,
                    },
                    "column_map": column_map,
                    "data_columns": data_cols,
                    "is_data": is_data,
                    "needs_llm_header": needs_llm_header,
                })

    return {"document_id": document_id, "tables": tables_out}


def _clean_table(raw):
    """Keep missing values in place; merge only table-wide currency columns."""
    rows = []
    for row in raw or []:
        if row is None:
            continue
        cells = [str(cell).replace("\n", " ").strip() if cell is not None else "" for cell in row]
        if any(cells):
            rows.append(cells)
    if not rows:
        return []

    symbols = {"$", "\u20ac", "\u00a3"}
    header, body = rows[0], rows[1:] or rows
    currency_columns = set()
    for column in range(1, max(map(len, rows)) - 1):
        values = [row[column] for row in body if column < len(row) and row[column]]
        left = header[column] if column < len(header) else ""
        right = header[column + 1] if column + 1 < len(header) else ""
        if left in symbols:
            values.append(left)
        if not values or any(value not in symbols for value in values):
            continue
        # Two nonempty header labels may be separate years, not a split amount.
        if left and right and left not in symbols:
            continue
        if any(row[column + 1] in symbols for row in body if column + 1 < len(row)):
            continue
        currency_columns.add(column)

    cleaned = []
    for row in rows:
        cells, column = [], 0
        while column < len(row):
            value = row[column]
            if column in currency_columns:
                following = row[column + 1] if column + 1 < len(row) else ""
                if value in symbols:
                    value = value + following if following else ""
                elif not value:
                    value = following
                column += 1
            cells.append(value)
            column += 1
        cleaned.append(cells)
    return cleaned


def _header_block_above(words, bbox):
    """
    Collect word-lines directly above the table (within its x-span), walking
    upward until a blank vertical gap. Returns the joined text of that block.
    """
    x0, top, x1, _bottom = bbox

    # words above the table top, roughly within its horizontal span
    above = [w for w in words
             if w["bottom"] <= top and w["x1"] > x0 and w["x0"] < x1]
    if not above:
        return ""

    # group into lines by rounded vertical position
    lines = {}
    for w in above:
        key = round(w["top"])
        lines.setdefault(key, []).append(w)
    line_keys = sorted(lines.keys(), reverse=True)  # closest to table first (largest top)

    # walk upward from the table, stop at the first big vertical gap
    block_lines = []
    prev_key = None
    for key in line_keys:
        if prev_key is not None and abs(prev_key - key) > GAP_THRESHOLD:
            break
        line_words = sorted(lines[key], key=lambda w: w["x0"])
        block_lines.append(" ".join(w["text"] for w in line_words))
        prev_key = key

    block_lines.reverse()  # top-to-bottom reading order
    return " ".join(block_lines)


# def _parse_periods(header_text):
#     """
#     Extract years and period-type markers from the header block text.
#     Returns (years, period_types) as lists.
#     """
#     if not header_text:
#         return [], []

#     lower = header_text.lower()

#     # years: 4-digit tokens that look like years, in order of appearance
#     years = []
#     for tok in header_text.replace(",", " ").split():
#         clean = tok.strip("().,;:")
#         if clean.isdigit() and len(clean) == 4 and 1990 < int(clean) < 2100:
#             years.append(int(clean))

#     # period types present in the block
#     period_types = []
#     for phrase, label in PERIOD_TYPES:
#         if phrase in lower and label not in period_types:
#             period_types.append(label)

#     return years, period_types
def _parse_periods(header_text, data_cols):
    """
    Extract years, period types, AND a positional column map.
    Returns (years, period_types, column_map).

    column_map is a list aligned to the DATA columns (excluding the row label),
    e.g. for 'Three Months Ended  Nine Months Ended ... 2026 2025 2026 2025':
      [{col:1, year:2026, period:three_months},
       {col:2, year:2025, period:three_months},
       {col:3, year:2026, period:nine_months},
       {col:4, year:2025, period:nine_months}]
    """
    if not header_text:
        return [], [], []

    lower = header_text.lower()

    # years in order of appearance
    years = []
    for tok in header_text.replace(",", " ").split():
        clean = tok.strip("().,;:")
        if clean.isdigit() and len(clean) == 4 and 1990 < int(clean) < 2100:
            years.append(int(clean))

    # period-type phrases in the order they appear in the header
    ordered_types = []
    for phrase, label in PERIOD_TYPES:
        idx = lower.find(phrase)
        if idx != -1:
            ordered_types.append((idx, label))
    ordered_types.sort()                     # by position in the text
    period_types = [label for _, label in ordered_types]

    # build the positional map
    column_map = []
    if years and data_cols > 0 and len(years) == data_cols:
        if period_types and len(years) == data_cols and len(period_types) >= 1:
            # split the year columns evenly across the period types, in order
            per_group = max(1, len(years) // len(period_types))
            for i, yr in enumerate(years[:data_cols]):
                group = min(i // per_group, len(period_types) - 1)
                column_map.append({
                    "col": i + 1,            # +1 because col 0 is the row label
                    "year": yr,
                    "period": period_types[group],
                })
        else:
            # no period types (e.g. balance sheet): just map years positionally
            for i, yr in enumerate(years[:data_cols]):
                column_map.append({"col": i + 1, "year": yr, "period": None})

    return years, period_types, column_map
def _looks_like_data(headers, rows, years):
    """
    Heuristic: a real data table has recovered years and numeric cells.
    TOC/navigation tables have labels like 'Item 1.' and page numbers only.
    """
    if not years:
        # no period columns found — likely TOC or non-financial
        joined = " ".join(str(c) for c in (headers or [])).lower()
        if joined.startswith("item") or "item " in joined:
            return False
    # count cells that parse as numbers across the first few rows
    numeric = 0
    for r in (rows or [])[:5]:
        for c in r[1:]:
            s = str(c).replace("$", "").replace(",", "").replace("(", "-").replace(")", "").strip()
            try:
                float(s)
                numeric += 1
            except ValueError:
                pass
    return numeric >= 2
# def extract_tables(document_id:str) -> dict:
#     pdf_path = os.path.join(Storage_root,document_id,"original.pdf")
#     if not os.path.exists(pdf_path):
#         raise FileNotFoundError(f"pdf {document_id} not found")

#     tables_out = []

#     with pdfplumber.open(pdf_path) as pdf:
#         for page_index, page in enumerate(pdf.pages):
#             page_number = page_index + 1

#             raw_tables = page.extract_tables()
#             for tabls_index, raw in enumerate(raw_tables):
#                 cleaned = _clean_table(raw)
#                 if not cleaned:
#                     continue
#                 headers = cleaned[0]
#                 rows = cleaned[1:]
#                 tables_out.append({
#                     "page" : page_number,
#                     "table_id" : f"table_{page_number}_{tabls_index+1}",
#                     "headers" : headers,
#                     "rows" : rows,
#                 })
#     return {"document_id" : document_id, "tables" : tables_out}

# def _clean_table(raw):
#     # cleaned = []
#     # for row in raw:

#     #     cells = [(c or "").replace("\n", " ").strip() for c in row]

#     #     if any(cells):
#     #         cleaned.append(cells)
#     # return cleaned
#     cleaned = []
#     for row in raw:
#         # normalize each cell to a stripped string ('' for None)
#         cells = [(c or "").replace("\n", " ").strip() for c in row]

#         merged = []
#         pending_currency = ""
#         for cell in cells:
#             if cell in ("$", "€", "£"):
#                 pending_currency = cell          # hold it for the next value
#                 continue
#             if cell == "":
#                 continue                          # drop spacer/empty cells
#             merged.append(pending_currency + cell)
#             pending_currency = ""

#         if merged:                                # keep only non-empty rows
#             cleaned.append(merged)
#     return cleaned

# if __name__ == "__main__":
#     import sys
#     import json

#     result = extract_tables(sys.argv[1])
#     print(f"Found {len(result["tables"])} tables in {result["document_id"]}")
#     for t in result["tables"]:
#         print(f" {t['table_id']} (page {t['page']}):" f"{len(t['headers'])} cols x {len(t['rows'])} rows")
#     with open("table.json", "w") as f:
#         json.dump(result, f, indent = 2)


