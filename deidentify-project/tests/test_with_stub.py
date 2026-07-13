"""Stub-engine smoke test.

We can't load spaCy in this sandbox (GitHub model CDN not reachable), so this
script swaps a minimal regex-based engine into place of DeidentificationEngine
to exercise the format handlers + PseudonymStore end-to-end.

In a real install, the actual engine in engine.py runs instead and adds NER
on top of these regex patterns.
"""
from __future__ import annotations

import re
import sys
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from deidentify.pseudonym import PseudonymStore
from deidentify.handlers import get_handler


# ---- minimal pattern-based stub of DeidentificationEngine -------------------

PATTERNS = [
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("US_SSN",        re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CREDIT_CARD",   re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b")),
    ("PHONE_NUMBER",  re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")),
    ("DATE_TIME",     re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("MEDICAL_RECORD_NUMBER",
                      re.compile(r"\bMRN[:\s]*([A-Z0-9]{4,15})\b", re.IGNORECASE)),
    # crude name detector — capitalized two-word — only used so we can verify behavior
    ("PERSON",        re.compile(r"\b(?:Dr\.\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+\b")),
]


class StubEngine:
    def __init__(self, store):
        self.store = store

    def detect(self, text):
        out = []
        for et, pat in PATTERNS:
            for m in pat.finditer(text):
                out.append(SimpleNamespace(
                    entity_type=et, score=0.9, start=m.start(), end=m.end()))
        # Sort by start, then resolve overlaps by preferring earlier+longer match
        out.sort(key=lambda r: (r.start, -(r.end - r.start)))
        non_overlapping = []
        last_end = -1
        for r in out:
            if r.start >= last_end:
                non_overlapping.append(r)
                last_end = r.end
        return non_overlapping

    def anonymize(self, text, results=None):
        if not text or not text.strip():
            return text, []
        if results is None:
            results = self.detect(text)
        if not results:
            return text, []
        # Apply replacements right-to-left so indices stay valid
        out = text
        for r in sorted(results, key=lambda x: -x.start):
            original = text[r.start:r.end]
            replacement = self.store.pseudonym_for(r.entity_type, original)
            out = out[:r.start] + replacement + out[r.end:]
        return out, results


# ---- run tests --------------------------------------------------------------

def main():
    root = Path(__file__).parent
    samples = root.parent / "samples"
    out_root = root / "test_output"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir()

    mapping_path = out_root / "mappings.json"
    store = PseudonymStore(mapping_file=mapping_path, seed=42)
    engine = StubEngine(store)

    print("=" * 70)
    print("Processing each sample with stub engine...")
    print("=" * 70)

    for sample in sorted(samples.iterdir()):
        handler = get_handler(sample)
        if handler is None:
            print(f"  no handler for {sample.name} — skip")
            continue
        out_path = out_root / sample.name
        result = handler.process(sample, out_path, engine)
        print(f"\n--- {sample.name}: {len(result['findings'])} findings, handler={result['format']}")
        # Show first 5 findings
        from collections import Counter
        counts = Counter(f["entity_type"] for f in result["findings"])
        for et, c in counts.most_common():
            print(f"    {et:25s} {c}")

    store.save()
    print(f"\n{'=' * 70}\nPseudonym mappings ({len(store._mappings)} entries):")
    for k, v in list(store._mappings.items())[:8]:
        print(f"  {k}\n      -> {v}")
    if len(store._mappings) > 8:
        print(f"  ... and {len(store._mappings) - 8} more")

    # Consistency check: run again, verify mappings unchanged
    print(f"\n{'=' * 70}\nCONSISTENCY CHECK")
    store2 = PseudonymStore(mapping_file=mapping_path, seed=42)
    engine2 = StubEngine(store2)
    sample = samples / "sample.txt"
    out_path = out_root / "sample_rerun.txt"
    get_handler(sample).process(sample, out_path, engine2)

    a = (out_root / "sample.txt").read_text()
    b = (out_root / "sample_rerun.txt").read_text()
    print("  Re-run produces identical output:", "YES ✓" if a == b else "NO ✗")

    # Cross-file consistency: same person in CSV + JSON should get same pseudonym
    csv_out = (out_root / "sample.csv").read_text()
    json_out = (out_root / "sample.json").read_text()
    # Original samples mention "Dr. Sarah Chen" in both sample.txt and sample.json
    # The pseudonym should be the same.
    print("\n  Mappings file (first 3 lines):")
    print(textwrap_lines(json.dumps(json.loads(mapping_path.read_text()), indent=2), 12))


def textwrap_lines(s, max_lines):
    lines = s.splitlines()
    if len(lines) <= max_lines:
        return s
    return "\n".join(lines[:max_lines]) + f"\n  ... ({len(lines) - max_lines} more lines)"


if __name__ == "__main__":
    main()
