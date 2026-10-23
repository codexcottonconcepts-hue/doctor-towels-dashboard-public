"""A shared font family reaches every page builder without redesigning it."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dropdown_ui
import refresh


class ApplicationFontTests(unittest.TestCase):
    def test_override_changes_only_family_including_controls_svg_and_pseudo_text(self):
        css = (ROOT / "application_fonts.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        selector, declarations = css.strip().rstrip("}").split("{", 1)
        self.assertEqual([item.strip() for item in selector.split(",")],
                         ["html", "body", "body *", "body *::before", "body *::after"])
        properties = [part.split(":", 1)[0].strip() for part in declarations.split(";") if part.strip()]
        self.assertEqual(properties, ["font-family"])
        self.assertIn('"Satoshi","Satoshi Variable"', declarations)
        self.assertIn("!important", declarations)

    def test_default_server_assets_embed_each_existing_face_and_override_once(self):
        html = dropdown_ui.assets()
        self.assertEqual(html.count('id="dt-application-fonts"'), 1)
        self.assertEqual(html.count("@font-face"), 3)
        self.assertEqual(html.count('id="dt-dropdown-styles"'), 1)
        self.assertEqual(html.count('id="dt-dropdown-script"'), 1)
        self.assertNotRegex(html, r"url\(['\"]?https?://")

    def test_dashboard_reuses_head_faces_with_no_duplicate_embedded_fonts(self):
        html = refresh.render_html({"fy": 2026, "months": [], "rows": []})
        self.assertEqual(html.count("@font-face"), 3)
        self.assertEqual(html.count('id="dt-application-fonts"'), 1)
        self.assertIn((ROOT / "application_fonts.css").read_text(encoding="utf-8"), html)

    def test_opt_out_keeps_family_override_and_the_existing_dropdown_assets(self):
        html = dropdown_ui.assets(include_font_faces=False)
        self.assertNotIn("@font-face", html)
        self.assertIn('id="dt-application-fonts"', html)
        self.assertIn('"Satoshi"', html)
        self.assertIn((ROOT / "dropdown_ui.css").read_text(encoding="utf-8"), html)
        self.assertIn((ROOT / "dropdown_ui.js").read_text(encoding="utf-8"), html)


if __name__ == "__main__":
    unittest.main()
