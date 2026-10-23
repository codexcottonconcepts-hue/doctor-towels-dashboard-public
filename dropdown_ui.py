"""Shared, self-contained dropdown enhancement for dashboards and forms."""
from pathlib import Path


def assets(*, include_font_faces: bool = True) -> str:
    root = Path(__file__).resolve().parent
    faces = (root / "satoshi_fonts.css").read_text(encoding="utf-8") if include_font_faces else ""
    fonts = (root / "application_fonts.css").read_text(encoding="utf-8")
    css = (root / "dropdown_ui.css").read_text(encoding="utf-8")
    script = (root / "dropdown_ui.js").read_text(encoding="utf-8")
    return (f'<style id="dt-application-fonts">{faces}{fonts}</style>'
            f'<style id="dt-dropdown-styles">{css}</style>'
            f'<script id="dt-dropdown-script">{script}</script>')
