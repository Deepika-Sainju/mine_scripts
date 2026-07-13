"""JSON handler — recursively anonymizes string values."""
from __future__ import annotations

import json
from pathlib import Path

from .base import FormatHandler


class JSONHandler(FormatHandler):
    extensions = (".json",)

    def process(self, input_path: Path, output_path: Path, engine) -> dict:
        data = json.loads(input_path.read_text(encoding="utf-8"))
        findings: list[dict] = []
        result = self._walk(data, engine, findings)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"format": "json", "findings": findings}

    def _walk(self, node, engine, findings):
        if isinstance(node, dict):
            return {k: self._walk(v, engine, findings) for k, v in node.items()}
        if isinstance(node, list):
            return [self._walk(item, engine, findings) for item in node]
        if isinstance(node, str) and node.strip():
            anonymized, results = engine.anonymize(node)
            findings.extend(
                {"entity_type": r.entity_type, "score": round(r.score, 3)} for r in results
            )
            return anonymized
        return node
