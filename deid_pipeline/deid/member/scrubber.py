"""MemberScrubber — one member's de-identification session.

Constructed from (MemberPHI, seed), it owns everything needed to replace that
member's PHI consistently:

  * a SurrogateGenerator whose collision guard is armed AT CONSTRUCTION with
    the member's real tokens — a fake can never reproduce them, and no caller
    can forget to arm the guard (it used to be a separate set_known step, and
    the CSV path forgot it);
  * the member's canonical fakes (name_part / fake_dob / identifier), gender
    threaded from the manifest automatically;
  * the two scrub profiles: `scrub_text` for born-digital text (exact matching
    only) and `scrub_pages` for OCR output (fuzzy names + patient-label
    discovery + labeled-DOB pass ON).

It deliberately KEEPS: provider names / NPIs, every date except the member DOB,
and all clinical content. No NER, no models — precision over recall (accepted
gap: relatives / other people's names in free text are not caught — see the
README's known-gap note).
"""
from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from deid.core.models import Detection, MemberPHI
from deid.core.spans import merge_detections
from deid.core.text import ALPHA_TOKEN_RE, apply_case, bounded_literal
from deid.member.matching import (
    CONTAINER_TYPES, DOB_LABEL_RE, generic_spans, label_name_tokens, member_spans,
)
from deid.surrogate.generator import SurrogateGenerator


class MemberScrubber:
    """All de-identification for ONE member: fakes + free-text scrubbing."""

    def __init__(self, phi: MemberPHI, seed: int = 1234) -> None:
        self.phi = phi
        self._seed = seed
        self._gen = SurrogateGenerator(seed=seed, known_tokens=_member_tokens(phi))

    def for_identity(self, phi: MemberPHI) -> "MemberScrubber":
        """A session for a refined identity (same seed).

        The C-CDA walker uses this after resolving the patient's identity from
        the chart itself (chart-authoritative, manifest as fallback) — the guard
        is re-armed with the resolved tokens.
        """
        return MemberScrubber(phi, seed=self._seed)

    # -- the member's canonical fakes (chart ↔ member CSV ↔ claims CSV) -------
    def name_part(self, value: str, kind: str = "last") -> str:
        """Fake name part; gender for a first name comes from the member."""
        return self._gen.name_part(value, kind, self.phi.gender)

    def fake_dob(self, value: str) -> str:
        return self._gen.fake_dob(value)

    def identifier(self, value: str) -> str:
        return self._gen.identifier(value)

    def surrogate(self, entity_type: str, value: str) -> str:
        """Generic surrogate for structured non-name fields (addr, telecom, …)."""
        return self._gen.surrogate(entity_type, value)

    # -- scrub profiles --------------------------------------------------------
    def scrub_text(self, text: str, *, use_regex: bool = True) -> Tuple[str, List[dict]]:
        """De-identify born-digital text (C-CDA narrative): exact member
        matching + generic direct identifiers. Returns (new_text, report)."""
        return self._scrub(text, use_regex=use_regex,
                           fuzzy=False, extra_names=None, dob_labels=False)

    def scrub_pages(self, pages, *, use_regex: bool = True) -> dict:
        """De-identify OCR pages [(page_no, text), …] -> {markdown, report,
        by_source}. The OCR profile is ON: fuzzy name matching, patient-label
        discovery (gathered across ALL pages, scrubbed document-wide), and the
        labeled-DOB pass."""
        discovered: Set[str] = set()
        for _pageno, text in pages:
            discovered |= label_name_tokens(text)
        # drop tokens already covered by the member's own name (handled with correct kind)
        member_toks = {m.group(0).lower()
                       for v in (self.phi.first, self.phi.middle, self.phi.last)
                       for m in ALPHA_TOKEN_RE.finditer(v or "")}
        discovered = {d for d in discovered if d.lower() not in member_toks}

        out_parts, report = [], []
        for pageno, text in pages:
            new_text, page_report = self._scrub(text, use_regex=use_regex, fuzzy=True,
                                                extra_names=discovered, dob_labels=True)
            for r in page_report:
                report.append({"page": pageno, **r})
            out_parts.append(f"<!-- page {pageno} -->\n\n{new_text}\n")

        by_source: Dict[str, int] = {}
        for r in report:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        return {"markdown": "\n\n---\n\n".join(out_parts), "report": report,
                "by_source": by_source}

    # -- internals -------------------------------------------------------------
    def _scrub(self, text: str, *, use_regex: bool, fuzzy: bool,
               extra_names, dob_labels: bool) -> Tuple[str, List[dict]]:
        if not text:
            return text, []
        member = member_spans(text, self.phi, fuzzy=fuzzy)
        for name in (extra_names or ()):
            for m in re.finditer(bounded_literal(name), text, re.I):
                member.append(Detection("MEMBER_LABEL_NAME", m.start(), m.end(), 0.9, "label"))
        if dob_labels:
            for m in DOB_LABEL_RE.finditer(text):
                member.append(Detection("MEMBER_DOB", m.start("value"), m.end("value"),
                                         0.95, "dob-label"))
        generic = generic_spans(text) if use_regex else []
        # A member name token can sit INSIDE a larger identifier (e.g. 'delgado' in
        # 'marcus.delgado@example.com'). Drop the contained member span so the whole
        # email/URL/IP/address is surrogated as that identifier, not collapsed to a name.
        containers = [d for d in generic if d.entity_type in CONTAINER_TYPES]
        member = [d for d in member if not any(
            c.start <= d.start and d.end <= c.end and (c.end - c.start) > (d.end - d.start)
            for c in containers)]
        dets = merge_detections(member + generic)

        report = []
        for d in sorted(dets, key=lambda x: x.start, reverse=True):
            original = text[d.start:d.end]
            sur = self._surrogate_for(d.entity_type, original)
            report.append({"type": d.entity_type, "original": original,
                           "surrogate": sur, "score": round(d.score, 3), "source": d.source})
            text = text[:d.start] + sur + text[d.end:]
        report.reverse()  # back to reading order
        return text, report

    def _surrogate_for(self, etype: str, original: str) -> str:
        # Name parts: surrogate the CANONICAL member value (not the matched text), so a
        # fuzzy/OCR-mangled hit maps to the same fake as the clean one and the CSVs;
        # then re-case to the matched occurrence.
        phi = self.phi
        if etype == "MEMBER_FIRST":
            return apply_case(original, self.name_part(phi.first or original, "first"))
        if etype == "MEMBER_MIDDLE":
            return apply_case(original, self.name_part(phi.middle or original, "middle"))
        if etype == "MEMBER_LAST":
            return apply_case(original, self.name_part(phi.last or original, "last"))
        if etype == "MEMBER_LABEL_NAME":  # label-discovered name (no canonical value)
            return self.name_part(original, "last")
        if etype == "MEMBER_DOB":
            return self.fake_dob(original)
        if etype == "MEMBER_ID":
            return self.identifier(original)
        return self.surrogate(etype, original)  # generic regex types (format-preserving)


def _member_tokens(phi: MemberPHI) -> Set[str]:
    """The member's real tokens the collision guard must never regenerate."""
    toks: Set[str] = set()
    for v in (phi.first, phi.middle, phi.last, phi.member_id):
        for m in ALPHA_TOKEN_RE.finditer(v or ""):
            toks.add(m.group(0))
    return toks
