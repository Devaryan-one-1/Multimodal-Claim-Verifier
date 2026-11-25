"""Bind a selected table cell to a printed note in the original evidence catalog."""
import re

SUPERS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
DIGITS = str.maketrans(SUPERS, "0123456789")
TAG = re.compile(rf"([{SUPERS}]+|\[\d{{1,3}}\]|[*†‡]+)|\b(?:footnote|note)\s+(\d+)\b", re.I)
TABLE = re.compile(r"\btable\s+([A-Z]?\d+(?:\.\d+)*)\b", re.I)
PAGE = re.compile(r"\b(?:on\s+)?(?:PDF\s+)?page\s+(\d+)\b", re.I)
NOTE = re.compile(rf"^\s*(?:Table\s+[A-Z]?\d+(?:\.\d+)*\s*[,.:–-]\s*)?"
                  rf"(?:(?:Footnote|Note)\s+(\d+)|([{SUPERS}]+|\[\d+\]|[*†‡]+)|"
                  r"(\d+)(?=[.)]))(?:\s*[.):–-]\s*|\s+)(\S.*)$", re.I)


def unmark(text):
    return re.sub(rf"(?:[{SUPERS}]+|\[\d{{1,3}}\]|[*†‡]+)\s*$", "", str(text)).strip()


def cell_requests(table, address, metric):
    row, col = address["row_index"], address["column_index"]
    cells = table["rows"][row]
    headers = table.get("headers") or []
    parts = [str(cells[0]), str(cells[col])]
    if col < len(headers):
        parts.append(str(headers[col]))
    requests = {}
    for part in parts:
        for m in TAG.finditer(part):
            marker = (m[1] or m[2]).strip("[]").translate(DIGITS)
            pages = PAGE.findall(part)
            target = int(pages[0]) if len(set(pages)) == 1 else None
            req = requests.setdefault(marker, dict(table_id=table["id"], document_id=table["document_id"],
                page=table["page"], row=row, column=col, label=str(cells[0]), raw=str(cells[col]),
                unit=address.get("unit_context", ""), marker=marker, target_page=table["page"],
                question=f"Does footnote {marker} limit this {metric or 'reported'} figure to a segment?"))
            if pages:
                req["target_page"] = target
    for req in requests.values():
        # A table caption may carry the actual page reference for a marked cell.
        for field in ("title", "caption"):
            ref = re.search(r"\b(?:see|refer to)\s+(?:footnote|note)\s+(\d+)\s+(?:on\s+)?(?:PDF\s+)?page\s+(\d+)",
                            str(table.get(field) or ""), re.I)
            if ref and ref[1] == req["marker"] and req["target_page"] == req["page"]:
                req["target_page"] = int(ref[2])
    return list(requests.values())


def _below(table, item):
    box, note = table.get("bbox"), item.get("bbox")
    return bool(box and note and note[1] >= box[3] and box[0] - 12 <= note[0] < box[2])


def _lookup(req, table, catalog):
    """Use a marker plus an ownership link. Ambiguous ownership yields no citation."""
    page, marker = req["target_page"], req["marker"]
    if not page:
        return None
    owner_name = TABLE.search(str(table.get("title") or table.get("caption") or ""))
    on_page = [s for s in catalog.values() if s.get("page") == page and s.get("document_id") == req["document_id"]]
    owners = [s for s in on_page if s.get("type") == "table"]
    hits = []

    def consider(item, passage, internal=False):
        lines = passage.splitlines()
        for i, line in enumerate(lines):
            match = NOTE.match(line)
            if not match or (match[1] or match[2] or match[3]).translate(DIGITS).strip("[]") != marker:
                continue
            named = TABLE.search(line)
            if named and (not owner_name or named[1].casefold() != owner_name[1].casefold()):
                continue
            explicit_owner = item.get("table_id")
            if explicit_owner and explicit_owner != table.get("table_id"):
                continue
            if not (internal or named or explicit_owner or
                    page != req["page"] or
                    (len(owners) == 1 and _below(table, item))):
                continue
            end = next((j for j in range(i+1, len(lines)) if NOTE.match(lines[j])), len(lines))
            quote = "\n".join(lines[i:end]).strip()
            citation = dict(item, id=f"{req['document_id']}::{item.get('source_evidence_id') or item['id']}",
                            source_evidence_id=item.get("source_evidence_id") or item["id"])
            hits.append(dict(quote=quote, evidence_id=citation["id"], page=page, citation=citation))

    if page == req["page"]:
        for row in table.get("rows", []):
            nonempty = [str(c) for c in row if str(c or "").strip()]
            if len(nonempty) == 1:
                consider(table, nonempty[0], internal=True)
    for item in on_page:
        if item.get("type") in {"text", "footnote", "caption", "ocr"}:
            consider(item, str(item.get("content") or ""))
    return hits[0] if len(hits) == 1 else None


def linked_note(req, table, catalog):
    note = _lookup(req, table, catalog)
    if note and note["page"] == req["page"]:
        pointer = re.search(r"\b(?:see|refer to)\s+(?:(?:note|footnote)\s+(\d+)\s*,?\s*(?:on\s+)?)?"
                            r"(?:PDF\s+)?page\s+(\d+)\b", note["quote"], re.I)
        if pointer and int(pointer[2]) != req["page"]:
            req["reference"] = note
            note = _lookup(dict(req, target_page=int(pointer[2]), marker=pointer[1] or req["marker"]), table, catalog)
    return note
