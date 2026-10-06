"""Design tokens of the dashboard: every theme defines every token the UI uses,
and the main text/background pairs stay readable (WCAG 2.x AA)."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "ui" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
STYLE = HTML[HTML.index("<style>"):HTML.index("</style>")]


def _block(selector: str) -> dict:
    """Custom properties declared in the first rule that starts with selector."""
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\n    \}", STYLE, re.S)
    assert match, f"{selector} rule not found"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1)))


DARK = _block(":root")
CONTRAST = _block(':root[data-theme="contrast"]')
THEMES = {"dark": DARK, "contrast": {**DARK, **CONTRAST}}


# Component-local geometry/animation variables (set by the glider script or by the
# button wipe rule itself), not theme tokens.
LOCAL_PROPERTIES = {"--glider-x", "--glider-w", "--skew", "--progress"}


def _used_tokens() -> set:
    used = set(re.findall(r"var\((--[\w-]+)", HTML))
    for script in STATIC.glob("*.js"):
        source = script.read_text(encoding="utf-8")
        used |= set(re.findall(r"var\((--[\w-]+)", source))
        used |= set(re.findall(r"(?:ThemeColor|themeColor)\(\s*['\"](--[\w-]+)", source))
    return used - LOCAL_PROPERTIES


def _luminance(color: str) -> float:
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", color), f"not a 6-digit hex colour: {color}"
    channels = []
    for i in (1, 3, 5):
        value = int(color[i:i + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_helper_matches_known_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_dark_theme_is_the_red_brand_palette():
    assert DARK["--brand-red"] == "#b5202c"
    assert DARK["--brand-red-bright"] == "#ff7c84"
    assert DARK["--accent"] == "#ff7c84"
    assert DARK["--panel"] == "#2c333b"
    assert "Cascadia Code" in DARK["--font-mono"]
    assert "Segoe UI" in DARK["--font-ui"]


def test_every_token_the_ui_uses_is_defined():
    used = _used_tokens()
    assert {"--bg", "--panel", "--text", "--accent", "--graph-bg", "--graph-wave"} <= used
    assert not used - set(DARK), f"undefined in the dark theme: {sorted(used - set(DARK))}"


def test_high_contrast_redefines_every_colour_token():
    colour_tokens = {name for name in DARK if not name.startswith("--font-")}
    missing = colour_tokens - set(CONTRAST)
    assert not missing, f"High contrast does not override: {sorted(missing)}"


TEXT_PAIRS = [
    ("--text", "--bg"), ("--text", "--panel"), ("--text", "--panel-raised"),
    ("--text", "--panel-inset"), ("--text", "--btn"), ("--text", "--btn-hover"),
    ("--text", "--highlight"), ("--text-dim", "--bg"), ("--text-dim", "--panel"),
    ("--text-dim", "--panel-raised"), ("--text-dim", "--panel-inset"),
    ("--text-dim", "--btn"), ("--text-dim", "--btn-hover"),
    ("--accent", "--panel"), ("--accent", "--panel-raised"), ("--accent", "--panel-inset"),
    ("--accent", "--highlight"), ("--accent", "--btn"), ("--accent2", "--panel"),
    ("--halted", "--panel"), ("--halted", "--panel-inset"), ("--halted", "--highlight"),
    ("--green", "--panel"), ("--green", "--panel-inset"), ("--green", "--panel-raised"),
    ("--on-brand", "--brand-red"), ("--on-brand", "--btn-active"),
    # Filled/danger toolbar buttons swap colours on hover: red text on --on-brand.
    ("--brand-red", "--on-brand"),
    ("--changed", "--highlight"), ("--changed", "--panel-inset"),
    ("--graph-label", "--graph-bg"), ("--graph-placeholder", "--graph-bg"),
    ("--graph-wave", "--graph-bg"),
    ("--hl-op", "--panel-inset"), ("--hl-reg", "--panel-inset"), ("--hl-num", "--panel-inset"),
    ("--hl-lbl", "--panel-inset"), ("--hl-cmt", "--panel-inset"),
    ("--hl-op", "--highlight"), ("--hl-reg", "--highlight"), ("--hl-num", "--highlight"),
    ("--hl-lbl", "--highlight"), ("--hl-cmt", "--highlight"),
]


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("foreground,background", TEXT_PAIRS)
def test_text_pairs_meet_wcag_aa(theme, foreground, background):
    tokens = THEMES[theme]
    ratio = contrast(tokens[foreground], tokens[background])
    assert ratio >= 4.5, f"{theme}: {foreground} on {background} is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("border,surface", [
    ("--border-strong", "--panel-inset"), ("--border-strong", "--panel"),
    ("--graph-grid-strong", "--graph-bg"),
])
def test_control_borders_are_visible(theme, border, surface):
    tokens = THEMES[theme]
    ratio = contrast(tokens[border], tokens[surface])
    if border == "--graph-grid-strong":
        assert ratio >= 1.5  # decorative gridline, not an identifying border
    else:
        assert ratio >= 3.0, f"{theme}: {border} on {surface} is {ratio:.2f}:1"


def _difference(a: str, b: str) -> str:
    """mix-blend-mode: difference of two opaque colours."""
    return "#" + "".join(f"{abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)):02x}" for i in (1, 3, 5))


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_wipe_label_is_readable_before_and_after_the_animation(theme):
    """Neutral toolbar buttons blend their --wipe coloured label with `difference`
    over the button fill, so the label colour changes while the wipe passes."""
    tokens = THEMES[theme]
    wipe, inset = tokens["--wipe"], tokens["--panel-inset"]
    rest_label = _difference(wipe, inset)       # label over the dark button
    end_label = _difference(wipe, wipe)         # label over the fully swept wipe
    assert contrast(rest_label, inset) >= 4.5, f"{theme}: resting label {rest_label}"
    assert contrast(end_label, wipe) >= 4.5, f"{theme}: label at the end of the wipe {end_label}"


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_panel_wipe_label_is_readable_on_the_default_fill(theme):
    """Panel buttons wipe too: their resting label is the difference blend over --btn.
    (--btn-hover only shows for the 200 ms the wipe takes to cover it.)"""
    tokens = THEMES[theme]
    label = _difference(tokens["--wipe"], tokens["--btn"])
    assert contrast(label, tokens["--btn"]) >= 4.5, f"{theme}: label {label} on --btn"


def test_neutral_buttons_wipe_but_filled_and_danger_buttons_swap_colours():
    """difference would turn Run's white label cyan: the wipe is for neutral buttons only."""
    assert ".btn-17:not(.btn-run):not(.btn-reset)::before" in STYLE
    assert "mix-blend-mode: difference" in STYLE
    blend = re.findall(r"([^{}]*)\{[^{}]*mix-blend-mode: difference", STYLE)
    assert blend and all(":not(.btn-run):not(.btn-reset)" in selector for selector in blend)
    assert re.search(r"\.btn-17\.btn-run:hover:not\(:disabled\),\s*\.btn-17\.btn-reset:hover", STYLE)


