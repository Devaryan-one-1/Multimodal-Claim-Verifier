"""Small Apple-report smoke comparison; no app/database/vector-index writes.

python check_table_methods.py --page-fallback
python check_table_methods.py --page-fallback --run-api --ask-key --vision-model provider/model:free

Uses GOOGLE_API_KEY (or GEMINI_API_KEY) for embeddings and OpenRouter for vision.
--ask-key prompts privately; it does not read/decrypt customer connection records.
Gold values are checked AFTER retrieval, never included in extraction prompts.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
SOURCE_SHA = "3518726963892d32ec11a876a5dfa44810cd557d577b91eae28fccad29962505"


@dataclass(frozen=True)
class Check:
    name: str
    claim: str
    metric: str
    # (source year, independently reviewed raw value in millions)
    facts: tuple
    claimed: Decimal
    expected: str
    growth: bool = False


CHECKS = (
    Check("T1", "At June 27, 2026, Apple's total current assets were $149,818 million.",
          "total current assets", ((2026, "149818"),), Decimal("149818"), "SUPPORTED"),
    Check("T2", "At June 27, 2026, Apple's total current assets were $147,957 million.",
          "total current assets", ((2026, "149818"),), Decimal("147957"), "CONTRADICTED"),
    Check("T3", "Apple's inventories grew 94.0% between September 27, 2025 and June 27, 2026.",
          "inventories", ((2025, "5718"), (2026, "11092")), Decimal("94.0"), "SUPPORTED", True),
    Check("T4", "Apple's depreciation and amortization was $9,973 million for the nine months ended June 27, 2026.",
          "depreciation and amortization", ((2026, "9973"),), Decimal("9973"), "SUPPORTED"),
)


def canonical(value):
    return " ".join(w for w in re.findall(r"\w+", str(value).casefold()) if w not in {"total", "and"})


def number(value):
    text = str(value).strip().replace(",", "").replace("$", "").replace("−", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-"+text[1:-1]
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise ValueError("Cell is not a directly readable number")
    return Decimal(text)


def cosine(left, right):
    if not left or len(left) != len(right):
        raise ValueError("Incompatible embedding dimensions")
    if any(not math.isfinite(x) for x in (*left, *right)):
        raise ValueError("Nonfinite embedding")
    scale = math.sqrt(sum(x*x for x in left)*sum(x*x for x in right))
    if scale == 0:
        raise ValueError("Zero embedding")
    return sum(a*b for a, b in zip(left, right))/scale


def assess(check, tables):
    found, addresses = {}, []
    for year, _gold in check.facts:
        candidates = []
        for table in tables:
            context = " ".join((table.get("unit", ""), table.get("context", "")))
            if not re.search(r"\bmillions?\b", context, re.I):
                continue
            columns = table.get("columns", [])
            for row_index, row in enumerate(table.get("rows", [])):
                if not row or canonical(row[0]) != canonical(check.metric):
                    continue
                for column, header in enumerate(columns[1:], 1):
                    if not re.search(rf"\b{year}\b", str(header)) or column >= len(row):
                        continue
                    period = str(header)+" "+context
                    if check.name == "T4" and not re.search(r"(?:nine|9)[ _-]*months", period, re.I):
                        continue
                    if check.name != "T4" and re.search(r"(?:three|six|nine|twelve|3|6|9|12)[ _-]*months", period, re.I):
                        continue
                    try:
                        value = number(row[column])
                    except ValueError:
                        continue
                    candidates.append((value, {"page": table["page"], "row": row_index,
                                               "column": column, "raw": row[column], "header": header}))
        if len(candidates) != 1:
            return {"status": "FAIL", "verdict": "UNKNOWN", "reason": f"{year}: missing/ambiguous row, unit or year column"}
        found[year], address = candidates[0]
        addresses.append(address)
    if check.growth:
        old, new = (found[year] for year, _ in check.facts)
        if old == 0:
            return {"status": "FAIL", "verdict": "UNKNOWN", "reason": "Zero growth baseline"}
        actual, tolerance = (new-old)/old*100, Decimal("0.05")
    else:
        actual, tolerance = found[check.facts[0][0]], Decimal("0.5")
    verdict = "SUPPORTED" if abs(actual-check.claimed) <= tolerance else "CONTRADICTED"
    cells_match = all(found[year] == Decimal(gold) for year, gold in check.facts)
    return {"status": "PASS" if cells_match and verdict == check.expected else "FAIL", "verdict": verdict,
            "source_values_correct": cells_match, "actual": str(actual), "addresses": addresses}


def extract(pdf, pages, folder, fallback, strategy):
    import pdfplumber
    from extraction.table_extractor import _clean_table, _header_block_above, _parse_periods
    tables, images, detection = [], [], []
    settings = {} if strategy == "lines" else {"vertical_strategy": "text", "horizontal_strategy": "text"}
    with pdfplumber.open(pdf) as document:
        for n in pages:
            if not 1 <= n <= len(document.pages):
                raise ValueError(f"Page {n} is outside this PDF")
            page = document.pages[n-1]
            accepted = []
            for candidate in page.find_tables(table_settings=settings):
                rows = _clean_table(candidate.extract())
                numeric = sum(bool(re.search(r"\d", cell)) for row in rows for cell in row)
                if len(rows) < 3 or numeric < 3 or candidate.bbox[2]-candidate.bbox[0] < page.width*0.3:
                    continue
                accepted.append((candidate, rows))
            detection.append({"page": n, "tables_found": len(accepted), "whole_page_fallback": not accepted and fallback})
            for index, (candidate, rows) in enumerate(accepted):
                context = _header_block_above(page.extract_words(), candidate.bbox)
                width = max(map(len, rows))
                _, _, mapping = _parse_periods(context, width-1)
                columns = [str(rows[0][i]) if i < len(rows[0]) else "" for i in range(width)]
                for col in mapping:
                    columns[col["col"]] = f"{col.get('year')} {col.get('period') or ''}"
                tables.append({"page": n, "title": "pdfplumber", "unit": context, "context": context,
                               "columns": columns, "rows": rows[1:]})
                x0, top, x1, bottom = candidate.bbox
                box = (max(0, x0-12), max(0, top-90), min(page.width, x1+12), min(page.height, bottom+6))
                path = folder/f"page-{n}-table-{index+1}.png"
                page.crop(box).to_image(resolution=150).save(str(path))
                images.append({"page": n, "path": str(path), "bbox": list(box), "fallback": False})
            if not accepted and fallback:
                path = folder/f"page-{n}-whole-page.png"
                page.to_image(resolution=150).save(str(path))
                images.append({"page": n, "path": str(path), "bbox": list(page.bbox), "fallback": True})
    return tables, images, detection


def embed(client, inputs, *, image_model=False, query=False):
    from google.genai import types
    if image_model:
        contents = [types.Content(parts=[types.Part.from_text(text="task: search result | query: "+item)]) if query else
                    types.Content(parts=[types.Part.from_bytes(data=Path(item).read_bytes(), mime_type="image/png")])
                    for item in inputs]
        config = types.EmbedContentConfig(output_dimensionality=768)
        model = "gemini-embedding-2"
    else:
        contents = inputs
        config = types.EmbedContentConfig(output_dimensionality=768, task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT")
        model = "gemini-embedding-001"
    vectors = []
    for start in range(0, len(contents), 6):
        result = client.models.embed_content(model=model, contents=contents[start:start+6], config=config)
        vectors.extend(list(item.values) for item in result.embeddings)
    if len(vectors) != len(inputs):
        raise ValueError("Embedding response count mismatch")
    return vectors


def vision(image, model, cache):
    path = image["path"]
    if path not in cache:
        from pydantic import create_model
        from services.model_gateway import ModelConfig, configure, structured
        table = create_model("TableRead", title=(str, ...), unit=(str, ...), columns=(list[str], ...), rows=(list[list[str]], ...))
        response = create_model("TablesRead", tables=(list[table], ...))
        configure(ModelConfig(provider="openrouter", text_model=model, vision_model=model, draft_model=model))
        read = structured(response, "Transcribe every financial table in this image. Copy the table title and unit verbatim. "
                          "columns must include the label column at index 0 and exact dates/durations for every value column. "
                          "Each row has the same width as columns; its first cell is the verbatim row label. "
                          "Copy printed numeric cells verbatim, preserving signs/commas; missing cells are empty strings. "
                          "Return no inferred figures or calculations. Empty tables if unreadable.", task="vision", images=[path])
        cache[path] = [dict(t.model_dump(), page=image["page"], context="") for t in read.tables]
    return cache[path]


def compare(method, client, tables, images, model, cache):
    started = time.perf_counter()
    if method == "pdfplumber_text":
        items = tables
    elif method == "vision_rows_text":
        items = [table for image in images for table in vision(image, model, cache)]
    else:
        items = images
    if not items:
        return {"status": "UNAVAILABLE", "reason": "No usable candidates extracted", "checks": []}
    multimodal = method == "direct_image"
    payload = [item["path"] if multimodal else json.dumps(item, ensure_ascii=False) for item in items]
    doc_vectors = embed(client, payload, image_model=multimodal)
    query_vectors = embed(client, [check.claim for check in CHECKS], image_model=multimodal, query=True)
    results = []
    for check, vector in zip(CHECKS, query_vectors):
        scores = [cosine(vector, doc) for doc in doc_vectors]
        winner = max(range(len(items)), key=lambda i: scores[i])
        selected = vision(items[winner], model, cache) if multimodal else [items[winner]]
        results.append(dict(case=check.name, score=round(scores[winner], 4), selected_page=items[winner]["page"],
                            **assess(check, selected)))
    return {"status": "COMPLETED", "passed": sum(row["status"] == "PASS" for row in results),
            "seconds": round(time.perf_counter()-started, 2), "candidates": len(items), "checks": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare three table methods on four source-reviewed Apple claims; isolated output only.")
    parser.add_argument("--pdf", type=Path, help="Defaults to the existing reviewed Apple PDF")
    parser.add_argument("--pages", type=int, nargs="+", default=[6, 8], help="Physical PDF pages; default 6 8")
    parser.add_argument("--table-strategy", choices=("lines", "text"), default="lines")
    parser.add_argument("--page-fallback", action="store_true", help="Use full-page images when pdfplumber misses a table; reported explicitly")
    parser.add_argument("--run-api", action="store_true", help="Run all three methods; sends the selected public PDF images/text to configured providers")
    parser.add_argument("--vision-model", default="", help="Exact image-capable OpenRouter ID; otherwise OPENROUTER_VISION_MODEL")
    parser.add_argument("--ask-key", action="store_true", help="Privately prompt for OpenRouter key for this process")
    args = parser.parse_args(argv)
    args.pages = list(dict.fromkeys(args.pages))
    if args.pdf is None:
        record = ROOT/".planning/reviews/2026-11-02-beta-launch/SOURCE_CHECK.json"
        if not record.is_file():
            parser.error("Use --pdf with the existing Apple report path")
        args.pdf = ROOT/json.loads(record.read_text(encoding="utf-8"))["source"]
    args.pdf = args.pdf.resolve()
    if not args.pdf.is_file() or not 1 <= len(args.pages) <= 3:
        parser.error("Choose an existing PDF and one to three pages")
    source_sha = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    if source_sha != SOURCE_SHA:
        parser.error("These four checks require the previously reviewed Apple PDF; this source version differs")
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT/".env", override=False)
        import pdfplumber  # Fail before creating output if project packages are missing.
    except ImportError:
        print("Run this file with your usual project Python environment (pdfplumber, Pillow, google-genai, pydantic).")
        return 2
    folder = ROOT/"tmp"/("table-methods-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(parents=True, exist_ok=False)
    # The existing gateway's relative logs also stay in this test folder.
    previous_cwd = Path.cwd()
    previous_log_dir = os.environ.get("CLAIM_VERIFIER_LOG_DIR")
    os.environ["CLAIM_VERIFIER_LOG_DIR"] = str(folder/"logs")
    os.chdir(folder)
    sys.path.insert(0, str(ROOT/"app"))
    report = {"pdf_sha256": source_sha, "pages": args.pages,
              "note": "Small smoke check: PASS requires correct retrieved cells/units/periods AND verdict. No production index used. Vision reads are cached across image methods; timings are not independent.",
              "embedding_models": {"pdfplumber_text": "gemini-embedding-001", "vision_rows_text": "gemini-embedding-001", "direct_image": "gemini-embedding-2"},
              "table_strategy": args.table_strategy, "methods": {}}
    try:
        tables, images, detection = extract(args.pdf, args.pages, folder, args.page_fallback, args.table_strategy)
        report.update(detection=detection, extracted_tables=tables, images=images)
        print("pdfplumber:", "; ".join(f"page {d['page']}: {d['tables_found']} tables"+
              (" (whole-page image fallback)" if d["whole_page_fallback"] else "") for d in detection))
        if args.run_api:
            from google import genai
            key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not key:
                print("Missing GOOGLE_API_KEY or GEMINI_API_KEY for the embedding comparison.")
                raise ValueError("Set GOOGLE_API_KEY or GEMINI_API_KEY for embeddings")
            if args.ask_key:
                os.environ["OPENROUTER_API_KEY"] = getpass.getpass("OpenRouter key (not saved): ").strip()
            model = args.vision_model or os.getenv("OPENROUTER_VISION_MODEL", "")
            report["vision_model"] = model
            client, cache = genai.Client(api_key=key), {}
            for method in ("pdfplumber_text", "vision_rows_text", "direct_image"):
                try:
                    if method != "pdfplumber_text" and (not model or not os.getenv("OPENROUTER_API_KEY")):
                        result = {"status": "SKIPPED", "reason": "Set --vision-model and OPENROUTER_API_KEY or --ask-key"}
                    else:
                        result = compare(method, client, tables, images, model, cache)
                except Exception as error:
                    # Provider exceptions can contain URLs/credentials; never print/save raw bodies.
                    result = {"status": "ERROR", "error_type": type(error).__name__, "code": str(getattr(error, "code", ""))}
                report["methods"][method] = result
                outcomes = " ".join(f"{r['case']}:{r['status']}({r['verdict']})" for r in result.get("checks", []))
                details = outcomes or result.get("reason") or (result.get("error_type", "")+" "+result.get("code", ""))
                print(f"{method}: {result['status']} {result.get('passed', 0)}/4 {details}")
            report["vision_reads"] = cache
        else:
            print("Images/extraction only. Add --run-api to compare the three methods.")
    except Exception as error:
        report["error"] = {"type": type(error).__name__}
        print("Check could not finish:", type(error).__name__)
    finally:
        destination = folder/"report.json"
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chdir(previous_cwd)
        if previous_log_dir is None:
            os.environ.pop("CLAIM_VERIFIER_LOG_DIR", None)
        else:
            os.environ["CLAIM_VERIFIER_LOG_DIR"] = previous_log_dir
        print("Report:", destination)
    methods = report["methods"]
    if report.get("error"):
        return 1
    if not args.run_api:
        return 0
    return 0 if len(methods) == 3 and all(m.get("status") == "COMPLETED" for m in methods.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
