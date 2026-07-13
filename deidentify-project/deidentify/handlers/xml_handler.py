"""XML handler — anonymizes element text, tail, and attribute values."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from .base import FormatHandler


class XMLHandler(FormatHandler):
    extensions = (".xml",)

    def process(self, input_path: Path, output_path: Path, engine) -> dict:
        parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
        tree = etree.parse(str(input_path), parser)
        findings: list[dict] = []

        for element in tree.iter():
            # element text
            if element.text and element.text.strip():
                anonymized, results = engine.anonymize(element.text)
                element.text = anonymized
                findings.extend(
                    {"entity_type": r.entity_type, "score": round(r.score, 3)} for r in results
                )
            # text between this element's end and next sibling's start
            if element.tail and element.tail.strip():
                anonymized, results = engine.anonymize(element.tail)
                element.tail = anonymized
                findings.extend(
                    {"entity_type": r.entity_type, "score": round(r.score, 3)} for r in results
                )
            # attributes
            for attr_name, attr_val in list(element.attrib.items()):
                if attr_val and attr_val.strip():
                    anonymized, results = engine.anonymize(attr_val)
                    element.attrib[attr_name] = anonymized
                    findings.extend(
                        {"entity_type": r.entity_type, "score": round(r.score, 3)}
                        for r in results
                    )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        tree.write(
            str(output_path), encoding="utf-8", xml_declaration=True, pretty_print=True
        )
        return {"format": "xml", "findings": findings}
