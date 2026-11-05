"""Builds docs/design-document.pdf from the Markdown source, for submission on Nalanda.

Run with `make design-pdf` after changing the document. Needs `markdown` and `xhtml2pdf`.
"""
from __future__ import annotations

import base64
import re
import struct
from pathlib import Path

import markdown
from xhtml2pdf import pisa

DOCS = Path(__file__).resolve().parents[1] / "docs"
IMAGE_WIDTH_PX = 650  # xhtml2pdf reads this as CSS pixels. It fills the A4 text width.

STYLE = """
@page { size: A4; margin: 2cm 1.8cm;
  @frame footer { -pdf-frame-content: footer; bottom: 0.8cm; margin-left: 1.8cm; margin-right: 1.8cm; height: 0.6cm; } }
body { font-family: Helvetica; font-size: 10pt; line-height: 1.35; color: #1f2933; }
h1 { font-size: 20pt; color: #0f172a; margin-bottom: 4pt; }
h2 { font-size: 14pt; color: #0f172a; margin-top: 14pt; margin-bottom: 4pt;
     border-bottom: 0.5pt solid #94a3b8; padding-bottom: 2pt; -pdf-keep-with-next: true; }
h3 { font-size: 11pt; color: #334155; margin-top: 9pt; margin-bottom: 3pt; -pdf-keep-with-next: true; }
p { margin: 0 0 5pt 0; }
table { width: 100%; margin: 4pt 0 8pt 0; }
th { background-color: #e2e8f0; font-size: 8.5pt; text-align: left; padding: 3pt; border: 0.5pt solid #94a3b8; }
td { font-size: 8.5pt; padding: 3pt; border: 0.5pt solid #cbd5e1; vertical-align: top; }
pre { background-color: #f1f5f9; font-family: Courier; font-size: 7.5pt; padding: 5pt; }
code { font-family: Courier; font-size: 9pt; }
#footer { text-align: center; font-size: 8pt; color: #64748b; }
"""


def embed_image(match: re.Match) -> str:
    """Inlines an image with an explicit size.

    xhtml2pdf cannot load a file path containing spaces, and it silently draws nothing
    unless it is told an image's size. A data URI with both set avoids the two problems.
    """
    data = (DOCS / match.group(2)).read_bytes()
    width, height = struct.unpack(">II", data[16:24])  # The PNG header holds the pixel size.
    encoded = base64.b64encode(data).decode()
    drawn_height = round(IMAGE_WIDTH_PX * height / width)
    return (
        f'<img width="{IMAGE_WIDTH_PX}" height="{drawn_height}" alt="{match.group(1)}" '
        f'src="data:image/png;base64,{encoded}"/>'
    )


def main() -> None:
    source = (DOCS / "design-document.md").read_text()
    body = markdown.markdown(source, extensions=["tables", "fenced_code"])
    body = re.sub(r'<img alt="([^"]*)" src="([^"]*)"\s*/>', embed_image, body)
    html = (
        f"<html><head><meta charset='utf-8'><style>{STYLE}</style></head><body>"
        "<div id='footer'>Smart Warehouse, Group 18 - page <pdf:pagenumber/></div>"
        f"{body}</body></html>"
    )
    with (DOCS / "design-document.pdf").open("wb") as output:
        result = pisa.CreatePDF(html, dest=output)
    if result.err:
        raise SystemExit(f"PDF build failed with {result.err} errors")


if __name__ == "__main__":
    main()
