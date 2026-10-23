"""The imported design stays inside live or transitioning Online Sales content."""
import hashlib
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def qualified_rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    position = 0
    while position < len(css):
        opening = css.find("{", position)
        if opening < 0:
            return
        prelude = css[position:opening].strip()
        depth, closing = 1, opening + 1
        while depth:
            if css[closing] == "{":
                depth += 1
            elif css[closing] == "}":
                depth -= 1
            closing += 1
        content = css[opening + 1:closing - 1]
        if prelude.startswith("@media"):
            yield from qualified_rules(content)
        else:
            yield prelude, content
        position = closing


def selector_list(prelude):
    depth, start = 0, 0
    for index, char in enumerate(prelude):
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            yield prelude[start:index].strip()
            start = index + 1
    yield prelude[start:].strip()


class OnlineStyleScopeTests(unittest.TestCase):
    def test_every_rule_requires_online_root_inside_live_or_id_free_transition(self):
        rules = list(qualified_rules((ROOT / "online_sales.css").read_text(encoding="utf-8")))
        self.assertGreater(len(rules), 25)
        prefix = ":is(#page,.tv-slide-outgoing) .online-sales"
        for prelude, content in rules:
            for selector in selector_list(prelude):
                self.assertTrue(selector.startswith(prefix), selector)
                self.assertNotIn("body", selector)
                self.assertNotIn(".rail", selector)
                self.assertNotIn(".rhead", selector)

    def test_single_scoped_style_and_existing_font_markers(self):
        template = (ROOT / "template.html").read_text(encoding="utf-8")
        self.assertEqual(template.count("/*__ONLINE_STYLES__*/"), 1)
        font_markers = sum(template.count(marker) for marker in
                           ("/*__ONLINE_FONTS__*/", "/*__SATOSHI__*/"))
        self.assertEqual(font_markers, 1)

    def test_font_is_exact_github_embedded_copy_without_external_request(self):
        data = (ROOT / "satoshi_fonts.css").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "b205da2b63eb7cc34aa68d7ca28139fabcbeeecabf2b298c7d58adc9ee03b236")
        css = data.decode("utf-8")
        self.assertEqual(css.count("@font-face"), 3)
        self.assertEqual(css.count("data:font/"), 3)
        self.assertNotRegex(css, r"url\(['\"]?https?://")
        self.assertNotIn("@import", css)

    def test_renderer_inlines_both_new_assets_without_business_changes(self):
        import refresh
        data = {"fy": "2026", "months": [], "rows": []}
        html = refresh.render_html(data)
        self.assertNotIn("/*__ONLINE_FONTS__*/", html)
        self.assertNotIn("/*__ONLINE_STYLES__*/", html)
        self.assertIn((ROOT / "online_sales.css").read_text(encoding="utf-8"), html)
        self.assertEqual(html.count((ROOT / "satoshi_fonts.css").read_text(encoding="utf-8")), 1)
        self.assertEqual(data, {"fy": "2026", "months": [], "rows": []})

    def test_negative_semantics_and_no_global_zoom_or_target_assumptions(self):
        css = (ROOT / "online_sales.css").read_text(encoding="utf-8")
        self.assertIn(".online-bar-fill.negative{background:#c8102e}", css)
        self.assertNotIn("zoom", css)
        self.assertNotIn(":root", css)
        self.assertNotIn("ANNUAL_TARGET", (ROOT / "online_sales.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
