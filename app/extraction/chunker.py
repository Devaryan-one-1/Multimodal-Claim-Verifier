import os
import json
import re

PROCESSED_ROOT = "data/processed"

MAX_CHARS = 800
OVERLAP_CHARS = 100

TEXT_TYPES = {"text", "heading", "caption", "footnote"}

ABBREVIATIONS = ["U.S.C.", "U.S.", "Inc.", "Corp.", "Ltd.", "No.", "Sec.",
                 "Section", "Fig.", "e.g.", "i.e.", "Mr.", "Ms.", "Dr."]

SENTENCE_ENDERS = (".", "!", "?")

def chunk_document(document_id:str) -> dict:
    doc_dir = os.path.join(PROCESSED_ROOT, document_id)
    evidence = _load(doc_dir, "evidence.json")["evidence"]
    chunks = []
    counter = 1

    def next_id():
        nonlocal counter
        cid = f"chunk_{counter:04d}"
        counter += 1
        return cid
    for ev in evidence:
        etype = ev["type"]

        if etype in TEXT_TYPES:
            text = (ev.get("content") or "").strip()
            if not text:
                continue
            for piece in _recursive_split(text, MAX_CHARS, OVERLAP_CHARS):
                chunks.append({
                    "id" : next_id(),
                    "document_id" : document_id,
                    "page" : ev["page"],
                    "text" : piece,
                    "type" : etype,
                    "location" : ev.get("bbox"),
                    "source_evidence_id" : ev["id"],
                })
        elif etype == "table":
            chunks.append({
                "id" : next_id(),
                "document_id" : document_id,
                "page" : ev["page"],
                "text" : ev.get("content") or "",
                "type" : "table",
                "location" : ev.get("bbox"),
                "source_evidence_id" : ev["id"],
                "table_id" : ev.get("table_id"),
                "headers" : ev.get("headers"),
                "rows" : ev.get("rows"),
                "column_map": ev.get("column_map"),
                "column_headers": ev.get("column_headers"),
                "is_data": ev.get("is_data", True),
            })
    result = { "document_id" : document_id, "chunks" : chunks
    }
    out_path = os.path.join(doc_dir, "chunks.json")
    with open(out_path, "w", encoding = "utf-8") as f:
        json.dump(result, f, indent = 2, ensure_ascii = False)
    print(f"Wrote {len(chunks)} chunks to {out_path}")

    return result

def _recursive_split(text, max_chars, overlap):
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)
    return _pack(sentences, max_chars, overlap)

def _split_sentences(text):
    protected = text
    for abbr in ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", "<DOT>"))

    sentences = []
    start = 0
    i = 0
    n = len(protected)

    while i<n:
        ch = protected[i]
        if ch in SENTENCE_ENDERS:
            if i + 1 >= n or protected[i+1].isspace():
                sentence = protected[start:i + 1].strip()
                if sentence:
                    sentences.append(sentence.replace("<DOT>", "."))
                j = i + 1
                while j < n and protected[j].isspace():
                    j = j + 1
                start = j
                i = j
                continue
        i = i + 1

    tail = protected[start:].strip()
    if tail:
        sentences.append(tail.replace("<DOT>", "."))
    return sentences

def _pack(units, max_chars, overlap):
    """Greedily pack units into chunks up to max_chars, with overlap."""
    chunks = []
    current = ""

    for unit in units:
        # a single unit longer than the cap: hard-split it by words
        if len(unit) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_split_words(unit, max_chars, overlap))
            continue

        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current += " " + unit
        else:
            chunks.append(current.strip())
            # start next chunk with an overlap tail from the previous one
            current = _tail(current, overlap) + " " + unit

    if current.strip():
        chunks.append(current.strip())
    return chunks

def _hard_split_words(text, max_chars, overlap):
    """Last resort: split an over-long sentence on word boundaries."""
    words = text.split()
    chunks = []
    current = ""
    for w in words:
        if not current:
            current = w
        elif len(current) + 1 + len(w) <= max_chars:
            current += " " + w
        else:
            chunks.append(current)
            current = _tail(current, overlap) + " " + w
    if current:
        chunks.append(current)
    return chunks

def _tail(text, overlap):
    """Return the last `overlap` chars, trimmed to a word boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return text
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1:] if space != -1 else tail

def _load(doc_dir, filename):
    path = os.path.join(doc_dir, filename)
    with open(path, encoding="utf-8") as f:
        return json.load(f)

if __name__ == "__main__":
    import sys
    from collections import Counter

    result = chunk_document(sys.argv[1])
    counts = Counter(c["type"] for c in result["chunks"])
    sizes = [len(c["text"]) for c in result["chunks"] if c["type"] != "table"]
    print("by type:", dict(counts))
    if sizes:
        print(f"prose chunk sizes: min={min(sizes)} max={max(sizes)} "
              f"avg={sum(sizes)//len(sizes)}")