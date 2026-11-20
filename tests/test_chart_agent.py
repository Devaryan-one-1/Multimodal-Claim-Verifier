"""Offline chart extraction tests and opt-in OpenRouter checks on synthetic images."""
import base64
from dataclasses import replace
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from agents.chart import CHART_MODEL, extract_chart_evidence, select_chart_operands
from reasoning.vision_client import RawChartExtraction
from services import model_gateway as gateway


CLAIM = {
    "claim_text": "The annual customer count was 80 in 2026.",
    "operation": "level", "metric": "customer count", "period_type": "annual",
    "end_period": 2026, "claimed_literal": "80", "unit": "number",
}


def draw_chart(path, *, labeled):
    image = Image.new("RGB", (720, 450), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    draw.text((360, 36), "Northstar annual customer count", fill="black", font=font, anchor="mt")
    draw.text((105, 62), "Customers", fill="black", font=font)
    for value in (0, 50, 100, 150):
        y = 370 - 1.8 * value
        draw.line((100, y, 650, y), fill="lightgray", width=2)
        draw.text((88, y), str(value), fill="black", font=font, anchor="rm")
    draw.line((100, 95, 100, 370, 650, 370), fill="black", width=2)
    for x, year, value in ((270, 2025, 80), (510, 2026, 120)):
        y = 370 - 1.8 * value
        draw.rectangle((x - 57, y, x + 57, 369), fill="#285e91")
        draw.text((x, 382), str(year), fill="black", font=font, anchor="mt")
        if labeled:
            draw.text((x, y - 10), str(value), fill="black", font=font, anchor="mb")
    image.save(path)


def synthetic_image(path, *, labeled):
    draw_chart(path, labeled=labeled)
    return {
        "chart_id": "customers", "document_id": "northstar", "page": 2,
        "image_path": path, "chart_type": "bar", "evidence": {"id": "northstar::customers"},
    }


def extraction_response(*, labeled):
    extraction = RawChartExtraction.model_validate({
        "chart_type": "bar", "title": "Northstar annual customer count",
        "display_unit": "customers", "x_axis": {"label": "Year"}, "y_axis": {"unit": "customers"},
        "data_points": [
            {"metric": "customer count", "period": str(year), "value": value,
             "display_value": str(value) if labeled else None,
             "value_source": "explicit_label" if labeled else "estimated_from_axis"}
            for year, value in ((2025, 80), (2026, 120))
        ],
    })
    return httpx.Response(200, json={
        "model": CHART_MODEL, "provider": "Nvidia",
        "choices": [{"finish_reason": "stop", "message": {"content": extraction.model_dump_json()}}],
    })


class ChartAgentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "chart.png"

    def read_fixture(self, *, labeled):
        chart = synthetic_image(self.path, labeled=labeled)
        config = replace(gateway.config(), provider="openrouter",
                         allow_fallbacks=True, vision_fallbacks=("paid/vision",))
        with patch.object(gateway, "config", return_value=config), \
             patch.object(gateway, "_key", return_value="fixture"), \
             patch.object(gateway, "record_model_call"), \
             patch.object(gateway.httpx, "post", return_value=extraction_response(labeled=labeled)) as call:
            evidence = extract_chart_evidence(chart)
        call.assert_called_once()
        payload = call.call_args.kwargs["json"]
        self.assertEqual(payload["model"], CHART_MODEL)
        image_input = payload["messages"][-1]["content"][-1]["image_url"]["url"]
        self.assertEqual(base64.b64decode(image_input.split(",", 1)[1]), self.path.read_bytes())
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertIn("data_points", schema["properties"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        return evidence

    def test_labeled_image_extracts_and_selects_the_claimed_year(self):
        chart = self.read_fixture(labeled=True)
        self.assertEqual(chart["page"], 2)
        self.assertEqual(chart["image_path"], str(self.path))
        self.assertEqual({fact["evidence_id"] for fact in chart["facts"]}, {chart["id"]})
        self.assertEqual([f["value"] for f in chart["facts"]], [80, 120])
        result = select_chart_operands(CLAIM, [chart])
        self.assertTrue(result.applicable, result.reason)
        point, = result.operands
        self.assertEqual((point.evidence_id, point.fact_index), ("northstar::customers", 1))

    def test_unlabeled_image_retains_estimates_as_analysis_only(self):
        chart = self.read_fixture(labeled=False)
        self.assertEqual(chart["facts"], [])
        self.assertEqual(chart["analysis"]["series"][0]["values"][0]["value_source"], "estimated_from_axis")
        self.assertFalse(select_chart_operands(CLAIM, [chart]).applicable)

    def test_growth_keeps_saved_fact_indexes_and_series_scope(self):
        chart = self.read_fixture(labeled=True)
        old = chart["facts"][0]
        chart["facts"].insert(0, dict(old, value_source="estimated_from_axis"))
        claim = dict(CLAIM, operation="growth", start_period=2025, claimed_literal="25%", unit="percent")
        result = select_chart_operands(claim, [chart])
        self.assertTrue(result.applicable, result.reason)
        self.assertEqual([(p.name, p.fact_index) for p in result.operands], [("old", 1), ("new", 2)])
        chart["facts"][2]["series_name"] = "Retail"
        self.assertFalse(select_chart_operands(claim, [chart]).applicable)

    def test_label_required_even_if_extractor_claims_value_is_explicit(self):
        chart = self.read_fixture(labeled=True)
        chart["facts"][1]["display_value"] = None
        self.assertFalse(select_chart_operands(CLAIM, [chart]).applicable)


@unittest.skipUnless(os.getenv("CHART_AGENT_LIVE") == "1", "set CHART_AGENT_LIVE=1 to call OpenRouter")
class ChartAgentLiveTests(unittest.TestCase):
    def extract(self, *, labeled):
        config = replace(gateway.config(), provider="openrouter")
        with TemporaryDirectory() as temp, patch.object(gateway, "config", return_value=config):
            return extract_chart_evidence(synthetic_image(Path(temp) / "chart.png", labeled=labeled))

    def test_reads_printed_values_and_years(self):
        chart = self.extract(labeled=True)
        for year, expected in ((2025, 80), (2026, 120)):
            result = select_chart_operands(dict(CLAIM, end_period=year), [chart])
            self.assertTrue(result.applicable, result.reason)
            point, = result.operands
            self.assertEqual(chart["facts"][point.fact_index]["value"], expected)

    def test_axis_ticks_do_not_become_exact_point_values(self):
        chart = self.extract(labeled=False)
        self.assertEqual(chart["facts"], [])
        self.assertFalse(select_chart_operands(CLAIM, [chart]).applicable)
