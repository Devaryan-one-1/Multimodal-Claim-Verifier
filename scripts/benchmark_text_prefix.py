"""Compare Text Agent prompt layouts on synthetic Fern Quay evidence.

Default: offline prefix lengths. --live: timed, validated Text Agent calls.
--live --review: time full hybrid reviews including retrieval, after one PDF indexing run.
Output is JSON Lines. Missing provider measurements are JSON null.
"""
import argparse
from contextlib import chdir, redirect_stdout
import json
import os
from os.path import commonprefix
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from agents import text
from services import model_gateway as gateway

PDF = ROOT / "tests/fixtures/worker_rollout/fern-quay.pdf"
CLAIMS = ("Fern Quay's Table 4 revenue figure covers only the retail segment.",
          "All operating segments are included in Fern Quay's Table 4 revenue figure.")
LAYOUTS = ("claim_first", "evidence_first")


def cases():
    with pymupdf.open(PDF) as pdf:
        pages = [dict(id=f"fern-quay-p{i}", type="text", document_id="fern-quay",
                      page=i, content=page.get_text().strip()) for i, page in enumerate(pdf, 1)]
    table, note = pages
    # A separately addressable passage extracted from the same report.
    reference, = [line for line in table["content"].splitlines() if "See Note 1" in line]
    pointer = dict(table, id="fern-quay-reference", content=reference)
    return {
        "identical_passages": ([note, table], [note, table]),
        "shared_first_passage": ([note, table], [note, pointer]),
        "different_first_passage": ([note, table], [table, note]),
    }


def serialize(prompt, layout):
    if layout == "evidence_first":
        return prompt
    body = json.loads(prompt)
    return json.dumps({"claim": body.pop("claim"), **body}, ensure_ascii=False)


def offline_pair(pair, layout):
    reply = text.GroundedResult(verdict="UNKNOWN", confidence=0, reason="Offline measurement.")
    with patch.object(text, "structured", return_value=reply) as model:
        for claim, records in zip(CLAIMS, pair):
            text.review_text_claim({"claim_text": claim}, records)
    first, second = model.call_args_list
    assert first.kwargs == second.kwargs
    prompts = [serialize(call.args[1], layout) for call in (first, second)]
    return len(commonprefix(prompts))


def measure_call(event):
    usage = event.get("usage") or {}
    return dict(model=event.get("actual_model"), upstream=event.get("upstream_provider"), purpose=event.get("purpose"),
                status=event.get("status"), http_status=event.get("http_status"),
                retry_after_seconds=event.get("retry_after_seconds"),
                prompt_tokens=usage.get("prompt_tokens"),
                cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"))


def timed(layout, run):
    def send(schema, prompt, **options):
        if options.get("purpose") == "text_verification":
            prompt = serialize(prompt, layout)
        return gateway.structured(schema, prompt, **options)

    with patch.object(text, "structured", send), gateway.capture_calls(isolated=True) as (calls, _):
        started = perf_counter()
        try:
            results = run()
            outcome = dict(status="complete" if results and all(r["status"] == "complete" for r in results)
                           else "error", results=results)
        except (text.VerificationFailure, gateway.ModelServiceError) as exc:
            outcome = dict(status="error", error_type=type(exc).__name__, stage=getattr(exc, "stage", None))
        return dict(**outcome, seconds=round(perf_counter() - started, 3),
                    calls=[measure_call(event) for event in calls])


def agent_review(claim, records):
    result = text.review_text_claim({"claim_text": claim}, records)
    return [dict(status="complete", verdict=result.verdict, citation_ids=result.evidence_ids)]


def emit(**fields):
    print(json.dumps(fields, ensure_ascii=True), flush=True)


def run_trials(groups, args, review=None):
    for trial in range(args.rounds):
        for index, (name, pair) in enumerate(groups.items()):
            layouts = LAYOUTS if (trial + index) % 2 == 0 else LAYOUTS[::-1]
            followups = {}
            for layout in layouts:
                for number, (claim, records) in enumerate(zip(CLAIMS, pair), 1):
                    result = timed(layout, lambda: review(claim) if review else agent_review(claim, records))
                    emit(mode="review" if review else "agent", case=name, round=trial + 1,
                         layout=layout, position=number, claim=claim, **result)
                    if result["status"] != "complete":
                        return 1
                    if number == 2:
                        followups[layout] = result
            old, new = (followups[layout] for layout in LAYOUTS)
            emit(mode="comparison", case=name, round=trial + 1,
                 second_request_seconds_saved=round(old["seconds"] - new["seconds"], 3),
                 same_verdicts_and_citations=old["results"] == new["results"],
                 same_reported_route=route_match(old, new))
    return 0


def route_match(old, new):
    def routes(result):
        return {(c["model"], c["upstream"]) for c in result["calls"] if c["purpose"] == "text_verification"}
    a, b = routes(old), routes(new)
    if not a or not b or any(not model or not provider for model, provider in a | b):
        return None
    return a == b


def review_worker(scratch, args):
    # Release the index's native file handles before the parent cleans up on Windows.
    with chdir(scratch), patch.dict(os.environ, {"CUSTOMER_STORAGE": "local"}):
        from pipelines.document_pipeline import process_document
        from pipelines.verification_pipeline import verify_claims
        from pilot.model_connections import model_config
        gateway.configure(model_config(text.TEXT_MODEL, gateway.CHART_EXTRACTION_MODEL))
        doc = uuid4().hex
        with redirect_stdout(sys.stderr):
            process_document(PDF.read_bytes(), PDF.name, doc, "Fern Quay")

        def review(claim):
            with redirect_stdout(sys.stderr):
                results = verify_claims(claim, [doc])
            return [dict(status=r["status"], verdict=r.get("verdict"), citation_ids=r.get("evidence_ids", []),
                         stage=r.get("error_stage")) for r in results]

        # Let retrieval choose passages for each claim, as it does in the dashboard.
        sys.exit(run_trials({"retrieved_evidence": (None, None)}, args, review))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send synthetic claims to OpenRouter")
    parser.add_argument("--review", action="store_true", help="run full reviews with real retrieval")
    parser.add_argument("--rounds", type=int, default=1, help="live repetitions, reversing layout order each round")
    args = parser.parse_args()
    if args.rounds < 1 or (args.review and not args.live):
        parser.error("Use a positive round count. --review requires --live.")
    groups = cases()
    if not args.live:
        for name, pair in groups.items():
            emit(mode="offline", case=name, **{layout: offline_pair(pair, layout) for layout in LAYOUTS},
                 unit="shared user-message characters")
        return 0

    from dotenv import load_dotenv
    from pilot.model_connections import model_config
    load_dotenv(ROOT / ".env", override=False)
    gateway.configure(model_config(text.TEXT_MODEL, gateway.CHART_EXTRACTION_MODEL))
    if not args.review:
        return run_trials(groups, args)

    from multiprocessing import get_context
    with TemporaryDirectory() as scratch:
        worker = get_context("spawn").Process(target=review_worker, args=(scratch, args))
        worker.start()
        worker.join()
        return worker.exitcode


if __name__ == "__main__":
    sys.exit(main())
