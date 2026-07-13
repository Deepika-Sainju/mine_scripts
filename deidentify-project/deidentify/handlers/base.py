"""Abstract base for file-format handlers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class FormatHandler(ABC):
    extensions: tuple = ()

    @abstractmethod
    def process(self, input_path: Path, output_path: Path, engine) -> dict:
        """Read input, anonymize, write to output. Return metadata dict with
        'format' (str) and 'findings' (list of dicts with entity_type, score, ...)."""
        ...