def test_wipe_respects_reduced_motion_and_hover_capability():
    assert "@media (hover: hover) and (pointer: fine)" in STYLE
    reduced = STYLE[STYLE.index(".btn-17, .btn-17::before"):]
    assert "animation: none !important" in reduced and "--progress: 0%" in reduced


def test_wipe_buttons_do_not_set_their_own_label_colour():
    """The neutral wipe label is --wipe blended with `difference`; an id rule or
    inline colour (accent) would turn cyan at the end of the hover."""
    page = (Path(__file__).parent.parent / "ui" / "static" / "index.html").read_text()
    for match in re.finditer(r"<button\b([^>]*\bbtn-17\b[^>]*)>", page):
        attrs = match.group(1)
        if re.search(r'class="[^"]*\b(btn-run|btn-reset)\b', attrs):
            continue
        style = re.search(r'style="([^"]*)"', attrs)
        assert not (style and re.search(r"(^|;)\s*color\s*:", style.group(1))), attrs
        ident = re.search(r'\bid="([^"]+)"', attrs)
        if ident:
            # `#id {`, `#id:hover {` and grouped selectors (`#a, #id {`); state
            # classes such as `#btn-multicore.mc-active` are toggles and exempt.
            sel = r"#" + re.escape(ident.group(1)) + r"(?::hover)?(?![\w.:-])"
            rules = re.findall(r"(?:^|[},;>])\s*(?:[^{}]*,\s*)?" + sel + r"\s*(?:,[^{}]*)?\{([^}]*)\}", page)
            assert not any(re.search(r"(^|;)\s*color\s*:", rule) for rule in rules), ident.group(1)
