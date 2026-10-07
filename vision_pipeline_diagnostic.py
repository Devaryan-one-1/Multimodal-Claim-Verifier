from __future__ import annotations

import argparse
import hashlib
import json
import os
import traceback
from pathlib import Path
from typing import Any

import pymupdf
from dotenv import load_dotenv

load_dotenv()

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def check(status: str, name: str, detail: str = "") -> None:
    symbol = f"[{status}]"
    print(f"{symbol:<8} {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"         {line}")


def dump(label: str, value: Any) -> None:
    print(f"\n{label}:")
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_synthetic_chart(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    page = doc.new_page(width=800, height=600)

    page.insert_text((180, 50), "International Revenue", fontsize=22)
    page.insert_text((260, 80), "USD billions", fontsize=14)

    page.draw_line((120, 480), (120, 130))
    page.draw_line((120, 480), (680, 480))

    for value, y in [(0, 480), (5, 330), (10, 180)]:
        page.insert_text((80, y + 5), str(value), fontsize=12)
        page.draw_line((115, y), (680, y), color=(0.8, 0.8, 0.8))

    page.draw_rect(
        pymupdf.Rect(220, 180, 330, 480),
        fill=(0.35, 0.35, 0.35),
    )
    page.draw_rect(
        pymupdf.Rect(440, 150, 550, 480),
        fill=(0.6, 0.6, 0.6),
    )

    page.insert_text((255, 165), "10", fontsize=14)
    page.insert_text((475, 135), "11", fontsize=14)

    page.insert_text((250, 510), "2024", fontsize=14)
    page.insert_text((470, 510), "2025", fontsize=14)

    pix = page.get_pixmap(dpi=200, alpha=False)
    pix.save(str(output_path))
    doc.close()

    return output_path


def run_diagnostics(
    *,
    overwrite: bool,
    use_existing_image: str | None,
    page_image: str | None,
) -> int:
    banner("VISION PIPELINE DIAGNOSTIC")

    failures: list[str] = []
    warnings: list[str] = []

    # 1. Import production module
    try:
        import app.reasoning.vision_client as vc
        check(PASS, "Import app.reasoning.vision_client")
    except Exception as exc:
        check(FAIL, "Import app.reasoning.vision_client", repr(exc))
        traceback.print_exc()
        return 1

    # 2. Check required production symbols
    required_symbols = [
        "analyze_chart",
        "RawChartExtraction",
        "ChartAnalysisResponse",
        "_normalize_raw_extraction",
        "_readable_value_count",
        "_build_facts",
        "_build_prompt",
        "_analyze_with_gemini",
        "_cache_path",
    ]

    for symbol in required_symbols:
        if hasattr(vc, symbol):
            check(PASS, f"vision_client.{symbol}")
        else:
            check(FAIL, f"vision_client.{symbol}", "Missing symbol")
            failures.append(f"Missing symbol: {symbol}")

    if failures:
        banner("STOPPING EARLY")
        return 1

    # 3. Configuration
    banner("CONFIGURATION")

    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        check(PASS, "GOOGLE_API_KEY configured", f"Length: {len(api_key)} chars")
    else:
        check(FAIL, "GOOGLE_API_KEY configured", "Missing environment variable")
        failures.append("GOOGLE_API_KEY missing")

    primary_model = getattr(vc, "PRIMARY_MODEL", getattr(vc, "DEFAULT_MODEL", None))
    fallback_model = getattr(vc, "FALLBACK_MODEL", None)

    check(PASS if primary_model else FAIL, "Primary model", str(primary_model))
    if fallback_model:
        check(PASS, "Fallback model", str(fallback_model))
    else:
        check(WARN, "Fallback model", "No fallback configured")
        warnings.append("No fallback model configured")

    # 4. Schema validation
    banner("SCHEMA VALIDATION")

    try:
        schema = vc.RawChartExtraction.model_json_schema()
        props = schema.get("properties", {})
        if "data_points" in props:
            check(PASS, "RawChartExtraction contains data_points")
        else:
            check(FAIL, "RawChartExtraction contains data_points")
            failures.append("RawChartExtraction lacks data_points")
        dump("RawChartExtraction properties", list(props.keys()))
    except Exception as exc:
        check(FAIL, "Build RawChartExtraction schema", repr(exc))
        failures.append("Schema generation failed")

    # 5. Prompt check
    banner("PROMPT CHECK")

    sample_chart_for_prompt = {
        "chart_id": "diagnostic_chart_001",
        "document_id": "diagnostic_test",
        "page": 1,
        "chart_type": "bar",
        "title": "International Revenue",
        "bbox": [0, 0, 100, 100],
    }

    try:
        prompt = vc._build_prompt(
            chart=sample_chart_for_prompt,
            full_page=False,
        )

        prompt_checks = {
            "mentions data points": (
                "data point" in prompt.lower()
                or "data_points" in prompt.lower()
            ),
            "mentions explicit_label": "explicit_label" in prompt,
            "mentions estimated_from_axis": "estimated_from_axis" in prompt,
            "mentions unreadable": "unreadable" in prompt,
            "says legend is not required": (
                "legend is not required" in prompt.lower()
            ),
        }

        for name, ok in prompt_checks.items():
            check(PASS if ok else WARN, name)
            if not ok:
                warnings.append(f"Prompt check: {name}")
    except Exception as exc:
        check(FAIL, "Build prompt", repr(exc))
        failures.append("Prompt build failed")

    # 6. Image
    banner("IMAGE CHECK")

    if use_existing_image:
        chart_path = Path(use_existing_image)
    else:
        chart_path = Path("data/test_assets/diagnostic_revenue_chart.png")
        try:
            create_synthetic_chart(chart_path)
            check(PASS, "Create synthetic chart", str(chart_path))
        except Exception as exc:
            check(FAIL, "Create synthetic chart", repr(exc))
            traceback.print_exc()
            return 1

    if not chart_path.exists():
        check(FAIL, "Chart image exists", str(chart_path))
        return 1

    check(PASS, "Chart image exists", str(chart_path))

    try:
        pix = pymupdf.Pixmap(str(chart_path))
        width, height = pix.width, pix.height
        check(PASS, "Chart image dimensions", f"{width} x {height}")
    except Exception as exc:
        check(FAIL, "Read chart image", repr(exc))
        return 1

    sha = file_hash(chart_path)
    check(PASS, "Chart image SHA256", sha)

    # 7. Chart object
    banner("CHART OBJECT")

    chart = {
        "chart_id": "diagnostic_chart_001",
        "document_id": "diagnostic_test",
        "page": 1,
        "chart_type": "bar",
        "title": "International Revenue",
        "bbox": [0, 0, width, height],
        "image_path": str(chart_path),
        "evidence": {
            "evidence_id": "diagnostic_chart_evidence_001"
        },
    }

    for field in ["chart_id", "document_id", "page", "chart_type", "image_path"]:
        ok = chart.get(field) is not None
        check(PASS if ok else FAIL, f"chart[{field!r}]", str(chart.get(field)))
        if not ok:
            failures.append(f"Missing chart field: {field}")

    # 8. Direct model tests
    banner("DIRECT MODEL TESTS")

    model_results: dict[str, Any] = {}
    models_to_test: list[tuple[str, str]] = []

    if primary_model:
        models_to_test.append(("primary", primary_model))
    if fallback_model and fallback_model != primary_model:
        models_to_test.append(("fallback", fallback_model))

    for role, model in models_to_test:
        print(f"\n--- {role.upper()} MODEL: {model} ---")

        try:
            analysis = vc._analyze_with_gemini(
                image_path=chart_path,
                chart=chart,
                full_page=False,
                model=model,
            )

            readable = vc._readable_value_count(analysis)

            model_results[model] = {
                "success": True,
                "readable_values": readable,
                "analysis": analysis.model_dump(mode="json"),
            }

            check(PASS, f"{model}: API + parse + normalization")

            if readable > 0:
                check(PASS, f"{model}: readable values", str(readable))
            else:
                check(
                    WARN,
                    f"{model}: readable values",
                    "0 — semantic chart-value extraction failed",
                )
                warnings.append(f"{model} returned zero readable values")

            dump(
                f"{model} normalized analysis",
                analysis.model_dump(mode="json"),
            )

        except Exception as exc:
            model_results[model] = {
                "success": False,
                "error_type": type(exc).__name__,
                "reason": str(exc),
            }

            text = str(exc)
            upper = text.upper()

            if "429" in upper or "RATE LIMIT" in upper or "RESOURCE_EXHAUSTED" in upper:
                check(WARN, f"{model}: model call", "Quota/rate limit:\n" + text)
                warnings.append(f"{model} quota/rate limit")
            elif "503" in upper or "UNAVAILABLE" in upper:
                check(WARN, f"{model}: model call", "Temporary service error:\n" + text)
                warnings.append(f"{model} temporary service failure")
            else:
                check(
                    FAIL,
                    f"{model}: model call",
                    f"{type(exc).__name__}: {exc}",
                )
                failures.append(f"{model} model call failed")

    # 9. Local normalization and fact-building test
    banner("LOCAL NORMALIZATION TEST")

    known_raw = {
        "chart_type": "bar",
        "title": "International Revenue",
        "display_unit": "USD billions",
        "normalized_unit": "USD billion",
        "x_axis": {
            "label": "Year",
            "unit": None,
            "values": ["2024", "2025"],
        },
        "y_axis": {
            "label": "Revenue",
            "unit": "USD billions",
            "values": ["0", "5", "10"],
        },
        "legend": [],
        "data_points": [
            {
                "series_name": "International Revenue",
                "metric": "revenue",
                "segment": "international",
                "period": "2024",
                "category": None,
                "value": 10,
                "display_value": "10",
                "value_source": "explicit_label",
            },
            {
                "series_name": "International Revenue",
                "metric": "revenue",
                "segment": "international",
                "period": "2025",
                "category": None,
                "value": 11,
                "display_value": "11",
                "value_source": "explicit_label",
            },
        ],
        "warnings": [],
    }

    try:
        parsed = vc.RawChartExtraction.model_validate(known_raw)
        check(PASS, "Pydantic parses known data_points")

        normalized = vc._normalize_raw_extraction(parsed)
        check(PASS, "Normalize data_points -> series")

        readable = vc._readable_value_count(normalized)

        if readable == 2:
            check(PASS, "Readable values after normalization", "2")
        else:
            check(
                FAIL,
                "Readable values after normalization",
                f"Expected 2, got {readable}",
            )
            failures.append("Normalization dropped known values")

        facts = vc._build_facts(
            analysis=normalized,
            chart=chart,
        )

        if len(facts) == 2:
            check(PASS, "Build facts", "2 facts")
        else:
            check(
                FAIL,
                "Build facts",
                f"Expected 2, got {len(facts)}",
            )
            failures.append("Fact builder dropped known values")

        dump("Known normalized analysis", normalized.model_dump(mode="json"))
        dump("Known facts", facts)

    except Exception as exc:
        check(
            FAIL,
            "Local normalization/fact test",
            f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        failures.append("Local normalization test failed")

    # 10. Public end-to-end path
    banner("PUBLIC analyze_chart TEST")

    try:
        result = vc.analyze_chart(
            chart,
            overwrite=overwrite,
            page_image_path=(Path(page_image) if page_image else None),
        )

        dump("Final analyze_chart result", result)

        if result.get("success"):
            check(PASS, "analyze_chart success=True")
        else:
            check(FAIL, "analyze_chart success=True", str(result.get("reason")))
            failures.append("analyze_chart failed")

        check(INFO, "Final status", str(result.get("status")))

        readable = result.get("readable_value_count", 0) or 0
        usable = result.get("usable_for_numeric_verification")

        if readable > 0 and usable is True:
            check(
                PASS,
                "Final numeric usability",
                f"{readable} readable value(s)",
            )
        else:
            check(
                WARN,
                "Final numeric usability",
                f"usable={usable}, readable_values={readable}",
            )
            warnings.append("Final output has no usable chart values")

        attempts = result.get("attempts", [])
        check(
            PASS if attempts else WARN,
            "Attempt provenance",
            f"{len(attempts)} attempt(s)",
        )

        facts = result.get("facts", [])
        check(
            PASS if facts else WARN,
            "Final facts",
            f"{len(facts)} fact(s)",
        )

    except Exception as exc:
        check(
            FAIL,
            "Public analyze_chart",
            f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        failures.append("Public analyze_chart exception")
        result = None

    # 11. Cache
    banner("CACHE CHECK")

    try:
        cache_path = vc._cache_path(chart["document_id"])
        check(INFO, "Expected cache path", str(cache_path))

        if cache_path.exists():
            check(PASS, "Cache file exists")
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            check(PASS, "Cache JSON valid")

            cached_entry = (
                cache_data
                .get("charts", {})
                .get(chart["chart_id"])
            )

            if cached_entry:
                check(PASS, "Diagnostic chart cache entry exists")
                if cached_entry.get("image_hash") == sha:
                    check(PASS, "Cached image hash matches")
                else:
                    check(
                        WARN,
                        "Cached image hash matches",
                        f"cached={cached_entry.get('image_hash')} current={sha}",
                    )
            else:
                check(WARN, "Diagnostic chart cache entry exists", "No entry found")
        else:
            check(WARN, "Cache file exists", "No cache file yet")

    except Exception as exc:
        check(WARN, "Cache diagnostics", repr(exc))
        warnings.append("Cache diagnostics failed")

    # 12. Root cause
    banner("ROOT-CAUSE SUMMARY")

    successful_models = [
        (model, item)
        for model, item in model_results.items()
        if item.get("success") is True
    ]

    all_successful_models_have_zero = (
        bool(successful_models)
        and all(
            item.get("readable_values", 0) == 0
            for _, item in successful_models
        )
    )

    if all_successful_models_have_zero:
        check(
            WARN,
            "Likely break point",
            (
                "Vision-model semantic extraction. API calls succeed and "
                "Python normalization is testable separately, but the models "
                "return zero usable chart values."
            ),
        )
        print(
            "\nRecommended next step:\n"
            "  Implement a specialized chart-mark fallback (bar/line/pie)\n"
            "  instead of adding more generic prompt text."
        )
    elif failures:
        check(
            FAIL,
            "Likely break point",
            "One or more local/API stages failed. See FAIL checks above.",
        )
    else:
        check(PASS, "Pipeline", "No hard break detected.")

    # 13. Final report
    banner("FINAL REPORT")

    print(f"Hard failures : {len(failures)}")
    print(f"Warnings      : {len(warnings)}")

    if failures:
        print("\nFailures:")
        for item in failures:
            print(f"  - {item}")

    if warnings:
        print("\nWarnings:")
        for item in warnings:
            print(f"  - {item}")

    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end diagnostic for app/reasoning/vision_client.py"
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Ignore cached chart-analysis results.",
    )

    parser.add_argument(
        "--image",
        default=None,
        help="Optional existing chart PNG. Otherwise a synthetic chart is created.",
    )

    parser.add_argument(
        "--page-image",
        default=None,
        help="Optional full-page PNG to exercise the page fallback.",
    )

    args = parser.parse_args()

    raise SystemExit(
        run_diagnostics(
            overwrite=args.overwrite,
            use_existing_image=args.image,
            page_image=args.page_image,
        )
    )


if __name__ == "__main__":
    main()
