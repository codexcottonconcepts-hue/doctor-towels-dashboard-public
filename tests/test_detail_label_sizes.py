"""Dense SVG label sizes remain chart-specific; the full-size TV view is stable."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DetailLabelSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "application_fonts.css").read_text(encoding="utf-8")
        cls.template = (ROOT / "template.html").read_text(encoding="utf-8")

    def test_dense_svg_labels_can_use_renderer_font_sizes(self):
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        sized_chart_rules = []
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(r"\.(?:tk|dl|cl)\b", selector) and "font-size:" in body:
                sized_chart_rules.append((selector.strip(), body.strip()))
        self.assertEqual(len(sized_chart_rules), 3)
        selector, body = sized_chart_rules[0]
        self.assertIn('body:not(.salesviewer-tv) [data-dashboard-widget="order_bands"] svg a text.tk:not(:last-child)', selector)
        self.assertEqual(body, "font-size:8.5px!important")
        selector, body = sized_chart_rules[1]
        self.assertIn('body:not(.salesviewer-tv) [data-dashboard-widget="sales_trend"] svg text.tk[text-anchor="middle"]', selector)
        self.assertEqual(body, "font-size:8.5px!important")
        selector, body = sized_chart_rules[2]
        self.assertIn("body.salesviewer-tv svg", selector)
        self.assertEqual(body, "font-size:12px!important")

    def test_compact_band_labels_and_counts_keep_their_reserved_sizes(self):
        band_function = self.template.split("function vBands(rs){", 1)[1].split("/* ---------- derived alerts", 1)[0]
        self.assertIn('style="font-size:9.5px">${axis}</text>', band_function)
        self.assertIn('style="font-size:9px" opacity=".85">${cnt(x.n)}</text>', band_function)
        self.assertIn('style="font-size:10.5px">${tick(x.v)}</text>', band_function)

    def test_satoshi_weight_and_centres_keep_their_existing_contract(self):
        self.assertIn('font-family:"Satoshi","Satoshi Variable"', self.css)
        self.assertRegex(self.css, r"body :is\(\.cl,\.tk,\.dl\)\{\s*font-weight:500!important;")
        self.assertIn("body svg text{font-weight:500!important}", self.css)
        self.assertNotIn("body svg text{font-size:", self.css)


if __name__ == "__main__":
    unittest.main()
