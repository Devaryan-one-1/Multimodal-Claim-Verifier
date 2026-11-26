"""Chart-label crop integrity, neighboring regions and cache refresh; offline."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image, ImageChops

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
# Legacy extraction imports load .env. Keep that import's settings isolated so
# collecting this test cannot switch other fixture tests to the operator's DB.
with patch.dict(os.environ, {"CUSTOMER_STORAGE": "local"}):
    from extraction import chart_detector as detector


class ChartCroppingTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.folder = Path(self.temp)
        self.page_path = self.folder / "page.png"
        self.cache_path = self.folder / "charts.json"
        self.enterContext(patch.object(detector, "CHARTS_ROOT", self.folder / "charts"))
        self.enterContext(patch.object(detector, "cache_file", return_value=self.cache_path))
        self.enterContext(patch.object(detector, "model_name", return_value="fixture/vision"))

    def render_aster(self):
        with pymupdf.open(PROJECT / "tests/fixtures/worker_rollout/aster-vale.pdf") as pdf:
            page = pdf[1]
            scale = 200 / 72
            page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).save(self.page_path)
            image_rect, = page.get_image_rects(page.get_images()[0][0])
            return [round(v * scale) for v in image_rect]

    def test_aster_original_detection_keeps_complete_title_and_currency_pixels(self):
        embedded = self.render_aster()
        original = [236, 406, 1188, 947]  # Saved defective detection, at 200 dpi.
        with Image.open(self.page_path) as page:
            expanded = detector._context_bbox(original, width_px=page.width, height_px=page.height)
            # The title and unit occupy the top quarter of the embedded figure.
            header = (embedded[0], embedded[1], embedded[2],
                      embedded[1] + (embedded[3] - embedded[1]) // 4)
            pixels = page.crop(header).convert("RGB")
            ink = ImageChops.difference(pixels, Image.new("RGB", pixels.size, "white")).getbbox()
            self.assertIsNotNone(ink)
            left, top, right, bottom = [ink[i] + header[i % 2] for i in range(4)]
            self.assertGreater(original[0], left, "Original crop must reproduce clipped label pixels")
            self.assertLessEqual(expanded[0], left)
            self.assertLessEqual(expanded[1], top)
            self.assertGreaterEqual(expanded[2], right)
            self.assertGreaterEqual(expanded[3], bottom)
            destination = self.folder / "fixed-crop.png"
            detector._crop_image(source_path=self.page_path, bbox=expanded, destination=destination)
            with Image.open(destination) as crop:
                recovered = crop.crop((left - expanded[0], top - expanded[1],
                                       right - expanded[0], bottom - expanded[1])).convert("RGB")
                self.assertEqual(recovered.tobytes(), page.crop((left, top, right, bottom)).convert("RGB").tobytes())

    def test_context_stays_within_page_at_all_edges(self):
        self.assertEqual(detector._context_bbox([0, 0, 100, 100], width_px=100, height_px=100),
                         [0, 0, 100, 100])
        box = [1, 1, 99, 99]
        detector._context_bbox(box, width_px=100, height_px=100)
        self.assertEqual(box, [1, 1, 99, 99])

    def test_context_does_not_cross_neighboring_chart_regions(self):
        boxes = [[100, 100, 500, 400], [510, 100, 900, 400], [100, 410, 500, 700]]
        padded = [detector._context_bbox(box, width_px=1000, height_px=800, neighbors=boxes)
                  for box in boxes]
        self.assertLessEqual(padded[0][2], padded[1][0])
        self.assertLessEqual(padded[0][3], padded[2][1])
        for raw, expanded in zip(boxes, padded):
            self.assertLessEqual(expanded[0], raw[0])
            self.assertLessEqual(expanded[1], raw[1])
            self.assertGreaterEqual(expanded[2], raw[2])
            self.assertGreaterEqual(expanded[3], raw[3])

    def test_detection_crop_and_citation_use_same_expanded_coordinates(self):
        self.render_aster()
        with Image.open(self.page_path) as page:
            w, h = page.size
        original = [236, 406, 1188, 947]
        candidate = SimpleNamespace(chart_type="bar", title="Consolidated revenue",
                                    box_2d=[round(original[1] / h * 1000), round(original[0] / w * 1000),
                                            round(original[3] / h * 1000), round(original[2] / w * 1000)])
        with patch.object(detector, "ensure_page_rendered", return_value=self.page_path), \
                patch.object(detector, "_detect_with_retry", return_value=SimpleNamespace(charts=[candidate])) as call:
            # Exercise the real detection/cache implementation without routing decorators.
            result = detector.detect_charts_on_page.__wrapped__("aster", 2)
            chart, = result["charts"]
            self.assertEqual(chart["bbox"], chart["evidence"]["bbox"])
            self.assertEqual(chart["detected_bbox"], chart["evidence"]["detected_bbox"])
            self.assertLess(chart["bbox"][0], chart["detected_bbox"][0])
            with Image.open(chart["image_path"]) as crop:
                self.assertEqual(crop.size, (chart["bbox"][2] - chart["bbox"][0],
                                             chart["bbox"][3] - chart["bbox"][1]))
            cached = detector.detect_charts_on_page.__wrapped__("aster", 2)
            self.assertEqual(cached["status"], "cached")
            self.assertEqual(cached["charts"][0]["bbox"], chart["bbox"])
            call.assert_called_once()

    def test_context_does_not_capture_diagonal_neighbor(self):
        box, other = [100, 100, 500, 400], [510, 410, 900, 700]
        expanded = detector._context_bbox(box, width_px=1000, height_px=800, neighbors=[other])
        overlap = (max(expanded[0], other[0]) < min(expanded[2], other[2]) and
                   max(expanded[1], other[1]) < min(expanded[3], other[3]))
        self.assertFalse(overlap)

    def test_previous_crop_cache_is_invalidated_and_current_cache_preserved(self):
        old = dict(cache_version=detector.CHART_CACHE_VERSION - 1, document_id="aster",
                   pages={"2": {"charts": ["clipped-crop"]}})
        self.cache_path.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(detector._load_cache("aster")["pages"], {})
        old["cache_version"] = detector.CHART_CACHE_VERSION
        self.cache_path.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(detector._load_cache("aster")["pages"], old["pages"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
