import os
import pdfplumber

Storage_Root = "data/documents"

def analyze_layout(document_id:str)->dict:
    pdf_path = os.path.join(Storage_Root,document_id,"original.pdf")
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"pdf {document_id}Dosen't exist")
    pages_out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            regions = []
            tables = page.find_tables()
            table_boxes = [t.bbox for t in tables]
            for t in tables:
                regions.append({
                    "type" : "table",
                    "bbox" : t.bbox,
                    "text" : None,
                })
            for img in page.images:
                regions.append({
                    "type": "figure",
                    "bbox": (img["x0"], img["top"], img["x1"], img["bottom"]),
                    "text" : None
                })
            lines = _group_words_into_lines(page.extract_words(extra_attrs = ["size"]))
            body_size = _estimate_body_size(lines)
            page_height = page.height

            for line in lines:
                if _inside_any(line["bbox"], table_boxes):
                    continue
                regions.append({
                    "type" : _classify_line(line, body_size, page_height),
                    "bbox" : line['bbox'],
                    "text" : line['text']
                })
            pages_out.append({
                "page_number" : page_index + 1,
                "regions" : regions,
            })
    return {"document_id" : document_id, "pages" : pages_out}

def _group_words_into_lines(words):
    """pdfplumber gives individual words; group ones on the same y into lines."""
    lines = {}
    for w in words:
        # round the vertical position so words on the same line bucket together
        key = round(w["top"])
        lines.setdefault(key, []).append(w)

    result = []
    for key in sorted(lines):
        group = sorted(lines[key], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in group)
        size = max(w.get("size", 0) for w in group)
        x0 = min(w["x0"] for w in group)
        x1 = max(w["x1"] for w in group)
        top = min(w["top"] for w in group)
        bottom = max(w["bottom"] for w in group)
        result.append({
            "text": text,
            "size": size,
            "bbox": (x0, top, x1, bottom),
        })
    return result

def _estimate_body_size(lines):
    """Most common font size = body text. Everything is judged relative to it."""
    if not lines:
        return 0
    sizes = {}
    for line in lines:
        s = round(line["size"], 1)
        sizes[s] = sizes.get(s, 0) + 1
    return max(sizes, key=sizes.get)  # the size that appears most often

def _classify_line(line, body_size, page_height):
    size = line["size"]
    text = line["text"].strip()
    top = line["bbox"][1]

    lower = text.lower()

    # caption: starts with "figure"/"table" and is small-ish
    if lower.startswith(("figure", "fig.", "table")) and size <= body_size + 1:
        return "caption"

    # heading: notably larger than body text
    if size >= body_size + 1.5:
        return "heading"

    # footnote: smaller than body AND near the bottom of the page
    if size < body_size - 0.5 and top > page_height * 0.85:
        return "footnote"

    return "paragraph"

def _inside_any(bbox, boxes):
    """True if the center of bbox falls inside any of the given boxes."""
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for b in boxes:
        if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
            return True
    return False

if __name__ == "__main__":
    import sys
    import json
    from collections import Counter

    result = analyze_layout(sys.argv[1])
    for page in result["pages"]:
        counts = Counter(r["type"] for r in page["regions"])
        print(f"Page {page['page_number']}: {dict(counts)}")
    with open("test.json", "w") as f:
        json.dump(result, f, indent=2)    