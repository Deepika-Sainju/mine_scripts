"""Handler registry / dispatcher."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import FormatHandler
from .csv_handler import CSVHandler
from .docx_handler import DocxHandler
from .json_handler import JSONHandler
from .pdf_handler import PDFHandler
from .text_handler import TextHandler
from .xml_handler import XMLHandler

# Instantiate once
HANDLERS: list[FormatHandler] = [
    TextHandler(),
    CSVHandler(),
    JSONHandler(),
    XMLHandler(),
    PDFHandler(),
    DocxHandler(),
]


def get_handler(path: Path) -> Optional[FormatHandler]:
    ext = path.suffix.lower()
    for h in HANDLERS:
        if ext in h.extensions:
            return h
    return None


def supported_extensions() -> list[str]:
    out: list[str] = []
    for h in HANDLERS:
        out.extend(h.extensions)
    return out
