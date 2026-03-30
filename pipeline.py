"""Example pipeline configuration for piighost-api.

This file is loaded by the server via the ``module:variable`` pattern::

    piighost-api serve pipeline:pipeline

Combines GLiNER2 (semantic NER) with regex detectors (common, EU, US)
for comprehensive PII coverage.
"""

from gliner2 import GLiNER2
from piighost.anonymizer import Anonymizer
from piighost.detector import CompositeDetector, RegexDetector
from piighost.detector.gliner2 import Gliner2Detector
from piighost.linker.entity import ExactEntityLinker
from piighost.pipeline.thread import ThreadAnonymizationPipeline
from piighost.placeholder import CounterPlaceholderFactory
from piighost.resolver import (
    ConfidenceSpanConflictResolver,
    MergeEntityConflictResolver,
)

# ------------------------------------------------------------------
# GLiNER2 detector : semantic NER labels
# ------------------------------------------------------------------
PII_LABELS = [
    # Identity
    "person",
    "first name",
    "last name",
    "date of birth",
    "age",
    "gender",
    "nationality",
    # Contact
    "email address",
    "phone number",
    "physical address",
    "zip code",
    "city",
    # Official IDs
    "social security number",
    "passport number",
    "driver's license number",
    "national id number",
    "tax identification number",
    # Financial
    "credit card number",
    "bank account number",
    "IBAN",
    # Technical
    "IP address",
    "license plate number",
    "username",
    "password",
    # Professional
    "organization",
    "company",
    "job title",
    # Medical
    "medical condition",
    "medication",
    "health insurance number",
]
model = GLiNER2.from_pretrained("fastino/gliner2-multi-v1")
gliner2_detector = Gliner2Detector(
    model=model,
    threshold=0.5,
    labels=PII_LABELS,
)

# ------------------------------------------------------------------
# Common regex detector : universal patterns
# ------------------------------------------------------------------

common_detector = RegexDetector(
    patterns={
        "EMAIL": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        "IP_V4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        "IP_V6": (
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
            r"|\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b"
            r"|\b::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}\b"
        ),
        "URL": r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?\)\]}]",
        "CREDIT_CARD": r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}\b",
        "PHONE_INTERNATIONAL": r"\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{1,4}){1,4}",
        "OPENAI_API_KEY": r"sk-(?:proj-)?[A-Za-z0-9\-_]{20,}",
        "AWS_ACCESS_KEY": r"\bAKIA[0-9A-Z]{16}\b",
        "GITHUB_TOKEN": r"\bgh[ps]_[A-Za-z0-9_]{36,}\b",
        "STRIPE_KEY": r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{24,}\b",
    }
)

# ------------------------------------------------------------------
# EU regex detector : European patterns
# ------------------------------------------------------------------

eu_detector = RegexDetector(
    patterns={
        "EU_IBAN": r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b",
        "EU_VAT": r"\b[A-Z]{2}\d{8,12}\b",
        "FR_SSN": r"\b[12]\d{2}(?:0[1-9]|1[0-2])\d{2}\d{3}\d{3}\d{2}(?:\s?\d{2})?\b",
        "FR_PHONE": r"\b(?:\+33|0)[1-9](?:[\s.\-]?\d{2}){4}\b",
        "FR_ZIP": r"\b(?:0[1-9]|[1-8]\d|9[0-8])\d{3}\b",
        "DE_PHONE": r"\b(?:\+49|0)\d{2,5}[\s/\-]?\d{3,10}\b",
        "DE_ZIP": r"\b(?:0[1-9]|[1-9]\d)\d{3}\b",
        "UK_NINO": r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b",
        "UK_NHS": r"\b\d{3}[\s\-]?\d{3}[\s\-]?\d{4}\b",
        "UK_POSTCODE": r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b",
    }
)

# ------------------------------------------------------------------
# US regex detector : American patterns
# ------------------------------------------------------------------

us_detector = RegexDetector(
    patterns={
        "US_SSN": r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
        "US_PHONE": (
            r"\b(?:\+1[\s.\-]?)?"
            r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"
        ),
        "US_PASSPORT": r"\b[A-Z]\d{8}\b",
        "US_ZIP_CODE": r"\b\d{5}(?:-\d{4})?\b",
        "US_EIN": r"\b\d{2}-\d{7}\b",
        "US_BANK_ROUTING": r"\b\d{9}\b",
    }
)

# ------------------------------------------------------------------
# Composite detector : all combined
# ------------------------------------------------------------------

detector = CompositeDetector(
    detectors=[
        gliner2_detector,
        common_detector,
        eu_detector,
        us_detector,
    ],
)

# ------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------

entity_linker = ExactEntityLinker()
entity_resolver = MergeEntityConflictResolver()
span_resolver = ConfidenceSpanConflictResolver()

ph_factory = CounterPlaceholderFactory()
anonymizer = Anonymizer(ph_factory=ph_factory)

pipeline = ThreadAnonymizationPipeline(
    detector=detector,
    span_resolver=span_resolver,
    entity_linker=entity_linker,
    entity_resolver=entity_resolver,
    anonymizer=anonymizer,
)
