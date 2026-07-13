"""Plain text handler (.txt, .log, .md)."""
from __future__ import annotations

from pathlib import Path

from .base import FormatHandler


class TextHandler(FormatHandler):
    extensions = (".txt", ".log", ".md")

    def process(self, input_path: Path, output_path: Path, engine) -> dict:
        text = input_path.read_text(encoding="utf-8", errors="replace")
        anonymized, findings = engine.anonymize(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(anonymized, encoding="utf-8")
        return {
            "format": "text",
            "findings": [
                {
                    "entity_type": f.entity_type,
                    "score": round(f.score, 3),
                    "start": f.start,
                    "end": f.end,
                }
                for f in findings
            ],
        }
