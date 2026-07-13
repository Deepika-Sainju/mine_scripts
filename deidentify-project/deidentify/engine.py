"""Detection + anonymization engine.

Wraps Presidio's analyzer (regex + spaCy NER) and anonymizer, routing every
detected entity through a PseudonymStore for consistent replacements.
"""
from __future__ import annotations

from typing import Optional

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from .pseudonym import PseudonymStore
from .recognizers import register_custom_recognizers


# Broad coverage: HIPAA + GDPR + financial
DEFAULT_ENTITIES = [
    # Generic / GDPR
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION",
    "DATE_TIME", "IP_ADDRESS", "URL", "NRP",
    # US (HIPAA-relevant)
    "US_SSN", "US_DRIVER_LICENSE", "US_PASSPORT", "US_ITIN", "US_BANK_NUMBER",
    # Financial
    "CREDIT_CARD", "IBAN_CODE", "CRYPTO",
    # UK
    "UK_NHS", "UK_NINO",
    # Medical
    "MEDICAL_LICENSE",
    # Custom HIPAA (defined in recognizers.py)
    "MEDICAL_RECORD_NUMBER", "DEVICE_IDENTIFIER", "HEALTH_PLAN_NUMBER", "ACCOUNT_NUMBER",
]


class DeidentificationEngine:
    def __init__(
        self,
        pseudonym_store: PseudonymStore,
        entities: Optional[list[str]] = None,
        confidence: float = 0.4,
        spacy_model: str = "en_core_web_md",
        language: str = "en",
    ):
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers()
        register_custom_recognizers(registry)

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": language, "model_name": spacy_model}],
            }
        )
        self.analyzer = AnalyzerEngine(
            registry=registry, nlp_engine=provider.create_engine()
        )
        self.anonymizer = AnonymizerEngine()
        self.store = pseudonym_store
        self.entities = entities or DEFAULT_ENTITIES
        self.confidence = confidence
        self.language = language

        # Build operator config once — maps each entity type to a lambda that
        # looks up the consistent pseudonym for the matched value.
        self._operators = {
            entity: OperatorConfig(
                "custom",
                {
                    # Default-arg trick avoids the classic late-binding closure bug
                    "lambda": (lambda v, et=entity: self.store.pseudonym_for(et, v))
                },
            )
            for entity in self.entities
        }

    def detect(self, text: str):
        if not text or not text.strip():
            return []
        return self.analyzer.analyze(
            text=text,
            language=self.language,
            entities=self.entities,
            score_threshold=self.confidence,
        )

    def anonymize(self, text: str, results=None):
        """Returns (anonymized_text, list_of_findings)."""
        if not text or not text.strip():
            return text, []
        if results is None:
            results = self.detect(text)
        if not results:
            return text, []
        out = self.anonymizer.anonymize(
            text=text, analyzer_results=results, operators=self._operators
        )
        return out.text, results
