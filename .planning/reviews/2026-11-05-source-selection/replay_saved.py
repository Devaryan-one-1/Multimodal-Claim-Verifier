"""Read-only replay of the user's saved Apple source snapshots; no provider calls."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import types

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/"tests"))
import test_claim_parser as parser_fixture
import test_source_selection as source_fixture

c = sqlite3.connect((ROOT/"customer_data/control.sqlite3").as_uri()+"?mode=ro", uri=True)
c.execute("PRAGMA query_only=ON")
saved = {}
for (raw,) in c.execute("SELECT result FROM jobs WHERE kind='verify' ORDER BY created DESC LIMIT 30"):
    for result in json.loads(raw).get("results", []):
        metric = (result.get("parsed_claim") or {}).get("metric")
        if metric in {"total current assets", "inventories", "share-based compensation expense"}:
            saved.setdefault(metric, result)
c.close()

parser = parser_fixture.ClaimParserTests()
parser.setUp()
selector = source_fixture.SourceSelectionTests()
selector.setUp()
results = []
try:
    for metric, literal, expected in (("total current assets", "$149,818 million", "SUPPORTED"),
                                      ("total current assets", "$147,957 million", "CONTRADICTED"),
                                      ("inventories", "94.0%", "SUPPORTED"),
                                      ("share-based compensation expense", "$10,523 million", "SUPPORTED"),
                                      ("share-based compensation expense", "$3,401 million", "CONTRADICTED")):
        snapshot = saved[metric]
        original = copy.deepcopy(snapshot["parsed_claim"])
        text = original["claim_text"].replace(original["claimed_literal"], literal)
        original.update(claim_text=text, claimed_literal=literal)
        parsed = parser.parser._normalize(types.SimpleNamespace(model_dump=lambda **kw: copy.deepcopy(original)), text)
        evidence = copy.deepcopy(snapshot["evidence"])
        numeric = selector.numeric
        numeric.structured.reset_mock()
        numeric.structured.side_effect = AssertionError("No live provider call permitted")
        local = numeric._local_selection(parsed, evidence)
        mode = "deterministic source selection"
        if not local:
            # Controlled replay of a short id and repeated identical address.
            # The source values and arithmetic are read by production code.
            table = next(item for item in evidence if item.get("type") == "table" and item.get("page") == 14)
            row = next(i for i, row in enumerate(table["rows"]) if numeric._metric_matches(metric, row[0]))
            column = next(cm["col"] for cm in table["column_map"] if cm["year"] == parsed["end_period"] and cm["period"] == "nine_months")
            operand = numeric.Operand(name="value", evidence_id=table["source_evidence_id"], row_index=row, column_index=column)
            numeric.structured.side_effect = [numeric.Selection(applicable=True, reason="Controlled alias response", operands=[operand, copy.deepcopy(operand)])]
            mode = "controlled model source alias; production validation and arithmetic"
        calc, comparison, reason, _ = numeric.verify_numeric(parsed, evidence)
        assert calc is not None, reason
        assert comparison["verdict"] == expected, comparison
        results.append({"metric": metric, "literal": literal, "period_type": parsed["period_type"],
                        "verdict": comparison["verdict"], "actual": calc["value"], "raw_inputs": calc["raw_inputs"],
                        "formula": calc["formula"], "source_inputs": calc["source_inputs"], "mode": mode})
    output = {"scope": "Saved source snapshots and actual parser fields; no API requests, index rebuilds or customer-data writes. Live model acceptance remains separate.", "cases_passed": len(results), "results": results}
    Path(__file__).with_name("SOURCE_REPLAY.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Saved Apple verification replay: {len(results)}/{len(results)} passed.")
finally:
    selector.doCleanups()
    parser.doCleanups()
