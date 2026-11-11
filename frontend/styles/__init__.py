from pathlib import Path

STYLES_DIR = Path(__file__).parent
THEME_CSS_PATH = STYLES_DIR / "theme.css"


def load_theme_css() -> str:
    if THEME_CSS_PATH.exists():
        return THEME_CSS_PATH.read_text(encoding="utf-8")
    return ""

