"""De-identify a PDF end to end: OCR -> member scrub -> markdown -> PDF.

Every PDF is treated as scanned and sent through LightOnOCR-2-1B, then the
known member's PHI (+ generic direct identifiers) is scrubbed from the page
text and the result rendered back to PDF.

Requires vLLM + a CUDA GPU (see pyproject.toml; `uv sync`).
"""
from __future__ import annotations

from pathlib import Path

from deid.core.reporting import write_detection_report
from deid.member.scrubber import MemberScrubber
from deid.ocr.lighton import ocr_pdf, pages_as_pairs
from deid.ocr.render import md_to_pdf


def deidentify_pdf_file_member(input_pdf, out_pdf, scrubber: MemberScrubber, *,
                               report_path=None, keep_intermediates=False,
                               use_regex=True, show_values=False) -> dict:
    """Member-file-driven PDF de-id: OCR, scrub the KNOWN member's PHI + generic
    identifiers (keep providers / dates / clinical), render back to PDF.

    NB: with keep_intermediates the OCR pages.json (RAW text, real PHI) is kept
    next to the output — only use it on synthetic/dev data.
    """
    out = Path(out_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)

    ocr_dir = str(out.parent / f"{out.stem}.ocr") if keep_intermediates else None
    ocr_result = ocr_pdf(str(input_pdf), out_dir=ocr_dir)

    res = scrubber.scrub_pages(pages_as_pairs(ocr_result), use_regex=use_regex)
    res["num_pages"] = ocr_result["num_pages"]

    deid_md = out.with_name(f"{out.stem}.deid.md")
    deid_md.write_text(res["markdown"], encoding="utf-8")
    md_to_pdf(str(deid_md), str(out_pdf))

    if report_path:
        write_detection_report(report_path, res["report"], res["by_source"], show_values)
    if not keep_intermediates:
        deid_md.unlink(missing_ok=True)
    return res
