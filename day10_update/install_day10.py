"""Run beside app/ after stopping Streamlit: python day10_update/install_day10.py

Edits source files only. Never imports the application, runs tests, calls models,
or touches PDFs, embeddings, drafts, reports or prediction caches.
"""
from __future__ import annotations

import ast
import io
import tokenize
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = Path.cwd()
changes = {}
originals = {}
current_source = ""


def source(relative):
    global current_source
    current_source = relative
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"Required source file missing: {relative}")
    originals.setdefault(relative, path.read_bytes())
    return path.read_text(encoding="utf-8-sig")


def once(text, old, new, label):
    # Older versions were retained as comments in the uploaded project. Count
    # only live code anchors, never commented-out examples or string contents.
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    def position(point):
        row, column = point
        return offsets[row - 1] + column

    ignored = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in {tokenize.COMMENT, tokenize.STRING}:
            ignored.append((position(token.start), position(token.end)))
    matches = []
    cursor = 0
    while True:
        start = text.find(old, cursor)
        if start < 0:
            break
        cursor = start + len(old)
        # Whitespace may precede an anchor, but its first code character must
        # not be inside a comment or a string. Preserve the original slice.
        code_start = start + len(old) - len(old.lstrip())
        if not any(left <= code_start < right for left, right in ignored):
            matches.append(start)
    if len(matches) != 1:
        raise RuntimeError(
            f"Compatibility stop: {current_source}: {label}. Expected one live code "
            f"section; found {len(matches)}. No files were changed."
        )
    start = matches[0]
    return text[:start] + new + text[start + len(old):]


def imports(text, value):
    return once(text, "from __future__ import annotations\n", "from __future__ import annotations\n\n" + value + "\n", "future import")


