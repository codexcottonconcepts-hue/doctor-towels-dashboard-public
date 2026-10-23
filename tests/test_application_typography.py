"""Match Online Sales typography without changing component geometry or data."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dropdown_ui
import refresh


class ApplicationTypographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "application_fonts.css").read_text(encoding="utf-8")

    def test_all_declarations_are_typography_only(self):
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        allowed = {"font-family", "font-size", "font-weight", "line-height", "letter-spacing"}
        declarations = re.findall(r"([a-z-]+)\s*:\s*([^{};]+);", css)
        self.assertGreater(len(declarations), 35)
        self.assertFalse(set(key for key, value in declarations) - allowed)
        self.assertNotIn("text-transform", css)
        self.assertNotIn("color:", css)
        self.assertNotIn("padding:", css)

    def test_card_roles_match_measured_online_reference(self):
        self.assertIn("font-size:28px!important;font-weight:500!important;line-height:1.2!important", self.css)
        self.assertIn("letter-spacing:-.035em!important", self.css)
        self.assertIn("font-size:12px!important;font-weight:500!important;line-height:1.3!important", self.css)
        self.assertIn("font-size:12px!important;font-weight:400!important;line-height:1.4!important", self.css)
        self.assertIn("font-size:16px!important;font-weight:400!important;line-height:1.5!important", self.css)
        self.assertIn("font-size:24px!important", self.css)

    def test_svg_centres_and_bold_current_date_are_not_shrunk(self):
        self.assertIn("body svg text{font-weight:500!important}", self.css)
        self.assertNotIn("body svg text{font-size", self.css)
        self.assertIn("body .tv-current-date{font-size:13px!important;font-weight:700!important}", self.css)
        self.assertIn("body .rhead h1{font-size:19px!important;font-weight:500!important;", self.css)
        self.assertIn(":is(#page,.tv-slide-outgoing) .comparison-name{font-size:12px}", self.css)
        self.assertNotIn(".comparison-name{font-size:12px!important}", self.css)

    def test_existing_shared_pipeline_embeds_style_once_and_fonts_once(self):
        server = dropdown_ui.assets()
        dashboard = refresh.render_html({"fy": 2026, "months": [], "rows": []})
        for html in (server, dashboard):
            self.assertEqual(html.count(self.css), 1)
            self.assertEqual(html.count("@font-face"), 3)
            self.assertEqual(html.count('id="dt-application-fonts"'), 1)

    def test_shared_header_and_inline_name_fitting_keep_their_contract(self):
        controls = re.search(r"body :is\(input,[^{]+", self.css).group(0)
        self.assertIn(":not(.rhead *)", controls)
        self.assertIn(":not(.comparison-name)", controls)
        self.assertIn(".rhead .dt-select-trigger{font-size:12.5px!important;font-weight:500!important;line-height:1.4!important;letter-spacing:normal!important}", self.css)
        self.assertIn(".rhead .views :is(button,.plan-link){font-size:11.5px!important;font-weight:500!important;line-height:1.45!important;letter-spacing:normal!important}", self.css)


if __name__ == "__main__":
    unittest.main()
