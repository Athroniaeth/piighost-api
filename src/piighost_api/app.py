"""Litestar application with PII anonymization API routes."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import msgspec
from keyshield import ApiKeyService
from keyshield.hasher.argon2 import Argon2ApiKeyHasher
from keyshield.repositories.in_memory import InMemoryApiKeyRepository
from litestar import Litestar, get, post, put
from litestar.exceptions import NotFoundException
from litestar.openapi import OpenAPIConfig

from piighost.exceptions import CacheMissError
from piighost.models import Detection, Entity, Span
from piighost.pipeline.thread import ThreadAnonymizationPipeline

from piighost_api.auth import create_auth_guard
from piighost_api.loader import load_pipeline
from piighost_api.observation import load_observation_service

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# msgspec request/response structs
# ------------------------------------------------------------------


class DetectionSchema(msgspec.Struct):
    text: str
    label: str
    start_pos: int
    end_pos: int
    confidence: float


class EntitySchema(msgspec.Struct):
    label: str
    placeholder: str
    detections: list[DetectionSchema]


class DetectRequest(msgspec.Struct):
    text: str
    thread_id: str = "default"


class OverrideDetectRequest(msgspec.Struct):
    text: str
    detections: list[DetectionSchema]
    thread_id: str = "default"


class AnonymizeRequest(msgspec.Struct):
    text: str
    thread_id: str = "default"


class DeanonymizeRequest(msgspec.Struct):
    text: str
    thread_id: str = "default"


class AnonymizeResponse(msgspec.Struct):
    anonymized_text: str
    entities: list[EntitySchema]


class DeanonymizeResponse(msgspec.Struct):
    text: str
    entities: list[EntitySchema]


class DetectResponse(msgspec.Struct):
    entities: list[EntitySchema]


class DeanonymizeEntResponse(msgspec.Struct):
    text: str


class ConfigResponse(msgspec.Struct):
    labels: list[str] | None
    placeholder_factory: str


class IndexResponse(msgspec.Struct):
    name: str
    version: str
    docs: str


class HealthResponse(msgspec.Struct):
    status: str
    detector: str


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _serialize_entities(
    entities: list[Entity],
    pipeline: ThreadAnonymizationPipeline,
    thread_id: str,
) -> list[EntitySchema]:
    """Serialize piighost entities with their placeholder tokens."""
    resolved = pipeline.get_resolved_entities(thread_id)
    tokens = pipeline.ph_factory.create(resolved)

    token_lookup: dict[tuple[str, str], str] = {}
    for ent, tok in tokens.items():
        token_lookup[(ent.detections[0].text.lower(), ent.label)] = tok

    result: list[EntitySchema] = []
    for entity in entities:
        key = (entity.detections[0].text.lower(), entity.label)
        placeholder = token_lookup.get(key, "")

        detections = [
            DetectionSchema(
                text=d.text,
                label=d.label,
                start_pos=d.position.start_pos,
                end_pos=d.position.end_pos,
                confidence=d.confidence,
            )
            for d in entity.detections
        ]
        result.append(
            EntitySchema(
                label=entity.label, placeholder=placeholder, detections=detections
            )
        )
    return result


def _serialize_entities_plain(entities: list[Entity]) -> list[EntitySchema]:
    """Serialize entities without placeholder tokens (for detection preview)."""
    result: list[EntitySchema] = []
    for entity in entities:
        detections = [
            DetectionSchema(
                text=d.text,
                label=d.label,
                start_pos=d.position.start_pos,
                end_pos=d.position.end_pos,
                confidence=d.confidence,
            )
            for d in entity.detections
        ]
        result.append(
            EntitySchema(label=entity.label, placeholder="", detections=detections)
        )
    return result


def _get_detector_labels(pipeline: ThreadAnonymizationPipeline) -> list[str] | None:
    """Try to extract labels from the detector."""
    detector = pipeline._detector
    if hasattr(detector, "labels"):
        return list(detector.labels)
    return None


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------


def create_app(pipeline_path: str) -> Litestar:
    """Create and configure the Litestar application.

    Args:
        pipeline_path: Import path in ``module:variable`` format.

    Returns:
        A fully configured ``Litestar`` instance.
    """
    pipeline = load_pipeline(pipeline_path)

    observation = load_observation_service()
    if observation is not None:
        pipeline._observation = observation
        logger.info("Observation enabled: %s", type(observation).__name__)

    pepper = os.getenv("SECRET_PEPPER")
    hasher = Argon2ApiKeyHasher(pepper=pepper)
    repo = InMemoryApiKeyRepository()
    svc_api_keys = ApiKeyService(repo=repo, hasher=hasher)

    guards: list = []

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncGenerator[None]:
        try:
            await svc_api_keys.load_dotenv()
            guards.append(create_auth_guard(svc_api_keys))
            logger.info("API keys loaded — auth enabled")
        except Exception as exc:
            logger.warning("No valid API keys found (%s) — auth disabled", exc)
        logger.info("Pipeline ready: %s", type(pipeline._detector).__name__)
        yield

    # ------------------------------------------------------------------
    # Route handlers (closures over pipeline)
    # ------------------------------------------------------------------

    @get("/", exclude_from_auth=True)
    async def index() -> IndexResponse:
        return IndexResponse(
            name="piighost-api",
            version="0.1.0",
            docs="/schema/swagger",
        )

    @get("/health", exclude_from_auth=True)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            detector=type(pipeline._detector).__name__,
        )

    @get("/v1/config")
    async def get_config() -> ConfigResponse:
        labels = _get_detector_labels(pipeline)
        factory_name = type(pipeline.ph_factory).__name__
        return ConfigResponse(
            labels=labels,
            placeholder_factory=factory_name,
        )

    @post("/v1/detect")
    async def detect(data: DetectRequest) -> DetectResponse:
        pipeline._thread_id = data.thread_id
        entities = await pipeline.detect_entities(data.text)
        return DetectResponse(
            entities=_serialize_entities_plain(entities),
        )

    @put("/v1/detect")
    async def override_detect(data: OverrideDetectRequest) -> None:
        detections = [
            Detection(
                text=d.text,
                label=d.label,
                position=Span(d.start_pos, d.end_pos),
                confidence=d.confidence,
            )
            for d in data.detections
        ]
        await pipeline.override_detections(
            data.text,
            detections,
            thread_id=data.thread_id,
        )

    @post("/v1/anonymize")
    async def anonymize(data: AnonymizeRequest) -> AnonymizeResponse:
        anonymized_text, entities = await pipeline.anonymize(
            data.text,
            thread_id=data.thread_id,
        )
        return AnonymizeResponse(
            anonymized_text=anonymized_text,
            entities=_serialize_entities(
                entities,
                pipeline,
                data.thread_id,
            ),
        )

    @post("/v1/deanonymize")
    async def deanonymize(data: DeanonymizeRequest) -> DeanonymizeResponse:
        try:
            original, entities = await pipeline.deanonymize(
                data.text,
                thread_id=data.thread_id,
            )
        except CacheMissError:
            raise NotFoundException("No cached mapping found for this text")

        return DeanonymizeResponse(
            text=original,
            entities=_serialize_entities(
                entities,
                pipeline,
                data.thread_id,
            ),
        )

    @post("/v1/deanonymize/entities")
    async def deanonymize_entities(data: DeanonymizeRequest) -> DeanonymizeEntResponse:
        result = await pipeline.deanonymize_with_ent(
            data.text,
            thread_id=data.thread_id,
        )
        return DeanonymizeEntResponse(text=result)

    return Litestar(
        route_handlers=[
            index,
            health,
            get_config,
            detect,
            override_detect,
            anonymize,
            deanonymize,
            deanonymize_entities,
        ],
        guards=guards,
        lifespan=[lifespan],
        openapi_config=OpenAPIConfig(
            title="piighost-api",
            version="0.1.0",
            description="REST API for piighost PII anonymization inference.",
        ),
    )
