import io
import json
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import benchmark_text_prefix as bench


class BenchmarkTests(unittest.TestCase):
    def test_offline_cases_reuse_only_leading_passages(self):
        with patch.object(bench.gateway, "structured", side_effect=AssertionError("Unexpected provider call")):
            lengths = {key: bench.offline_pair(pair, "evidence_first") for key, pair in bench.cases().items()}
        self.assertGreater(lengths["identical_passages"], lengths["shared_first_passage"])
        self.assertGreater(lengths["shared_first_passage"], lengths["different_first_passage"])

    def test_missing_cache_metric_is_distinct_from_zero(self):
        self.assertIsNone(bench.measure_call({"usage": {"prompt_tokens": 100}})["cached_tokens"])
        event = {"usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 0}}}
        self.assertEqual(bench.measure_call(event)["cached_tokens"], 0)
        event["usage"]["prompt_tokens_details"]["cached_tokens"] = 80
        self.assertEqual(bench.measure_call(event)["cached_tokens"], 80)

    def test_live_path_pairs_equivalent_payloads_and_reverses_order(self):
        reply = bench.text.GroundedResult(verdict="UNKNOWN", confidence=0, reason="Fixture.")
        output = io.StringIO()
        with patch.object(bench.gateway, "structured", return_value=reply) as gateway, redirect_stdout(output):
            status = bench.run_trials({"same": bench.cases()["identical_passages"]}, Namespace(rounds=2))
        self.assertEqual(status, 0)
        prompts = [json.loads(call.args[1]) for call in gateway.call_args_list]
        self.assertEqual(prompts[:2], prompts[2:4])
        self.assertEqual([next(iter(p)) for p in prompts],
                         ["claim", "claim", "evidence", "evidence", "evidence", "evidence", "claim", "claim"])
        comparisons = [r for r in map(json.loads, output.getvalue().splitlines()) if r["mode"] == "comparison"]
        self.assertEqual(len(comparisons), 2)
        self.assertTrue(all(r["same_verdicts_and_citations"] for r in comparisons))
        self.assertTrue(all(r["same_reported_route"] is None for r in comparisons))

    def test_provider_failure_stops_trials_without_a_speed_comparison(self):
        output = io.StringIO()
        with patch.object(bench.gateway, "structured", side_effect=bench.gateway.ModelServiceError("429")), redirect_stdout(output):
            status = bench.run_trials(bench.cases(), Namespace(rounds=2))
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")
