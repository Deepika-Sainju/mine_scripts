"""Custom recognizers for HIPAA-specific identifiers Presidio doesn't ship out of the box."""
from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer, RecognizerRegistry


def register_custom_recognizers(registry: RecognizerRegistry) -> None:
    """Add HIPAA-specific recognizers. Presidio already covers SSN, credit cards, phones,
    emails, IPs, US driver's licenses, passports, ITINs, medical licenses, etc."""

    # Medical Record Number — pattern + contextual boost
    mrn = PatternRecognizer(
        supported_entity="MEDICAL_RECORD_NUMBER",
        name="mrn_recognizer",
        patterns=[
            # Strongly labeled: "MRN: 12345678", "Medical Record #: ABC-123"
            Pattern(
                "mrn_labeled",
                r"\b(?:MRN|Medical[ _-]?Record(?:[ _-]?(?:Number|No\.?|#))?|MR#)[:\s#]*([A-Z0-9\-]{4,15})\b",
                0.85,
            ),
        ],
        context=["mrn", "medical", "record", "patient", "chart"],
    )
    registry.add_recognizer(mrn)

    # Device identifier (HIPAA Safe Harbor identifier #14)
    device = PatternRecognizer(
        supported_entity="DEVICE_IDENTIFIER",
        name="device_recognizer",
        patterns=[
            Pattern(
                "device_serial",
                r"\b(?:Device(?:[ _-]ID)?|Serial(?:[ _-]?(?:No\.?|Number|#))?|SN)[:\s#]*([A-Z0-9\-]{6,20})\b",
                0.8,
            ),
        ],
        context=["device", "serial", "identifier", "implant", "pacemaker"],
    )
    registry.add_recognizer(device)

    # Health plan beneficiary number (HIPAA identifier #9)
    health_plan = PatternRecognizer(
        supported_entity="HEALTH_PLAN_NUMBER",
        name="health_plan_recognizer",
        patterns=[
            Pattern(
                "hpn_labeled",
                r"\b(?:Health[ _-]?Plan(?:[ _-]?(?:Number|No\.?|#|ID))?|Member[ _-]?ID|Beneficiary(?:[ _-]?(?:Number|No\.?|#))?)[:\s#]*([A-Z0-9\-]{6,15})\b",
                0.85,
            ),
        ],
        context=["health", "plan", "member", "beneficiary", "insurance", "policy"],
    )
    registry.add_recognizer(health_plan)

    # Account number (HIPAA identifier #8)
    account = PatternRecognizer(
        supported_entity="ACCOUNT_NUMBER",
        name="account_recognizer",
        patterns=[
            Pattern(
                "account_labeled",
                r"\b(?:Account(?:[ _-]?(?:Number|No\.?|#))?|Acct\.?)[:\s#]*([0-9\-]{6,20})\b",
                0.8,
            ),
        ],
        context=["account", "acct", "billing"],
    )
    registry.add_recognizer(account)
