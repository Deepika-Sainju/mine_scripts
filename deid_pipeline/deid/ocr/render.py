"""Render de-identified markdown back to a PDF (closes the PDF->OCR->md->PDF loop)."""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF
import markdown as md_lib

_PAGE_MARK = re.compile(r"<!--\s*page\s+\d+\s*-->")
_US_LETTER = fitz.paper_rect("letter")


def md_to_pdf(md_path: str, pdf_path: str) -> None:
    text = Path(md_path).read_text(encoding="utf-8")
    text = _PAGE_MARK.sub("<hr/>", text)
    html_body = md_lib.markdown(text, extensions=["tables"])
    html = (
        "<body style='font-family:sans-serif; font-size:10px'>"
        + html_body
        + "</body>"
    )

    writer = fitz.DocumentWriter(pdf_path)
    story = fitz.Story(html=html)
    more = 1
    while more:
        dev = writer.begin_page(_US_LETTER)
        # leave a margin inside the page
        where = _US_LETTER + (36, 36, -36, -36)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
