"""Compare free models: one case by default, or 39 cases on three models with --detailed.

python scripts/compare_claim_models.py --detailed          # preview, no calls
python scripts/compare_claim_models.py --detailed --run    # up to 117 calls
python scripts/compare_claim_models.py --detailed --run --only ernie
Reports record answers, errors, tokens and timings. No app imports or changes.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

MODELS = (
    "inclusionai/ling-3.2-flash:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "baidu/ernie-5.5-flash-preview:free",
    "liquid/lfm-3-1.7b:free",
)
SHORTLIST = {
    "ernie": "baidu/ernie-5.5-flash-preview:free",
    "ling": "inclusionai/ling-3.2-flash:free",
    "nemotron": "nvidia/nemotron-3.5-super-vl:free",
}


def summarize(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    results = report["results"]
    answered = [r for r in results if r["outcome"] != "provider_error"]
    timings = [r["seconds"] for r in answered]
    summary = dict(model=report["model"], report=str(path), **report["summary"])
    summary.update(
        mean_seconds=round(statistics.mean(timings), 3) if timings else None,
        median_seconds=round(statistics.median(timings), 3) if timings else None,
        prompt_tokens=sum((r.get("usage") or {}).get("prompt_tokens", 0) or 0 for r in results),
        completion_tokens=sum((r.get("usage") or {}).get("completion_tokens", 0) or 0 for r in results),
        reasoning_tokens=sum(((r.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0 for r in results),
        cases=[{k: r.get(k) for k in ("case", "outcome", "errors", "seconds", "http_status", "provider_message")} for r in results],
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--detailed", action="store_true", help="All 39 cases, ERNIE/Ling/Nemotron only; continue after interpretation failures")
    parser.add_argument("--only", choices=tuple(SHORTLIST), help="Run one shortlisted model, useful when free quotas are limited")
    args = parser.parse_args()
    checker = Path(__file__).resolve().with_name("check_claim_interpretation.py")
    models = [SHORTLIST[args.only]] if args.only else list(SHORTLIST.values()) if args.detailed else MODELS
    selection = ["--all", "--keep-going"] if args.detailed else ["--case", "wrong_claim_preserved"]
    cases = 39 if args.detailed else 1
    print(f"Up to {cases * len(models)} calls; one attempt per case, prompt v3, 2048 output tokens per call.", flush=True)
    comparison = dict(created=datetime.now(timezone.utc).isoformat(), planned_calls=cases*len(models),
                      suite="detailed" if args.detailed else "single_case", results=[])
    summary_path = None
    if args.run:
        folder = checker.parents[1] / "tmp" / ("claim-model-comparison-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        folder.mkdir(parents=True)
        summary_path = folder / "comparison.json"
    outcomes = []
    for model in models:
        print(f"Model: {model} | cases: {cases} | prompt: v3 | max output: 2048", flush=True)
        if args.run:
            report_path = None
            # Stream progress and capture only the report path, never credentials.
            with subprocess.Popen([sys.executable, str(checker), "--run", *selection,
                                   "--model", model, "--prompt-version", "v3", "--max-tokens", "2048"],
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace") as process:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    if line.startswith("Report: "):
                        report_path = line.removeprefix("Report: ").strip()
                code = process.wait()
            outcomes.append((model, code))
            result = summarize(report_path) if report_path else dict(model=model, status="no_report_preflight_failed")
            comparison["results"].append(dict(result, exit_code=code))
            summary_path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
            if model != models[-1]:
                time.sleep(4)
    if not args.run:
        print("No requests made. Add --run to execute; --only ernie/ling/nemotron selects one model.")
    else:
        print("\nChecker exit codes (0=pass; nonzero=inspect that model's report/error):")
        for model, code in outcomes:
            print(f"{model}: {code}")
        print("Comparison:", summary_path)
    return int(any(code != 0 for _, code in outcomes))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Comparison interrupted. Completed reports remain saved.")
        sys.exit(130)
