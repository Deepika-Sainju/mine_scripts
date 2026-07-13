"""Per-file audit log of detections, useful for compliance review."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class AuditLog:
    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = Path(log_path) if log_path else None
        self.entries: list[dict] = []
        self.started = datetime.now(timezone.utc).isoformat()

    def record(self, input_path, output_path, result, error=None) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": str(input_path),
            "output": str(output_path) if output_path else None,
            "status": "error" if error else "ok",
            "error": str(error) if error else None,
        }
        if result:
            entry["format"] = result.get("format")
            findings = result.get("findings", [])
            counts: dict[str, int] = {}
            for f in findings:
                counts[f["entity_type"]] = counts.get(f["entity_type"], 0) + 1
            entry["entity_counts"] = counts
            entry["finding_count"] = len(findings)
        self.entries.append(entry)

    def save(self) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w") as f:
            json.dump({"started": self.started, "files": self.entries}, f, indent=2)

    def summary(self) -> dict:
        total = len(self.entries)
        errors = sum(1 for e in self.entries if e["status"] == "error")
        all_counts: dict[str, int] = {}
        for e in self.entries:
            for et, c in e.get("entity_counts", {}).items():
                all_counts[et] = all_counts.get(et, 0) + c
        return {"files_processed": total, "errors": errors, "entity_totals": all_counts}
