"""Shared, self-contained dropdown enhancement for dashboards and forms."""
from pathlib import Path


def assets() -> str:
    root = Path(__file__).resolve().parent
    css = (root / "dropdown_ui.css").read_text(encoding="utf-8")
    script = (root / "dropdown_ui.js").read_text(encoding="utf-8")
    return (f'<style id="dt-dropdown-styles">{css}</style>'
            f'<script id="dt-dropdown-script">{script}</script>')
