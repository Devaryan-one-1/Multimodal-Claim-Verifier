"""Offline comparison of the source-scoped, claim-only and default hybrid dashboard workflows.

Run this file directly to print the verdicts and their citations side by side.
Model replies and retrieval rankings are controlled. Parsing, source-address
validation, parallel verification, arithmetic and verdict combination use app code.
"""
import json
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_claim_parser as parser_fixtures
import test_evidence_interpreter as interpreter_fixtures
from reasoning.evidence_fusion import fuse_evidence


DOC = "doc-a"
EXPLICIT = "Annual customer count was 120 in 2026."
RELATIVE = "Annual customer count was 120 in the latest reported year."
WORKFLOWS = ("Source-scoped", "Claim-only", "Hybrid")


def source_records(table_count, chart_count, text_count):
    records = []
    if table_count is not None:
        records.append(dict(id="table", type="table", page=1, title="Annual customer count",
                            headers=["Metric", "2026"], rows=[["Customer count", str(table_count)]],
                            column_map=[dict(col=1, year=2026, period="annual")], unit="customers"))
    if chart_count is not None:
        facts = [dict(category=year, display_value=str(value), value=value, unit="customers",
                      value_source="explicit_label") for year, value in (("2025", 80), ("2026", chart_count))]
        records.append(dict(id="chart", type="chart", page=2, title="Annual customer count",
                            unit="customers", x_axis={"label": "Year"}, facts=facts))
    if text_count is not None:
        records.append(dict(id="text", type="text", page=3,
                            content=f"Annual customer count was {text_count} in 2026."))
    return [dict(item, document_id=DOC, company="Northstar") for item in records]


