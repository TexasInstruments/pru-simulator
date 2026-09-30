"""Render USERS_GUIDE.md and DEVELOPMENT_REPORT.md to PDF.

No pandoc on this machine: markdown-it-py ('gfm-like' preset, for tables)
renders HTML, and headless Google Chrome prints it to PDF.

    python3 source/pif_eth_100/build_pdfs.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

_HERE = Path(__file__).resolve().parent
DOCS = ("USERS_GUIDE.md", "DEVELOPMENT_REPORT.md")
CSS = """
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.45; margin: 18mm 16mm; color: #111; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 1.4em; border-bottom: 1px solid #aaa; }
h3 { font-size: 12pt; margin-top: 1.2em; }
table { border-collapse: collapse; margin: 0.8em 0; font-size: 9pt; }
th, td { border: 1px solid #999; padding: 3px 6px; vertical-align: top; }
th { background: #eee; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt;
       background: #f3f3f3; padding: 0 2px; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: 6px;
      white-space: pre-wrap; word-break: break-all; font-size: 8pt; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #999; margin-left: 0; padding-left: 10px; color: #444; }
"""


def render_html(md_text: str, title: str) -> str:
    body = MarkdownIt("gfm-like", {"linkify": False}).render(md_text)
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def _chrome() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("no Chrome/Chromium found for PDF printing")


def build(md: Path) -> Path:
    out = md.with_suffix(".pdf")
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / f"{md.stem}.html"
        html.write_text(render_html(md.read_text(encoding="utf-8"), md.stem), encoding="utf-8")
        subprocess.run([_chrome(), "--headless", "--disable-gpu", "--no-sandbox",
                        f"--print-to-pdf={out}", "--print-to-pdf-no-header",
                        "--no-pdf-header-footer", html.as_uri()],
                       check=True, capture_output=True, timeout=180)
    if not out.exists() or out.read_bytes()[:4] != b"%PDF":
        raise SystemExit(f"PDF not produced: {out}")
    return out


def main() -> int:
    for name in DOCS:
        print(f"wrote {build(_HERE / name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
