"""Per-format document de-identifiers + chart dispatch by file type.

`deidentify_chart` routes a chart to its handler by extension. Handlers are
imported lazily so CSV-only runs never touch the OCR stack (PyMuPDF/vLLM).
"""
from __future__ import annotations

from typing import Tuple

from deid.core.models import MemberPHI


def deidentify_chart_to(src_path: str, out_file, phi: MemberPHI, *, seed, use_regex,
                        report_path, show_values, scrubber=None) -> Tuple[str, dict]:
    """De-identify a chart to an EXPLICIT output path. Returns (out_path, result).

    Routing is by the SOURCE file's extension (C-CDA/XML → structured parse, CPU;
    anything else → scanned PDF via OCR, GPU) — the output format follows the
    source, not ``out_file``'s extension. ``out_file`` is written verbatim (used by
    the mapping-driven flow, where the caller dictates the exact path/name). Pass
    ``scrubber`` to reuse one member session; otherwise one is built from ``phi``.
    """
    from deid.member.scrubber import MemberScrubber
    if scrubber is None:
        scrubber = MemberScrubber(phi, seed=seed)
    out_file = str(out_file)
    if str(src_path).lower().endswith(".xml"):
        from deid.documents.ccda import deidentify_ccda_file_member
        res = deidentify_ccda_file_member(src_path, out_file, scrubber,
                                          use_regex=use_regex, report_path=report_path,
                                          show_values=show_values)
    else:
        from deid.documents.pdf import deidentify_pdf_file_member  # OCR stack, lazy
        res = deidentify_pdf_file_member(src_path, out_file, scrubber,
                                         use_regex=use_regex, report_path=report_path,
                                         show_values=show_values)
    return out_file, res


def deidentify_chart(src_path: str, out_stem, phi: MemberPHI, *, seed, use_regex,
                     report_path, show_values, scrubber=None) -> Tuple[str, dict]:
    """De-identify a chart, writing ``<out_stem>_deid.<ext>`` (sibling-Chart flow).

    Thin wrapper over `deidentify_chart_to` that appends the `_deid` suffix + the
    format extension. Pass ``scrubber`` to reuse one member session across a main
    chart and its attachments (shared fakes + collision guard).
    """
    ext = ".xml" if str(src_path).lower().endswith(".xml") else ".pdf"
    return deidentify_chart_to(src_path, f"{out_stem}_deid{ext}", phi, seed=seed,
                               use_regex=use_regex, report_path=report_path,
                               show_values=show_values, scrubber=scrubber)