class WorkflowComparisonTests(unittest.TestCase):
    def setUp(self):
        self.parser = parser_fixtures.ClaimParserTests()
        self.parser.setUp()
        self.addCleanup(self.parser.doCleanups)
        self.env = interpreter_fixtures.EvidenceInterpreterTests()
        self.env.setUp()
        self.addCleanup(self.env.doCleanups)
        self.pipeline = self.env.pipeline()
        self.pipeline["parse_claims"] = self.parser.parser.parse_claims
        self.pipeline["fuse_evidence"] = fuse_evidence
        self.env.document["company_name"] = "Northstar"
        self.enterContext(patch.object(sys.modules["services.model_gateway"], "CHART_EXTRACTION_MODEL",
                                       "fixture/vision", create=True))
        self.env.numeric.structured.side_effect = self.text_selector
        self.enterContext(patch("agents.text.structured", side_effect=self.text_selector))
        self.label_model = self.enterContext(patch("verification.source_conflicts.structured",
                                                   side_effect=self.read_labels))

    def read_labels(self, schema, prompt, **kwargs):
        self.assertEqual(kwargs["purpose"], "source_reconciliation")
        readings = []
        for packet in json.loads(prompt)["findings"]:
            labels = [re.search(r"(?:total |retail )?(?:customer count|revenue)", s["wording"], re.I).group()
                      for s in packet["subjects"]]
            retail = any("retail" in label.lower() for label in labels)
            readings.append(dict(index=packet["index"], scope="different" if retail else "matches",
                                 labels=labels, scope_quote=labels[0] if retail else ""))
        return SimpleNamespace(readings=[SimpleNamespace(**r) for r in readings], same_meaning=True)

    def text_selector(self, schema, prompt, **kwargs):
        # The first number in the fixture sentence is the count; the second is the year.
        source, = json.loads(prompt)["evidence"]
        self.assertEqual(source["type"], "text")
        address = dict(name="value", evidence_id=source["id"], number_index=0, row_index=None, column_index=None)
        operand = address if hasattr(schema, "model_validate") else self.env.numeric.Operand(**address)
        return schema(applicable=True, reason="Count in the annual sentence.", operands=[operand])

    def interpreter_reply(self, wording, suggested):
        def model_reply(schema, prompt, **kwargs):
            request = json.loads(prompt)
            self.assertEqual(request["claim"], wording)
            record = next(e for e in request["evidence"] if e["id"] == suggested)
            period = max(record["periods"].values(), key=lambda p: p["year"])
            atom = self.env.atom(metric="customer count", literal="120", operation="level", unit="count",
                                 direction="flat", approximate=False, start=None, end=period["key"],
                                 sources=[suggested])
            return SimpleNamespace(data=self.env.response(atom))

        return model_reply

    def compare(self, scenario, wording, counts, hint, expected, *, missed=()):
        records = source_records(*counts)
        self.env.storage.side_effect = lambda path: (self.env.document if path.endswith("document.json")
                                                    else {"evidence": records})
        self.pipeline["search"] = lambda *a, **kw: [dict(document_id=DOC, source_evidence_id=r["id"])
                                                   for r in records]
        independent_hits = {k: [] for k in ("text", "table", "chart")}
        for r in records:
            if r["id"] not in missed:
                independent_hits[r["type"]].append(dict(document_id=DOC, source_evidence_id=r["id"]))

        explicit = wording == EXPLICIT
        response = self.parser.atom(wording, company=None, metric="customer count", subject="customer count",
                                    claimed_literal="120", unit="number", period_type="annual",
                                    end_period=2026 if explicit else None, period_quote="2026" if explicit else None)
        self.parser.respond([response])
        self.env.search.return_value = independent_hits
        results = {}
        with patch.object(self.env.module, "structured", side_effect=self.interpreter_reply(wording, f"{DOC}::{hint}")):
            for mode, options in zip(WORKFLOWS, ({"workflow": "source_scoped"},
                                                 {"workflow": "independent_modalities"}, {}), strict=True):
                claims, prepared = self.pipeline["prepare_verification"](wording, [DOC], **options)
                self.assertNotIn("parsing_error", claims[0], claims[0])
                if mode == "Hybrid":
                    self.assertEqual(prepared["workflow"], "hybrid")
                    self.assertEqual(claims[0]["suggested_source_ids"], [f"{DOC}::{hint}"])
                    self.assertNotIn("meaning_source_ids", claims[0])
                results[mode] = self.pipeline["_verify_one"](claims[0], [DOC], prepared=prepared)

        cells = []
        for mode, verdict in zip(WORKFLOWS, expected, strict=True):
            result = results[mode]
            with self.subTest(scenario=scenario, workflow=mode):
                self.assertEqual(result["status"], "complete", result.get("reason"))
                self.assertEqual(result["verdict"], verdict, result.get("reason"))
                self.assertEqual(result["parsed_claim"]["claimed_literal"], "120")
                if mode != "Source-scoped":
                    self.assertEqual(set(result["evidence_checks"]), set(independent_hits))
            ids = sorted(s.rsplit("::", 1)[-1] for s in result["evidence_ids"])
            cells.append(f"{result['verdict']} ({', '.join(ids) or 'no citation'})")
        if __name__ == "__main__":
            print(f"{scenario:<25} " + " | ".join(f"{cell:<34}" for cell in cells), flush=True)
        return results

    def test_agreement(self):
        results = self.compare("All three agree", EXPLICIT, (120, 120, 120), "table", ("SUPPORTED",) * 3)
        self.label_model.assert_not_called()
        self.assertEqual(results["Source-scoped"]["evidence_ids"], [f"{DOC}::table"])
        for mode in ("Claim-only", "Hybrid"):
            self.assertEqual(set(results[mode]["evidence_ids"]), {f"{DOC}::{k}" for k in ("text", "table", "chart")})
        text_check = results["Hybrid"]["evidence_checks"]["text"]
        self.assertEqual(text_check["calculation"]["evidence_agent"], "text")
        self.assertEqual(text_check["quotes"], [{"evidence_id": f"{DOC}::text", "quote": EXPLICIT}])

    def test_conflict_outside_suggested_source(self):
        results = self.compare("Chart disagrees", EXPLICIT, (120, 100, 120), "table",
                               ("SUPPORTED", "UNKNOWN", "SOURCE_CONFLICT"))
        for mode in ("Claim-only", "Hybrid"):
            checks = results[mode]["evidence_checks"]
            self.assertEqual(checks["chart"]["calculation"]["value"], 100)
            self.assertEqual(checks["chart"]["verdict"], "CONTRADICTED")
            self.assertEqual(checks["text"]["verdict"], "SUPPORTED")

    def test_hint_recovers_source_missing_from_independent_ranking(self):
        results = self.compare("Table missed by search", EXPLICIT, (100, 120, None), "table",
                               ("CONTRADICTED", "SUPPORTED", "SOURCE_CONFLICT"), missed=("table",))
        self.assertEqual(results["Claim-only"]["evidence_ids"], [f"{DOC}::chart"])
        self.assertEqual(set(results["Hybrid"]["evidence_ids"]), {f"{DOC}::chart", f"{DOC}::table"})

    def verify_sources(self, records, reporter=None, **changes):
        self.env.storage.return_value = {"evidence": records}
        self.env.search.return_value = {
            kind: [dict(document_id=DOC, source_evidence_id=r["id"]) for r in records if r["type"] == kind]
            for kind in ("text", "table", "chart")}
        claim = dict(claim_type="assertion", operation="level", claim_text=EXPLICIT, metric="customer count",
                     claimed_literal="120", unit="number", period_type="annual", end_period=2026)
        claim.update(changes)
        return self.pipeline["_verify_one"](claim, [DOC], reporter, prepared={"workflow": "hybrid"})

    def footnote_case(self, wording="Retail segment only.", *, marker="¹", note_page=1, alternative=False):
        table = source_records(150, None, None)[0]
        table.update(title="Table 2. Revenue", rows=[["Revenue" + marker, "150"]],
                     context="USD millions", unit="USD millions", bbox=[20, 40, 200, 120])
        note = dict(id="note", type="footnote", document_id=DOC, page=note_page,
                    content="Note 1: " + wording, bbox=[20, 125, 185, 136])
        records = [table, note]
        if alternative:
            chart = source_records(None, 180, None)[0]
            chart.update(title="Consolidated annual revenue", unit="USD millions")
            for fact in chart["facts"]:
                fact.update(metric="Revenue", unit="USD millions")
            records.append(chart)
        return records

    def verify_revenue(self, records, reporter=None):
        return self.verify_sources(records, reporter, metric="revenue", claimed_literal="USD 150 million",
                                   unit="amount", claim_text="Consolidated revenue was USD 150 million in 2026.")

    def scope_reply(self, schema, prompt, **kwargs):
        self.assertEqual(kwargs["purpose"], "footnote_followup")
        answers = []
        for q in json.loads(prompt)["questions"]:
            quote = next((s for s in q["passages"] if "retail" in s.lower() or "consolidat" in s.lower()), "")
            scope = ("excluded" if "retail" in quote.lower() else "matches") if quote else "unclear"
            answers.append(SimpleNamespace(index=q["index"], scope=scope, quote=quote))
        return SimpleNamespace(answers=answers)

    def test_linked_footnote_restricts_or_confirms_scope(self):
        for note, expected in [("Retail segment only.", "UNKNOWN"), ("Includes all consolidated subsidiaries.", "SUPPORTED")]:
            with self.subTest(note=note), patch("agents.text.structured", side_effect=self.scope_reply) as model:
                result = self.verify_revenue(self.footnote_case(note))
                self.assertEqual(result["status"], "complete", result["reason"])
                self.assertEqual(result["verdict"], expected, result["reason"])
                self.assertEqual(result["footnote_checks"][0]["quote"], "Note 1: " + note)
                self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::table", f"{DOC}::note"})
                model.assert_called_once()

    def test_retail_table_yields_to_explicit_consolidated_chart_in_one_round(self):
        with patch("agents.text.structured", side_effect=self.scope_reply) as model:
            result = self.verify_revenue(self.footnote_case(alternative=True))
        self.assertEqual(result["verdict"], "CONTRADICTED", result["reason"])
        self.assertEqual(result["comparison"]["actual"], 180_000_000)
        self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::table", f"{DOC}::note", f"{DOC}::chart"})
        model.assert_called_once()
        self.assertEqual(len(json.loads(model.call_args.args[1])["questions"]), 2)

    def test_text_and_chart_disagree_after_retail_table_is_excluded(self):
        def answer(schema, prompt, **kwargs):
            if kwargs['purpose'] == 'footnote_followup':
                return self.scope_reply(schema, prompt, **kwargs)
            return self.text_selector(schema, prompt, **kwargs)

        for label, verdict in [('revenue', 'SOURCE_CONFLICT'), ('total revenue', 'SUPPORTED')]:
            with self.subTest(label=label), patch('agents.text.structured', side_effect=answer):
                records = self.footnote_case(alternative=True)
                prose = source_records(None, None, 150)[0]
                prose['content'] = f'Consolidated {label} was USD 150 million in 2026.'
                result = self.verify_revenue([*records, prose])
                self.assertEqual(result['verdict'], verdict, result['reason'])
                self.assertEqual(result['footnote_checks'][0]['scope'], 'excluded')
                decision = result['source_resolution']
                self.assertEqual({f['kind'] for f in decision['findings']}, {'text', 'chart'})
                self.assertEqual({(e['id'], e['page']) for e in result['evidence']}, {
                    (f'{DOC}::table', 1), (f'{DOC}::note', 1), (f'{DOC}::chart', 2), (f'{DOC}::text', 3)})
                if verdict == 'SUPPORTED':
                    self.assertEqual(decision['status'], 'possible_source_conflict')
                    self.assertEqual(decision['findings'][decision['preferred']]['kind'], 'text')

    def test_row_and_column_notes_checked_together_and_both_must_match(self):
        records = self.footnote_case("Includes all consolidated subsidiaries.")
        records[0]["headers"][-1] = "2026[2]"
        records.append(dict(records[1], id="note2", content="Note 2: Retail segment only."))
        with patch("agents.text.structured", side_effect=self.scope_reply) as model:
            result = self.verify_revenue(records)
        self.assertEqual(result["verdict"], "UNKNOWN", result["reason"])
        self.assertEqual({c["marker"] for c in result["footnote_checks"]}, {"1", "2"})
        model.assert_called_once()
        self.assertEqual(len(json.loads(model.call_args.args[1])["questions"]), 2)

    def test_alternative_consolidated_table_determines_verdict(self):
        records = self.footnote_case()
        records.append(dict(records[0], id="group", page=3, title="Consolidated annual revenue",
                            rows=[["Revenue", "180"]]))
        # The joint selection uses the marked cell. The independent check also finds the group table.
        choice = self.env.numeric.Selection(applicable=True, reason="Selected revenue",
                     operands=[self.env.numeric.Operand(name="value", evidence_id=f"{DOC}::table",
                                                        row_index=0, column_index=1)])
        with patch("agents.table.structured", return_value=choice), patch("agents.text.structured", side_effect=self.scope_reply):
            result = self.verify_revenue(records)
        self.assertEqual(result["verdict"], "CONTRADICTED", result["reason"])
        self.assertEqual(result["comparison"]["actual"], 180_000_000)
        self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::table", f"{DOC}::note", f"{DOC}::group"})

    def test_invalid_followup_quote_pauses_and_does_not_publish_a_factual_verdict(self):
        reply = SimpleNamespace(answers=[SimpleNamespace(index=0, scope="matches", quote="Invented scope statement")])
        with patch("agents.text.structured", return_value=reply):
            result = self.verify_revenue(self.footnote_case())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_stage"], "footnote_followup")

    def test_duplicate_note_numbers_resolve_only_to_explicit_table_caption(self):
        records = self.footnote_case()
        second = dict(records[0], id="second", title="Table 3", rows=[["Expense¹", "300"]])
        records[1]["content"] = "Table 2, Note 1: Includes all consolidated subsidiaries."
        records.extend([second, dict(records[1], id="othernote", content="Table 3, Note 1: Retail segment only.")])
        with patch("agents.text.structured", side_effect=self.scope_reply), patch("agents.table.structured",
                return_value=self.env.numeric.Selection(applicable=False, reason="No matching metric", operands=[])):
            result = self.verify_revenue(records)
        self.assertEqual(result["verdict"], "SUPPORTED", result["reason"])
        self.assertEqual(result["footnote_checks"][0]["note_id"], f"{DOC}::note")

    def test_shared_page_with_second_table_cannot_borrow_its_note(self):
        records = self.footnote_case()
        records.append(dict(records[0], id="second", title="Table 3", rows=[["Expense¹", "300"]]))
        with patch("agents.table.structured",
                return_value=self.env.numeric.Selection(applicable=False, reason="No matching metric", operands=[])):
            result = self.verify_revenue(records)
        self.assertEqual(result["verdict"], "UNKNOWN", result["reason"])
        self.assertIsNone(result["footnote_checks"][0]["note_id"])

    def test_retry_after_footnote_failure_reuses_saved_findings_and_explicit_page(self):
        class Reporter:
            footnote_state = None
            def update(self, *args, **kwargs):
                pass
            def save_footnote(self, state):
                self.footnote_state = json.loads(json.dumps(state))
        reporter = Reporter()
        records = self.footnote_case("Includes all consolidated subsidiaries.", marker="¹ (see Note 1 on page 4)", note_page=4)
        with patch("agents.text.structured", side_effect=TimeoutError("timeout")):
            failed = self.verify_revenue(records, reporter)
        self.assertEqual(failed["error_stage"], "footnote_followup")
        self.assertEqual(reporter.footnote_state["plan"]["status"], "pending")
        self.pipeline["embed_query"].reset_mock()
        with patch("agents.text.structured", side_effect=self.scope_reply) as model:
            retried = self.verify_revenue(records, reporter)
            self.assertEqual(retried["verdict"], "SUPPORTED", retried["reason"])
            self.assertEqual(reporter.footnote_state["plan"]["status"], "answered")
            replay = self.verify_revenue(records, reporter)
            self.assertEqual(replay["footnote_checks"][0]["note_page"], 4)
            model.assert_called_once()
        self.pipeline["embed_query"].assert_not_called()

    def test_owned_note_refers_to_another_page(self):
        records = self.footnote_case("See Note 3 on page 4.")
        records.append(dict(records[1], id="target", page=4, content="Note 3: Retail segment only."))
        with patch("agents.text.structured", side_effect=self.scope_reply) as model:
            result = self.verify_revenue(records)
        self.assertEqual(result["verdict"], "UNKNOWN", result["reason"])
        self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::table", f"{DOC}::note", f"{DOC}::target"})
        self.assertEqual(result["footnote_checks"][0]["note_page"], 4)
        model.assert_called_once()

    def test_similar_labels_use_text_in_both_directions(self):
        for text, table, expected in [(120, 100, "SUPPORTED"), (100, 120, "CONTRADICTED")]:
            with self.subTest(expected=expected):
                records = source_records(table, None, text)
                records[1]["content"] = f"Total customer count was {text} in 2026."
                result = self.verify_sources(records)
                self.assertEqual(result["verdict"], expected, result["reason"])
                self.assertEqual(result["calculation"]["value"], text)
                self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::text", f"{DOC}::table"})
                decision = result["source_resolution"]
                self.assertEqual(decision["status"], "possible_source_conflict")
                self.assertEqual(decision["findings"][decision["preferred"]]["kind"], "text")

    def test_table_precedes_chart_for_similar_labels(self):
        records = source_records(100, 120, None)
        records[0]["rows"][0][0] = "Total customer count"
        result = self.verify_sources(records)
        self.assertEqual(result["verdict"], "CONTRADICTED", result["reason"])
        self.assertEqual(result["calculation"]["value"], 100)

    def test_total_revenue_in_prose_and_revenue_in_table(self):
        table, prose = source_records(140, None, 150)
        table.update(title="Annual financial results", rows=[["Revenue", "140"]],
                     context="Consolidated figures in USD millions.", unit="USD millions")
        prose["content"] = "Total revenue was USD 150 million in 2026."
        result = self.verify_sources([table, prose], metric="total revenue", claimed_literal="USD 150 million",
                                     unit="amount", claim_text="Total revenue was USD 150 million in 2026.")
        self.assertEqual(result["verdict"], "SUPPORTED", result["reason"])
        self.assertEqual(result["source_resolution"]["status"], "possible_source_conflict")
        findings = result["source_resolution"]["findings"]
        self.assertEqual({(s["kind"], tuple(s["labels"]), s["value"]) for s in findings},
                         {("text", ("Total revenue",), 150_000_000), ("table", ("Revenue",), 140_000_000)})

    def test_same_priority_separate_charts_conflict(self):
        records = source_records(None, 120, None)
        other = source_records(None, 100, None)[0]
        other.update(id="other", title="Annual total customer count")
        result = self.verify_sources([*records, other])
        self.assertEqual(result["verdict"], "SOURCE_CONFLICT", result["reason"])
        self.assertIsNone(result["calculation"])
        self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::chart", f"{DOC}::other"})

    def test_explicitly_different_scope_is_excluded_before_priority(self):
        records = source_records(100, None, 120)
        records[1]["content"] = "Retail customer count was 120 in 2026."
        result = self.verify_sources(records)
        self.assertEqual(result["verdict"], "CONTRADICTED", result["reason"])
        self.assertEqual(result["source_resolution"]["status"], "scope_difference")
        self.assertEqual(result["calculation"]["value"], 100)

    def test_invented_metric_label_is_an_execution_error(self):
        def invented(schema, prompt, **kwargs):
            response = self.read_labels(schema, prompt, **kwargs)
            response.readings[0].labels = ["unprinted metric"]
            return response
        self.label_model.side_effect = invented
        result = self.verify_sources(source_records(120, 100, None))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_stage"], "source_reconciliation")

    def test_uncertain_scope_keeps_both_citations_without_using_priority(self):
        def uncertain(schema, prompt, **kwargs):
            response = self.read_labels(schema, prompt, **kwargs)
            response.readings[0].scope = "uncertain"
            return response
        self.label_model.side_effect = uncertain
        result = self.verify_sources(source_records(120, 100, None))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertIsNone(result["source_resolution"]["preferred"])
        self.assertEqual(set(result["evidence_ids"]), {f"{DOC}::table", f"{DOC}::chart"})

    def test_latest_reported_year(self):
        results = self.compare("Latest year on chart", RELATIVE, (None, 120, None), "chart",
                               ("SUPPORTED", "UNKNOWN", "SUPPORTED"))
        self.assertIsNone(results["Claim-only"]["parsed_claim"]["end_period"])
        self.assertEqual(results["Hybrid"]["parsed_claim"]["end_period"], 2026)

    def test_compound_claim_keeps_each_subclaims_own_suggestions_after_checkpoint(self):
        wording = "Annual customer count was 80 in 2025 and 120 in 2026."
        records = source_records(120, 120, None)
        self.env.storage.side_effect = lambda path: (self.env.document if path.endswith("document.json")
                                                     else {"evidence": records})
        self.pipeline["search"] = lambda *a, **kw: [dict(document_id=DOC, source_evidence_id=r["id"])
                                                   for r in records]
        self.env.search.return_value = {"text": [], "table": [], "chart": []}

        def compound_reply(schema, prompt, **kwargs):
            candidates = {r["type"]: r for r in json.loads(prompt)["evidence"]}
            atoms = []
            for kind, literal, year in (("chart", "80", 2025), ("table", "120", 2026)):
                candidate = candidates[kind]
                end = next(k for k, p in candidate["periods"].items() if p["year"] == year)
                atoms.append(self.env.atom(metric="customer count", literal=literal, operation="level", unit="count",
                                           direction="flat", approximate=False, start=None, end=end,
                                           sources=[candidate["id"]]))
            return SimpleNamespace(data=self.env.response(*atoms))

        with patch.object(self.env.module, "structured", side_effect=compound_reply) as interpreter:
            claims, prepared = self.pipeline["prepare_verification"](wording, [DOC])
        interpreter.assert_called_once()
        claims, prepared = json.loads(json.dumps([claims, prepared]))
        results = [self.pipeline["_verify_one"](claim, [DOC], prepared=prepared) for claim in claims]
        self.assertEqual([r["verdict"] for r in results], ["SUPPORTED", "SUPPORTED"], results)
        self.assertEqual([r["evidence_ids"] for r in results], [[f"{DOC}::chart"], [f"{DOC}::table"]])
        self.assertEqual(results[0]["evidence_checks"]["table"]["candidate_count"], 0)
        self.assertEqual(results[1]["evidence_checks"]["chart"]["candidate_count"], 0)

    def test_hybrid_qualitative_text_branch_retains_the_footnote_quote(self):
        sentence = "The company opened its second office."
        evidence = dict(id="note", document_id=DOC, type="footnote", page=3, content=sentence)
        self.env.storage.return_value = {"evidence": [evidence]}
        self.env.search.return_value = {"text": [dict(document_id=DOC, source_evidence_id="note")],
                                        "table": [], "chart": []}
        self.pipeline["source_context"] = self.env.numeric.source_context
        claim = dict(claim_type="assertion", operation="qualitative", claim_text=sentence)
        key = f"{DOC}::note"
        citation = {"evidence_id": key, "quote": sentence}
        quote = SimpleNamespace(**citation, model_dump=lambda **kwargs: citation)
        with patch("agents.text.structured", return_value=SimpleNamespace(
                verdict="SUPPORTED", confidence=0.9, reason="The footnote confirms the opening.",
                evidence_ids=[key], quotes=[quote])) as model:
            result = self.pipeline["_verify_one"](claim, [DOC], prepared={"workflow": "hybrid"})
        self.assertEqual(result["verdict"], "SUPPORTED", result.get("reason"))
        self.assertEqual(result["evidence_checks"]["text"]["quotes"], [{"evidence_id": key, "quote": sentence}])
        self.assertEqual(model.call_args.kwargs["purpose"], "text_verification")


if __name__ == "__main__":
    print(f"{'Fixture':<25} " + " | ".join(f"{name:<34}" for name in WORKFLOWS), flush=True)
    unittest.main(verbosity=0)
