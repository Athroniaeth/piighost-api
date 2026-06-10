"""Litestar application with PII anonymization API routes."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version
from pathlib import Path

import msgspec
from keyshield import ApiKeyService
from keyshield.hasher.argon2 import Argon2ApiKeyHasher
from keyshield.repositories.in_memory import InMemoryApiKeyRepository
from litestar import Litestar, delete, get, post, put
from litestar.exceptions import NotFoundException
from litestar.openapi import OpenAPIConfig

from piighost.config import load_pipeline
from piighost.exceptions import CacheMissError
from piighost.models import Detection, Entity, Span
from piighost.pipeline.thread import ThreadAnonymizationPipeline

from piighost_api.auth import create_auth_guard
from piighost_api.observation import load_observation_service

logger = logging.getLogger(__name__)

API_VERSION = _pkg_version("piighost-api")


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


class DetectorLabelsSchema(msgspec.Struct):
    name: str | None
    type: str
    labels: list[str]


class PipelineMetaSchema(msgspec.Struct):
    name: str | None
    schema_version: int


class LabelsResponse(msgspec.Struct):
    pipeline: PipelineMetaSchema
    detectors: list[DetectorLabelsSchema]


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
    tokens = pipeline.get_resolved_tokens(thread_id)
    token_lookup = {ent.canonical_key: tok for ent, tok in tokens.items()}

    result: list[EntitySchema] = []
    for entity in entities:
        placeholder = token_lookup.get(entity.canonical_key, "")
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


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------


def create_app(config_path: Path) -> Litestar:
    """Create and configure the Litestar application.

    Args:
        config_path: Path to a piighost TOML configuration file.

    Returns:
        A fully configured ``Litestar`` instance.
    """
    pipeline, manifest = load_pipeline(config_path)

    observation = load_observation_service()
    if observation is not None:
        pipeline.observation = observation
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
            logger.info("API keys loaded, auth enabled")
        except Exception as exc:
            if os.getenv("PIIGHOST_ALLOW_ANONYMOUS", "").strip().lower() not in (
                "1",
                "true",
                "yes",
                "on",
            ):
                raise RuntimeError(
                    "No valid API keys found and PIIGHOST_ALLOW_ANONYMOUS is not "
                    "set. Refusing to serve PII endpoints unauthenticated; define "
                    "API_KEY_<name> entries or explicitly opt in to anonymous "
                    "mode with PIIGHOST_ALLOW_ANONYMOUS=true."
                ) from exc
            logger.warning("Anonymous mode enabled (%s), auth disabled", exc)
        logger.info(
            "Pipeline ready: %s (%d detector(s))",
            manifest.name or "<unnamed>",
            len(manifest.detectors),
        )
        yield

    # ------------------------------------------------------------------
    # Route handlers (closures over pipeline)
    # ------------------------------------------------------------------

    @get("/", exclude_from_auth=True)
    async def index() -> IndexResponse:
        return IndexResponse(
            name="piighost-api",
            version=API_VERSION,
            docs="/schema/swagger",
        )

    @get("/health", exclude_from_auth=True)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            detector=", ".join(d.type for d in manifest.detectors) or "none",
        )

    @get("/v1/labels", exclude_from_auth=True)
    async def labels() -> LabelsResponse:
        return LabelsResponse(
            pipeline=PipelineMetaSchema(
                name=manifest.name,
                schema_version=manifest.schema_version,
            ),
            detectors=[
                DetectorLabelsSchema(name=d.name, type=d.type, labels=d.labels)
                for d in manifest.detectors
            ],
        )

    @post("/v1/detect")
    async def detect(data: DetectRequest) -> DetectResponse:
        entities = await pipeline.detect_entities(data.text, thread_id=data.thread_id)
        return DetectResponse(entities=_serialize_entities_plain(entities))

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

    @delete("/v1/threads/{thread_id:str}")
    async def forget_thread(thread_id: str) -> None:
        """Erase every trace of a conversation: memory and cached mappings.

        Backed by ``ThreadAnonymizationPipeline.forget_thread`` (right to
        be forgotten). Idempotent.
        """
        await pipeline.forget_thread(thread_id)

    max_body = int(os.getenv("PIIGHOST_MAX_BODY_BYTES", "1000000"))

    middleware = []
    rate_limit_env = os.getenv("PIIGHOST_RATE_LIMIT", "")
    if rate_limit_env:
        # Format: "<unit>:<count>", e.g. "minute:300".
        from litestar.middleware.rate_limit import RateLimitConfig

        unit, _, count = rate_limit_env.partition(":")
        middleware.append(
            RateLimitConfig(
                rate_limit=(unit, int(count)),  # pyrefly: ignore[bad-argument-type]
                # exclude takes regex patterns; anchor them so "/" does not
                # match every path.
                exclude=["^/health$", "^/$"],
            ).middleware
        )

    return Litestar(
        route_handlers=[
            index,
            health,
            labels,
            detect,
            override_detect,
            anonymize,
            deanonymize,
            deanonymize_entities,
            forget_thread,
        ],
        guards=guards,
        lifespan=[lifespan],
        request_max_body_size=max_body,
        middleware=middleware,
        openapi_config=OpenAPIConfig(
            title="piighost-api",
            version=API_VERSION,
            description="REST API for piighost PII anonymization inference.",
        ),
    )