def prepare():
    if not (ROOT / "app/main.py").is_file():
        raise RuntimeError("Run this installer from your project folder, beside app/ and data/.")
    if (ROOT / "app/services/observability.py").exists():
        raise RuntimeError("Day 10 logging already exists. No files changed; do not apply twice.")
    # Keep the latest installed model routing, dashboard tabs and evaluation code.
    text = source("app/main.py")
    anchor = "from ui.workspace import run_dashboard"
    text = once(text, anchor, "from services.observability import configure_logging\nconfigure_logging()\n\n" + anchor, "main dashboard import")
    changes["app/main.py"] = text

    text = source("app/pipelines/document_pipeline.py")
    text = imports(text, "from services.observability import event, operation\nfrom services.document_guards import validate_pdf, usable_tables, safe_ocr\nfrom services.document_errors import NoUsableEvidenceError")
    text = once(text, "def process_document(", '@operation("document_processing")\ndef process_document(', "document function")
    text = once(text, "    started = time.perf_counter()", '    validate_pdf(file_bytes)\n    event("document_uploaded", document_id=document_id, size_bytes=len(file_bytes))\n    started = time.perf_counter()', "PDF validation")
    text = once(text, '        parsed = parsepdf(document_id)', '        event("extraction_started", document_id=document_id)\n        parsed = parsepdf(document_id)', "extraction start")
    text = once(text, '        tables = extract_tables(document_id)', '        tables = usable_tables(extract_tables(document_id), warnings)\n        event("tables_found", document_id=document_id, count=len(tables["tables"]))\n        if not tables["tables"]:\n            warnings.append("No structured tables extracted; text, OCR and visual evidence may still be available.")', "table validation")
    text = once(text, '        ocr_count = 0', '        ocr_count = 0\n        if weak:\n            event("weak_text_pages", document_id=document_id, count=len(weak))\n        if not metadata["text_page_count"]:\n            warnings.append("No native PDF text was extracted; verification depends on successful OCR or visual extraction.")', "scanned pages")
    text = once(text, 'result = ocr_page(document_id,', 'result = safe_ocr(ocr_page, document_id,', "OCR boundary")
    text = once(text, '        update("Building evidence", 6,', '        event("charts_found", document_id=document_id, count=len(detected_charts))\n        if not any(str(item.get("content") or "").strip() or item.get("rows") or item.get("facts") for item in evidence):\n            raise NoUsableEvidenceError()\n        update("Building evidence", 6,', "empty evidence")
    text = once(text, '        chunks = chunk_document(document_id)["chunks"]', '        chunks = chunk_document(document_id)["chunks"]\n        if not chunks:\n            raise NoUsableEvidenceError()', "empty chunks")
    text = once(text, '        return metadata', '        event("document_ready", document_id=document_id, evidence_count=len(evidence),\n              table_count=metadata["table_count"], chart_count=metadata["chart_count"],\n              ocr_page_count=ocr_count, chunk_count=index_count)\n        return metadata', "document ready")
    text = once(text, 'metadata.update(status="error", error=str(exc))', 'metadata.update(status="error", error=str(exc), error_kind=type(exc).__name__,\n                        error_stage=getattr(exc, "stage", "document_processing"))', "document failure")
    changes["app/pipelines/document_pipeline.py"] = text

    text = source("app/pipelines/verification_pipeline.py")
    # Refuse to replace older verifier versions lacking the last numerical/quotas fixes.
    for required in ('from services.verification_errors import', 'source_context', 'reasoning_validation'):
        if required not in text:
            raise RuntimeError(f"The recent verification fixes must be installed first (missing {required}).")
    text = imports(text, "from services.observability import event, operation")
    text = once(text, '@trace_verification\ndef verify_claims(', '@trace_verification\n@operation("claim_verification")\ndef verify_claims(', "verification tracing")
    text = once(text, '    started = time.perf_counter()', '    event("claim_received", claim_length=len(claim_text), document_count=len(document_ids))\n    started = time.perf_counter()', "claim event")
    text = once(text, '            if not hits:', '            event("evidence_retrieved", count=len(hits), stage="initial")\n            if not hits:\n                event("evidence_unavailable", stage="retrieval")', "retrieval event")
    text = once(text, '            result["evidence"] = evidence', '            event("evidence_retrieved", count=len(evidence), stage="resolved")\n            result["evidence"] = evidence', "resolved evidence event")
    text = once(text, '            result.update(calculation=calculation, comparison=comparison,', '            event("calculation_performed" if comparison else "calculation_unavailable",\n                  available=bool(comparison), operation=pc.get("operation"))\n            result.update(calculation=calculation, comparison=comparison,', "calculation event")
    old = '            result.setdefault("calculation_unavailable_reason", str(exc))\n            return result'
    new = '            if not result.get("calculation_unavailable_reason"):\n                result["calculation_unavailable_reason"] = str(exc)\n            return result\n        finally:\n            event("verification_error" if result.get("status") == "error" else "verdict_generated",\n                  level="error" if result.get("status") == "error" else "info",\n                  status=result.get("status"), verdict=result.get("verdict"),\n                  error_kind=result.get("error_kind"), stage=result.get("error_stage"))'
    text = once(text, old, new, "final verdict/error event")
    changes["app/pipelines/verification_pipeline.py"] = text

    text = source("app/services/model_gateway.py")
    text = imports(text, "from services.observability import record_model_call")
    text = once(text, 'def _record(event):\n', 'def _record(event):\n    record_model_call(event)\n', "gateway recorder")
    changes["app/services/model_gateway.py"] = text

    # Move direct UI workflow imports behind explicit compatibility services.
    # Read syntax only: no project imports or execution. Existing UI bodies stay byte-for-byte equivalent.
    exports = {}
    application_modules = {"pipelines.document_pipeline", "pipelines.verification_pipeline", "extraction.image_renderer"}
    allowed_names = {"process_document", "saved_documents", "verify_claims", "aggregate_verdict", "render_page"}
    for path in sorted((ROOT / "app/ui").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        content = source(relative)
        lines = content.splitlines(keepends=True)
        edits = []
        for node in ast.walk(ast.parse(content)):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            module = node.module or ""
            normalized = module.removeprefix("app.")
            names = [alias.name for alias in node.names]
            if normalized in application_modules and set(names).issubset(allowed_names):
                target = "services.application"
            elif normalized.startswith("evaluation."):
                target = "services.evaluation_" + normalized.split(".", 1)[1].replace(".", "_")
                if "*" in names:
                    raise RuntimeError(f"Unsupported wildcard evaluation import in {relative}")
                exports.setdefault((target, normalized), set()).update(names)
            else:
                continue
            start, end = node.lineno - 1, node.end_lineno
            block = "".join(lines[start:end])
            replacement = block.replace("from " + module + " import", "from " + target + " import", 1)
            edits.append((start, end, replacement))
        for start, end, replacement in sorted(edits, reverse=True):
            lines[start:end] = [replacement]
        if edits:
            changes[relative] = "".join(lines)
    for (target, module), names in exports.items():
        relative = "app/" + target.replace(".", "/") + ".py"
        changes[relative] = ('"""Evaluation service exports; workflow implementation stays in evaluation/."""\n'
                             f'from {module} import (\n    ' + ",\n    ".join(sorted(names)) + '\n)\n')
    for path in (PACKAGE / "payload").rglob("*"):
        if path.is_file():
            relative = path.relative_to(PACKAGE / "payload").as_posix()
            changes[relative] = path.read_text(encoding="utf-8")
    # Retain any pre-existing files in the backup, including README.
    for relative in changes:
        path = ROOT / relative
        if path.exists():
            originals.setdefault(relative, path.read_bytes())


def install():
    prepare()  # All compatibility anchors checked before the first write.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup = ROOT / "day10_backups" / stamp
    backup.mkdir(parents=True)
    for relative, data in originals.items():
        if relative in changes:
            dest = backup / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    new_files = [relative for relative in changes if relative not in originals]
    (backup / "NEW_FILES.txt").write_text("\n".join(new_files), encoding="utf-8")
    written = []
    try:
        for relative, content in changes.items():
            dest = ROOT / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            written.append(relative)
            dest.write_text(content, encoding="utf-8")
    except Exception:
        for relative in reversed(written):
            dest = ROOT / relative
            if relative in originals:
                dest.write_bytes(originals[relative])
            else:
                dest.unlink(missing_ok=True)
        raise
    (backup / "CHANGED_FILES.txt").write_text("\n".join(sorted(changes)), encoding="utf-8")
    print("Day 10 source update installed. No application code or tests were run.")
    print("Backup:", backup)
    print("Updated files:")
    for relative in sorted(changes):
        print(" -", relative)
    print("Restart with: streamlit run app/main.py")


if __name__ == "__main__":
    try:
        install()
    except Exception as exc:
        raise SystemExit(str(exc))

# """Run beside app/ after stopping Streamlit: python day10_update/install_day10.py

# Edits source files only. Never imports the application, runs tests, calls models,
# or touches PDFs, embeddings, drafts, reports or prediction caches.
# """
# from __future__ import annotations

# import ast
# import shutil
# from datetime import datetime, timezone
# from pathlib import Path

# PACKAGE = Path(__file__).resolve().parent
# ROOT = Path.cwd()
# changes = {}
# originals = {}


# def source(relative):
#     path = ROOT / relative
#     if not path.is_file():
#         raise RuntimeError(f"Required source file missing: {relative}")
#     originals.setdefault(relative, path.read_bytes())
#     return path.read_text(encoding="utf-8-sig")


# def once(text, old, new, label):
#     if text.count(old) != 1:
#         raise RuntimeError(f"Compatibility stop: {label}. Expected exactly one matching code section. No files were changed.")
#     return text.replace(old, new, 1)


# def imports(text, value):
#     return once(text, "from __future__ import annotations\n", "from __future__ import annotations\n\n" + value + "\n", "future import")


# def prepare():
#     if not (ROOT / "app/main.py").is_file():
#         raise RuntimeError("Run this installer from your project folder, beside app/ and data/.")
#     if (ROOT / "app/services/observability.py").exists():
#         raise RuntimeError("Day 10 logging already exists. No files changed; do not apply twice.")
#     # Keep the latest installed model routing, dashboard tabs and evaluation code.
#     text = source("app/main.py")
#     anchor = "from ui.workspace import run_dashboard"
#     text = once(text, anchor, "from services.observability import configure_logging\nconfigure_logging()\n\n" + anchor, "main dashboard import")
#     changes["app/main.py"] = text

#     text = source("app/pipelines/document_pipeline.py")
#     text = imports(text, "from services.observability import event, operation\nfrom services.document_guards import validate_pdf, usable_tables, safe_ocr\nfrom services.document_errors import NoUsableEvidenceError")
#     text = once(text, "def process_document(", '@operation("document_processing")\ndef process_document(', "document function")
#     text = once(text, "    started = time.perf_counter()", '    validate_pdf(file_bytes)\n    event("document_uploaded", document_id=document_id, size_bytes=len(file_bytes))\n    started = time.perf_counter()', "PDF validation")
#     text = once(text, '        parsed = parsepdf(document_id)', '        event("extraction_started", document_id=document_id)\n        parsed = parsepdf(document_id)', "extraction start")
#     text = once(text, '        tables = extract_tables(document_id)', '        tables = usable_tables(extract_tables(document_id), warnings)\n        event("tables_found", document_id=document_id, count=len(tables["tables"]))\n        if not tables["tables"]:\n            warnings.append("No structured tables extracted; text, OCR and visual evidence may still be available.")', "table validation")
#     text = once(text, '        ocr_count = 0', '        ocr_count = 0\n        if weak:\n            event("weak_text_pages", document_id=document_id, count=len(weak))\n        if not metadata["text_page_count"]:\n            warnings.append("No native PDF text was extracted; verification depends on successful OCR or visual extraction.")', "scanned pages")
#     text = once(text, 'result = ocr_page(document_id,', 'result = safe_ocr(ocr_page, document_id,', "OCR boundary")
#     text = once(text, '        update("Building evidence", 6,', '        event("charts_found", document_id=document_id, count=len(detected_charts))\n        if not any(str(item.get("content") or "").strip() or item.get("rows") or item.get("facts") for item in evidence):\n            raise NoUsableEvidenceError()\n        update("Building evidence", 6,', "empty evidence")
#     text = once(text, '        chunks = chunk_document(document_id)["chunks"]', '        chunks = chunk_document(document_id)["chunks"]\n        if not chunks:\n            raise NoUsableEvidenceError()', "empty chunks")
#     text = once(text, '        return metadata', '        event("document_ready", document_id=document_id, evidence_count=len(evidence),\n              table_count=metadata["table_count"], chart_count=metadata["chart_count"],\n              ocr_page_count=ocr_count, chunk_count=index_count)\n        return metadata', "document ready")
#     text = once(text, 'metadata.update(status="error", error=str(exc))', 'metadata.update(status="error", error=str(exc), error_kind=type(exc).__name__,\n                        error_stage=getattr(exc, "stage", "document_processing"))', "document failure")
#     changes["app/pipelines/document_pipeline.py"] = text

#     text = source("app/pipelines/verification_pipeline.py")
#     # Refuse to replace older verifier versions lacking the last numerical/quotas fixes.
#     for required in ('from services.verification_errors import', 'source_context', 'reasoning_validation'):
#         if required not in text:
#             raise RuntimeError(f"The recent verification fixes must be installed first (missing {required}).")
#     text = imports(text, "from services.observability import event, operation")
#     text = once(text, '@trace_verification\ndef verify_claims(', '@trace_verification\n@operation("claim_verification")\ndef verify_claims(', "verification tracing")
#     text = once(text, '    started = time.perf_counter()', '    event("claim_received", claim_length=len(claim_text), document_count=len(document_ids))\n    started = time.perf_counter()', "claim event")
#     text = once(text, '            if not hits:', '            event("evidence_retrieved", count=len(hits), stage="initial")\n            if not hits:\n                event("evidence_unavailable", stage="retrieval")', "retrieval event")
#     text = once(text, '            result["evidence"] = evidence', '            event("evidence_retrieved", count=len(evidence), stage="resolved")\n            result["evidence"] = evidence', "resolved evidence event")
#     text = once(text, '            result.update(calculation=calculation, comparison=comparison,', '            event("calculation_performed" if comparison else "calculation_unavailable",\n                  available=bool(comparison), operation=pc.get("operation"))\n            result.update(calculation=calculation, comparison=comparison,', "calculation event")
#     old = '            result.setdefault("calculation_unavailable_reason", str(exc))\n            return result'
#     new = '            if not result.get("calculation_unavailable_reason"):\n                result["calculation_unavailable_reason"] = str(exc)\n            return result\n        finally:\n            event("verification_error" if result.get("status") == "error" else "verdict_generated",\n                  level="error" if result.get("status") == "error" else "info",\n                  status=result.get("status"), verdict=result.get("verdict"),\n                  error_kind=result.get("error_kind"), stage=result.get("error_stage"))'
#     text = once(text, old, new, "final verdict/error event")
#     changes["app/pipelines/verification_pipeline.py"] = text

#     text = source("app/services/model_gateway.py")
#     text = imports(text, "from services.observability import record_model_call")
#     text = once(text, 'def _record(event):\n', 'def _record(event):\n    record_model_call(event)\n', "gateway recorder")
#     changes["app/services/model_gateway.py"] = text

#     # Move direct UI workflow imports behind explicit compatibility services.
#     # Read syntax only: no project imports or execution. Existing UI bodies stay byte-for-byte equivalent.
#     exports = {}
#     application_modules = {"pipelines.document_pipeline", "pipelines.verification_pipeline", "extraction.image_renderer"}
#     allowed_names = {"process_document", "saved_documents", "verify_claims", "aggregate_verdict", "render_page"}
#     for path in sorted((ROOT / "app/ui").glob("*.py")):
#         relative = path.relative_to(ROOT).as_posix()
#         content = source(relative)
#         lines = content.splitlines(keepends=True)
#         edits = []
#         for node in ast.walk(ast.parse(content)):
#             if not isinstance(node, ast.ImportFrom) or node.level:
#                 continue
#             module = node.module or ""
#             normalized = module.removeprefix("app.")
#             names = [alias.name for alias in node.names]
#             if normalized in application_modules and set(names).issubset(allowed_names):
#                 target = "services.application"
#             elif normalized.startswith("evaluation."):
#                 target = "services.evaluation_" + normalized.split(".", 1)[1].replace(".", "_")
#                 if "*" in names:
#                     raise RuntimeError(f"Unsupported wildcard evaluation import in {relative}")
#                 exports.setdefault((target, normalized), set()).update(names)
#             else:
#                 continue
#             start, end = node.lineno - 1, node.end_lineno
#             block = "".join(lines[start:end])
#             replacement = block.replace("from " + module + " import", "from " + target + " import", 1)
#             edits.append((start, end, replacement))
#         for start, end, replacement in sorted(edits, reverse=True):
#             lines[start:end] = [replacement]
#         if edits:
#             changes[relative] = "".join(lines)
#     for (target, module), names in exports.items():
#         relative = "app/" + target.replace(".", "/") + ".py"
#         changes[relative] = ('"""Evaluation service exports; workflow implementation stays in evaluation/."""\n'
#                              f'from {module} import (\n    ' + ",\n    ".join(sorted(names)) + '\n)\n')
#     for path in (PACKAGE / "payload").rglob("*"):
#         if path.is_file():
#             relative = path.relative_to(PACKAGE / "payload").as_posix()
#             changes[relative] = path.read_text(encoding="utf-8")
#     # Retain any pre-existing files in the backup, including README.
#     for relative in changes:
#         path = ROOT / relative
#         if path.exists():
#             originals.setdefault(relative, path.read_bytes())


# def install():
#     prepare()  # All compatibility anchors checked before the first write.
#     stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
#     backup = ROOT / "day10_backups" / stamp
#     backup.mkdir(parents=True)
#     for relative, data in originals.items():
#         if relative in changes:
#             dest = backup / relative
#             dest.parent.mkdir(parents=True, exist_ok=True)
#             dest.write_bytes(data)
#     new_files = [relative for relative in changes if relative not in originals]
#     (backup / "NEW_FILES.txt").write_text("\n".join(new_files), encoding="utf-8")
#     written = []
#     try:
#         for relative, content in changes.items():
#             dest = ROOT / relative
#             dest.parent.mkdir(parents=True, exist_ok=True)
#             written.append(relative)
#             dest.write_text(content, encoding="utf-8")
#     except Exception:
#         for relative in reversed(written):
#             dest = ROOT / relative
#             if relative in originals:
#                 dest.write_bytes(originals[relative])
#             else:
#                 dest.unlink(missing_ok=True)
#         raise
#     (backup / "CHANGED_FILES.txt").write_text("\n".join(sorted(changes)), encoding="utf-8")
#     print("Day 10 source update installed. No application code or tests were run.")
#     print("Backup:", backup)
#     print("Updated files:")
#     for relative in sorted(changes):
#         print(" -", relative)
#     print("Restart with: streamlit run app/main.py")


# if __name__ == "__main__":
#     try:
#         install()
#     except Exception as exc:
#         raise SystemExit(str(exc))
