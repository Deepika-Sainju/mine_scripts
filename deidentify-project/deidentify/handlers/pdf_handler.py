"""PDF handler — uses PyMuPDF (fitz) to redact original PII and insert pseudonyms.

Caveats:
- Text replacement isn't pixel-perfect: PyMuPDF redacts the original glyphs and
  inserts replacement text into the redacted rectangle. Font/size matching is
  approximate, and very long replacements may overflow.
- Scanned PDFs (image-only) need OCR — not handled in this version.
"""
from __future__ import annotations

from pathlib import Path

from .base import FormatHandler


class PDFHandler(FormatHandler):
    extensions = (".pdf",)

    def process(self, input_path: Path, output_path: Path, engine) -> dict:
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise RuntimeError(
                "PyMuPDF (fitz) is required for PDF processing. "
                "Install with: pip install PyMuPDF"
            ) from e

        doc = fitz.open(str(input_path))
        findings: list[dict] = []

        try:
            for page in doc:
                text = page.get_text()
                if not text.strip():
                    continue
                _, results = engine.anonymize(text)

                # For each detected span, find its rect(s) on the page and
                # schedule a redaction with the replacement text inline.
                # Dedupe by (original_value) so we don't double-process duplicates.
                seen: set[tuple[str, str]] = set()
                for r in results:
                    original = text[r.start:r.end]
                    key = (r.entity_type, original)
                    if key in seen:
                        continue
                    seen.add(key)

                    replacement = engine.store.pseudonym_for(r.entity_type, original)
                    rects = page.search_for(original)
                    for rect in rects:
                        page.add_redact_annot(
                            rect,
                            text=replacement,
                            fontsize=max(8, rect.height * 0.75),
                            fill=(1, 1, 1),  # white fill behind replacement
                        )
                    findings.append(
                        {
                            "entity_type": r.entity_type,
                            "score": round(r.score, 3),
                            "page": page.number,
                            "instances_on_page": len(rects),
                        }
                    )

                page.apply_redactions()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(output_path), garbage=4, deflate=True)
        finally:
            doc.close()

        return {"format": "pdf", "findings": findings}
