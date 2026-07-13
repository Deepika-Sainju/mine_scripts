"""CSV/TSV handler.

In addition to running the detector per cell, hints from sensitive column names
(e.g. 'patient_name', 'ssn') force-pseudonymize values the model might miss
when stripped of surrounding context.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .base import FormatHandler


SENSITIVE_HEADERS = {
    # name-like
    "name": "PERSON", "first_name": "PERSON", "last_name": "PERSON",
    "full_name": "PERSON", "patient_name": "PERSON", "patient": "PERSON",
    "customer_name": "PERSON",
    # contact
    "email": "EMAIL_ADDRESS", "email_address": "EMAIL_ADDRESS",
    "phone": "PHONE_NUMBER", "phone_number": "PHONE_NUMBER",
    "mobile": "PHONE_NUMBER", "telephone": "PHONE_NUMBER",
    # govt id
    "ssn": "US_SSN", "social_security": "US_SSN", "social_security_number": "US_SSN",
    "passport": "US_PASSPORT", "driver_license": "US_DRIVER_LICENSE",
    # dates / location
    "dob": "DATE_TIME", "date_of_birth": "DATE_TIME", "birth_date": "DATE_TIME",
    "address": "LOCATION", "street": "LOCATION", "city": "LOCATION",
    "state": "LOCATION", "zip": "LOCATION", "postal_code": "LOCATION",
    # medical
    "mrn": "MEDICAL_RECORD_NUMBER", "medical_record_number": "MEDICAL_RECORD_NUMBER",
    "patient_id": "MEDICAL_RECORD_NUMBER",
    # financial
    "credit_card": "CREDIT_CARD", "cc_number": "CREDIT_CARD",
    "card_number": "CREDIT_CARD", "iban": "IBAN_CODE",
    "account_number": "ACCOUNT_NUMBER", "bank_account": "US_BANK_NUMBER",
    # network
    "ip": "IP_ADDRESS", "ip_address": "IP_ADDRESS",
}


def _classify_header(header: str) -> str | None:
    """Return an entity type if the header name looks sensitive, else None."""
    h = header.strip().lower().replace(" ", "_").replace("-", "_")
    if h in SENSITIVE_HEADERS:
        return SENSITIVE_HEADERS[h]
    # substring match for variants like "patient_first_name"
    for key, ent in SENSITIVE_HEADERS.items():
        if key in h:
            return ent
    return None


class CSVHandler(FormatHandler):
    extensions = (".csv", ".tsv")

    def process(self, input_path: Path, output_path: Path, engine) -> dict:
        delimiter = "\t" if input_path.suffix.lower() == ".tsv" else ","
        with input_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f, delimiter=delimiter))

        if not rows:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("", encoding="utf-8")
            return {"format": "csv", "findings": []}

        header = rows[0]
        # Map column index -> hinted entity type (or None)
        col_hints: list[str | None] = [_classify_header(h) for h in header]

        all_findings: list[dict] = []
        out_rows = [header]
        for row in rows[1:]:
            new_row = []
            for i, cell in enumerate(row):
                if not cell or not cell.strip():
                    new_row.append(cell)
                    continue
                anonymized, findings = engine.anonymize(cell)
                if not findings and col_hints[i] is not None:
                    # Detector missed it but the column header says it's PII.
                    et = col_hints[i]
                    anonymized = engine.store.pseudonym_for(et, cell)
                    all_findings.append({"entity_type": et, "score": 1.0, "source": "header_hint"})
                else:
                    all_findings.extend(
                        {"entity_type": f.entity_type, "score": round(f.score, 3)}
                        for f in findings
                    )
                new_row.append(anonymized)
            out_rows.append(new_row)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerows(out_rows)

        return {"format": "csv", "findings": all_findings}
