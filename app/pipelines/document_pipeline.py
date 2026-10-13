from __future__ import annotations

import re
import time
from pathlib import Path

import pymupdf
from PIL import Image

from extraction.pdf_parser import parsepdf
from extraction.layout_analyzer import analyze_layout
from extraction.table_extractor import extract_tables
from extraction.build_evidence import build_evidence
from extraction.chunker import chunk_document
from retrieval.embeddings import COLLECTION_NAME, embed_document
from services.runtime import digest, read, write

PROFILE = "dashboard-v1"


def process_document(file_bytes: bytes, filename: str, document_id: str, company_name: str = "", reporter=None,
                     *, enable_ocr: bool = True, chart_mode: str = "candidate pages", visual_limit: int = 20, force: bool = False) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", document_id) or document_id in {".", ".."}:
        raise ValueError("Invalid document ID.")
    started = time.perf_counter()
    folder = Path("data/processed") / document_id
    folder.mkdir(parents=True, exist_ok=True)
    source = Path("data/documents") / document_id / "original.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source_hash = digest(file_bytes)
    options = {"ocr": enable_ocr, "chart_mode": chart_mode, "visual_limit": visual_limit}
    meta_path = folder / "document.json"
    def update(stage, completed, message):
        if reporter:
            reporter.update(stage, completed=completed, total=7, message=message)
    if meta_path.exists() and not force:
        previous = read(meta_path)
        if (previous.get("profile") == PROFILE and previous.get("pdf_sha256") == source_hash
                and previous.get("options") == options and previous.get("status") == "ready"
                and (folder / "index.json").exists()
                and read(folder / "index.json").get("collection") == COLLECTION_NAME):
            if source.exists() and digest(source.read_bytes()) == source_hash:
                embed_document(document_id, reporter=reporter)
                update("Ready", 7, "Reusing this document's completed processing and index")
                return previous
    update("Saving document", 1, filename)
    source.write_bytes(file_bytes)
    metadata = {"document_id": document_id, "filename": filename, "company_name": company_name,
                "pdf_path": str(source), "pdf_sha256": source_hash, "fingerprint": source_hash,
                "size_bytes": len(file_bytes), "profile": PROFILE,
                "options": options, "status": "processing", "warnings": []}
    write(meta_path, metadata)
    warnings = metadata["warnings"]
    try:
        update("Extracting text", 2, "Reading actual PDF pages")
        parsed = parsepdf(document_id)
        update("Analyzing layout", 3, "Separating text, tables and visual regions")
        layout = analyze_layout(document_id)
        regions = {p["page_number"]: p["regions"] for p in layout["pages"]}
        pages = []
        with pymupdf.open(source) as pdf:
            for item in parsed["pages"]:
                page_number = item["page_number"]
                page = pdf[page_number - 1]
                pages.append({"page_number": page_number, "text": item.get("text") or item.get("Text") or "",
                              "width": page.rect.width, "height": page.rect.height,
                              "rotation": page.rotation, "regions": regions.get(page_number, [])})
        write(folder / "pages.json", {"document_id": document_id, "pages": pages})
        metadata.update(page_count=parsed["page_count"], metadata=parsed["metadata"],
                        text_page_count=sum(bool(p["text"].strip()) for p in pages))
        update("Extracting tables", 4, "Preserving table values and recovered period headers")
        tables = extract_tables(document_id)
        write(folder / "tables.json", tables)
        write(meta_path, metadata)
        base = build_evidence(document_id)["evidence"]
        by_page = {p["page_number"]: p for p in pages}
        evidence = []
        for item in base:
            if item["type"] == "chart" or (item["type"] == "table" and item.get("is_data") is False):
                continue
            page = by_page[item["page"]]
            item.update(bbox_space="pdf_points", page_width_pt=page["width"],
                        page_height_pt=page["height"], page_rotation=page["rotation"])
            if item["type"] == "table":
                item["context"] = (item.get("column_headers") or {}).get("raw", "")
                # Keep actual page context to resolve units and footnotes.
                item["page_context"] = page["text"]
            evidence.append(item)
        evidence = _merge_text_lines(evidence)
        update("Processing visual evidence", 5, "OCR weak-text pages and analyze requested chart pages")
        weak = [p for p in pages if len(re.sub(r"\W", "", p["text"])) < 80]
        ocr_count = 0
        if enable_ocr:
            from extraction.ocr import ocr_page
            for page in weak:
                result = ocr_page(document_id, page["page_number"], extracted_text=page["text"], providers=("gemini", "cloud_vision"), overwrite=force)
                item = result.get("evidence")
                if result.get("success") and item and not result.get("requires_unknown"):
                    item = dict(item)
                    item.update(width_px=result.get("width_px"), height_px=result.get("height_px"))
                    evidence.append(item)
                    ocr_count += 1
                else:
                    warnings.append(f"OCR unavailable on page {page['page_number']}: {result.get('reason', 'no readable output')}")
        elif weak:
            warnings.append(f"OCR disabled: {len(weak)} weak-text pages were not transcribed.")
        candidates = [p["page_number"] for p in pages if any(r["type"] == "figure" for r in p["regions"])
                      or re.search(r"\b(chart|graph|figure)\b", p["text"], re.I)]
        requested = []
        if chart_mode == "all pages":
            requested = [p["page_number"] for p in pages]
        elif chart_mode == "candidate pages":
            requested = candidates[:visual_limit]
            warnings.append("Chart detection used candidate pages; charts without detectable layout/caption cues may be missed.")
            if len(candidates) > visual_limit:
                warnings.append(f"Chart scan limit left {len(candidates) - visual_limit} candidate pages unscanned.")
        else:
            warnings.append("Chart detection was disabled.")
        detected_charts = []
        if requested:
            from extraction.chart_detector import detect_charts
            from reasoning.vision_client import analyze_charts
            detection = detect_charts(document_id, requested, overwrite=force)
            detected_charts = detection.get("charts", [])
            for result in detection.get("results", []):
                if not result.get("success"):
                    warnings.append(f"Chart detection failed on page {result.get('page_number')}: {result.get('reason', 'provider failure')}")
            analysis = analyze_charts(detected_charts, overwrite=force)
            analyzed = {r.get("chart_id"): r for r in analysis.get("results", [])}
            for chart in detected_charts:
                result = analyzed.get(chart.get("chart_id"), {})
                item = dict(chart.get("evidence") or {})
                if not item:
                    continue
                facts = [fact for fact in result.get("facts", []) if fact.get("value") is not None and fact.get("value_source") == "explicit_label"]
                analysis_data = result.get("analysis") or {}
                display_unit = analysis_data.get("display_unit") or (analysis_data.get("y_axis") or {}).get("unit")
                if display_unit:
                    facts = [dict(fact, unit=display_unit) for fact in facts]
                item["facts"] = facts
                item["content"] = item.get("content", "") + "\n" + "\n".join(
                    f"{f.get('metric') or ''} {f.get('segment') or ''} {f.get('period') or f.get('category') or ''}: {f.get('display_value') or f.get('value')} {f.get('unit') or ''}"
                    for f in facts)
                rendered = Path("data/pages") / document_id / f"page_{item['page']:03d}.png"
                if rendered.exists():
                    with Image.open(rendered) as image:
                        item.update(width_px=image.width, height_px=image.height)
                evidence.append(item)
                if not facts:
                    warnings.append(f"Chart {chart.get('chart_id')} has no explicit readable numeric labels.")
        update("Building evidence", 6, f"Combining {len(evidence)} actual source items")
        write(folder / "evidence.json", {"document_id": document_id, "evidence": evidence})
        chunks = chunk_document(document_id)["chunks"]
        update("Creating search index", 7, f"Indexing {len(chunks)} chunks with document-specific IDs")
        index_count = embed_document(document_id, reporter=reporter)
        metadata.update(status="ready", text_extracted=metadata["text_page_count"] > 0,
                        table_count=sum(t.get("is_data", True) for t in tables["tables"]),
                        chart_count=len(detected_charts), ocr_page_count=ocr_count,
                        evidence_count=len(evidence), chunk_count=index_count,
                        visual_pages_scanned=len(requested), processing_time_seconds=time.perf_counter() - started)
        write(meta_path, metadata)
        return metadata
    except Exception as exc:
        metadata.update(status="error", error=str(exc))
        write(meta_path, metadata)
        raise


