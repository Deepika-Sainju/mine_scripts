"""Regex detection of direct identifiers (SSN, phone, email, address, labeled ids).

These patterns are unioned with known-member matching by deid.member — the only
detection the pipeline does. There is no NER, no Philter, no model: the member
manifest tells us who the patient is, so no discovery-based detection is needed
(see the README's Notes & caveats).
"""
