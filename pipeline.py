"""Example pipeline configuration for piighost-api.

This file is loaded by the server via the ``module:variable`` pattern::

    piighost-api serve pipeline:pipeline
"""

from gliner2 import GLiNER2
from piighost.anonymizer import Anonymizer
from piighost.detector.gliner2 import Gliner2Detector
from piighost.linker.entity import ExactEntityLinker
from piighost.pipeline.thread import ThreadAnonymizationPipeline
from piighost.placeholder import CounterPlaceholderFactory
from piighost.resolver import MergeEntityConflictResolver, ConfidenceSpanConflictResolver

entity_linker = ExactEntityLinker()
entity_resolver = MergeEntityConflictResolver()
span_resolver = ConfidenceSpanConflictResolver()

ph_factory = CounterPlaceholderFactory()
anonymizer = Anonymizer(ph_factory=ph_factory)

model = GLiNER2.from_pretrained("fastino/gliner2-multi-v1")
detector = Gliner2Detector(
    model=model,
    threshold=0.5,
    labels=["PERSON", "LOCATION"],
)

pipeline = ThreadAnonymizationPipeline(
    detector=detector,
    span_resolver=span_resolver,
    entity_linker=entity_linker,
    entity_resolver=entity_resolver,
    anonymizer=anonymizer,
)