def saved_documents() -> list[dict]:
    documents = []
    for path in sorted(Path("data/processed").glob("*/document.json")):
        try:
            item = read(path)
            document_id = item.get("document_id") or path.parent.name
            item["document_id"] = document_id
            pdf = Path("data/documents") / document_id / "original.pdf"
            if not pdf.exists():
                continue
            item.setdefault("filename", pdf.name)
            item.setdefault("pdf_path", str(pdf))
            item.setdefault("company_name", "")
            item.setdefault("fingerprint", item.get("pdf_sha256") or digest(pdf.read_bytes()))
            item.setdefault("size_bytes", pdf.stat().st_size)
            index = path.parent / "index.json"
            if item.get("profile") != PROFILE or not index.exists() or read(index).get("collection") != COLLECTION_NAME:
                item["status"] = "needs processing"
            documents.append(item)
        except (OSError, ValueError, KeyError):
            continue
    known = {item["document_id"] for item in documents}
    for pdf in sorted(Path("data/documents").glob("*/original.pdf")):
        document_id = pdf.parent.name
        if document_id in known:
            continue
        documents.append({"document_id": document_id, "filename": f"{document_id}.pdf",
                          "company_name": "", "pdf_path": str(pdf), "status": "needs processing",
                          "fingerprint": digest(pdf.read_bytes()), "size_bytes": pdf.stat().st_size,
                          "page_count": 0, "warnings": []})
    return documents


def _merge_text_lines(items: list[dict]) -> list[dict]:
    """Retain one page/box while joining nearby lines into searchable passages."""
    grouped = []
    for item in items:
        item = dict(item)
        previous = grouped[-1] if grouped else None
        box, prior = item.get("bbox"), previous.get("bbox") if previous else None
        if (previous and item.get("type") == previous.get("type") == "text"
                and item.get("page") == previous.get("page") and box and prior
                and -2 <= box[1] - prior[3] <= 20
                and abs(box[0] - prior[0]) <= 35
                and len(previous["content"]) + len(item["content"]) < 1500):
            previous["content"] += "\n" + item["content"]
            previous["bbox"] = [min(prior[0], box[0]), min(prior[1], box[1]),
                                max(prior[2], box[2]), max(prior[3], box[3])]
        else:
            grouped.append(item)
    return grouped
