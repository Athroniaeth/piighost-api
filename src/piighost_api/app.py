"""Litestar application with PII anonymization API routes.

The routes serve the contract the piighost ``PIIGhostClient`` calls, so a remote
``ThreadAnonymizationPipeline`` drives this server exactly like a local pipeline:

* ``POST /v1/anonymize`` -> ``{anonymized_text, entities}``
* ``POST /v1/anonymize/corrected`` -> ``{anonymized_text}``
* ``POST /v1/deanonymize`` -> ``{text}``
* ``DELETE /v1/threads/{id}`` -> ``{messages, detections}``

plus a detection preview and health/labels endpoints for human consumers.
"""

import logging
import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version
from pathlib import Path

import msgspec
from keyshield import ApiKeyService
from keyshield.hasher.argon2 import Argon2ApiKeyHasher
from keyshield.repositories.in_memory import InMemoryApiKeyRepository
from litestar import Litestar, delete, get, post
from litestar.openapi import OpenAPIConfig

from piighost.components.detector.patterns import (
    EU_PATTERNS,
    FR_PATTERNS,
    GENERIC_PATTERNS,
    US_PATTERNS,
)
from piighost.config import load_config, load_thread_pipeline
from piighost.conversation_memory import MessageRole
from piighost.models import Detection, Entity, Span
from piighost.pipeline.thread import ThreadAnonymizationPipeline

from piighost_api.auth import AuthState, create_auth_guard
from piighost_api.observation import configure_observation
from piighost_api.routes.openai import build_openai_router

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


class CorrectedDetectionSchema(msgspec.Struct):
    """A detection as produced by ``piighost.models.Detection.to_dict()``."""

    text: str
    label: str
    start: int
    end: int
    confidence: float


class DetectRequest(msgspec.Struct):
    text: str
    thread_id: str = "default"


class AnonymizeRequest(msgspec.Struct):
    text: str
    thread_id: str = "default"
    role: str = "user"


class AnonymizeCorrectedRequest(msgspec.Struct):
    text: str
    detections: list[CorrectedDetectionSchema]
    thread_id: str = "default"


class DeanonymizeRequest(msgspec.Struct):
    text: str
    thread_id: str = "default"


class AnonymizeResponse(msgspec.Struct):
    anonymized_text: str
    entities: list[EntitySchema]


class AnonymizeCorrectedResponse(msgspec.Struct):
    anonymized_text: str


class DeanonymizeResponse(msgspec.Struct):
    text: str


class DetectResponse(msgspec.Struct):
    entities: list[EntitySchema]


class ForgetResponse(msgspec.Struct):
    messages: int
    detections: int


class ThreadTokensResponse(msgspec.Struct):
    tokens: dict[str, str]


class IndexResponse(msgspec.Struct):
    name: str
    version: str
    docs: str


class HealthResponse(msgspec.Struct):
    status: str
    detector: str


class LabelsResponse(msgspec.Struct):
    name: str | None
    detector: str
    labels: list[str]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


_CATALOGS: dict[str, dict[str, str]] = {
    "generic": GENERIC_PATTERNS,
    "us": US_PATTERNS,
    "eu": EU_PATTERNS,
    "fr": FR_PATTERNS,
}


def _detector_labels(config: object) -> set[str]:
    """Collect the label vocabulary a detector config can emit.

    Walks the detector config tree so the /v1/labels route can offer the full
    set of labels a human corrector may reassign. Regex labels are the pattern
    keys, inline ones plus the merged catalogs; NER and LLM labels are the
    declared labels, or the external keys when a raw-to-canonical mapping is
    given; a composite or chunked detector contributes its children's labels. An
    unknown detector type contributes nothing.
    """
    detector_type = getattr(config, "type", None)

    if detector_type == "regex":
        labels = set(getattr(config, "patterns", {}))
        for catalog in getattr(config, "catalogs", []):
            labels |= set(_CATALOGS.get(catalog, {}))
        return labels

    if detector_type == "composite":
        labels = set()
        for child in getattr(config, "detectors", []):
            labels |= _detector_labels(child)
        return labels

    if detector_type == "chunked":
        return _detector_labels(getattr(config, "detector", None))

    if detector_type == "exact":
        return set(getattr(config, "values", {}).values())

    if detector_type in ("gliner2", "spacy", "transformers", "llm"):
        labels = getattr(config, "labels", None)
        if labels is None:
            return set()
        if isinstance(labels, dict):
            return set(labels.keys())
        return set(labels)

    return set()


def _detection_schema(detection: Detection) -> DetectionSchema:
    """Serialize a piighost Detection to the API wire shape."""
    return DetectionSchema(
        text=detection.text,
        label=detection.label,
        start_pos=detection.span.start,
        end_pos=detection.span.end,
        confidence=detection.confidence,
    )


def _serialize_tokens(tokens: Mapping[Entity, str]) -> list[EntitySchema]:
    """Serialize an anonymization's entity-to-token mapping."""
    return [
        EntitySchema(
            label=entity.label,
            placeholder=str(token),
            detections=[_detection_schema(d) for d in entity.detections],
        )
        for entity, token in tokens.items()
    ]


def _serialize_entities_plain(entities: list[Entity]) -> list[EntitySchema]:
    """Serialize entities without placeholder tokens (for detection preview)."""
    return [
        EntitySchema(
            label=entity.label,
            placeholder="",
            detections=[_detection_schema(d) for d in entity.detections],
        )
        for entity in entities
    ]


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------


def create_app(config_path: Path) -> Litestar:
    """Create and configure the Litestar application.

    Args:
        config_path: Path to a piighost TOML or JSON configuration file.

    Returns:
        A fully configured ``Litestar`` instance.
    """
    config = load_config(config_path)
    pipeline: ThreadAnonymizationPipeline = load_thread_pipeline(config_path)
    detector_type = config.detector.type
    openai_router = build_openai_router(pipeline)

    if configure_observation():
        logger.info("Observation export enabled")

    pepper = os.getenv("SECRET_PEPPER")
    hasher = Argon2ApiKeyHasher(pepper=pepper)
    repo = InMemoryApiKeyRepository()
    svc_api_keys = ApiKeyService(repo=repo, hasher=hasher)

    # The guard is registered once at construction time (see the Litestar(...)
    # call below) and reads this mutable dict. The lifespan flips
    # ``enabled`` to True after keys load successfully; because the guard
    # captured the dict by reference, that flip is visible. Appending a guard
    # to the ``guards`` list inside the lifespan would NOT work: Litestar
    # copies and freezes per-handler guards during registration.
    auth_state: AuthState = {"enabled": False, "svc": svc_api_keys}

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncGenerator[None]:
        try:
            await svc_api_keys.load_dotenv()
            auth_state["enabled"] = True
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
            "Pipeline ready: %s (detector: %s)",
            config.name or "<unnamed>",
            detector_type,
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
        return HealthResponse(status="ok", detector=detector_type)

    @get("/v1/labels", exclude_from_auth=True)
    async def labels() -> LabelsResponse:
        vocabulary = sorted(_detector_labels(config.detector))
        return LabelsResponse(
            name=config.name,
            detector=detector_type,
            labels=vocabulary,
        )

    @post("/v1/detect")
    async def detect(data: DetectRequest) -> DetectResponse:
        detections = await pipeline.detector.detect(data.text)
        entities = pipeline.linker.link(detections)
        return DetectResponse(entities=_serialize_entities_plain(entities))

    @post("/v1/anonymize")
    async def anonymize(data: AnonymizeRequest) -> AnonymizeResponse:
        result = await pipeline.anonymize(
            data.text,
            data.thread_id,
            role=MessageRole(data.role),
        )
        return AnonymizeResponse(
            anonymized_text=result.text,
            entities=_serialize_tokens(result.tokens),
        )

    @post("/v1/anonymize/corrected")
    async def anonymize_corrected(
        data: AnonymizeCorrectedRequest,
    ) -> AnonymizeCorrectedResponse:
        detections = [
            Detection(
                span=Span(d.start, d.end),
                text=d.text,
                label=d.label,
                confidence=d.confidence,
            )
            for d in data.detections
        ]
        result = await pipeline.anonymize_corrected(
            data.text,
            data.thread_id,
            detections,
        )
        return AnonymizeCorrectedResponse(anonymized_text=result.text)

    @post("/v1/deanonymize")
    async def deanonymize(data: DeanonymizeRequest) -> DeanonymizeResponse:
        text = await pipeline.deanonymize(data.text, data.thread_id)
        return DeanonymizeResponse(text=text)

    @delete("/v1/threads/{thread_id:str}", status_code=200)
    async def forget_thread(thread_id: str) -> ForgetResponse:
        """Erase every trace of a conversation (right to be forgotten).

        Backed by ``ThreadAnonymizationPipeline.forget_thread``. Idempotent, and
        reports how many messages and detections were dropped.
        """
        forgotten = await pipeline.forget_thread(thread_id)
        return ForgetResponse(
            messages=forgotten.messages,
            detections=forgotten.detections,
        )

    @get("/v1/threads/{thread_id:str}/tokens")
    async def thread_tokens(thread_id: str) -> ThreadTokensResponse:
        """Return the thread's placeholder-to-value map for streaming restoration.

        Backed by ThreadAnonymizationPipeline.thread_token_map: it reads the cache
        and exposes each token with the value it restores to, so a client resolves
        a whole stream once instead of deanonymizing token by token. Auth-gated
        like the other thread routes, since it discloses the thread's real values.
        """
        token_map = await pipeline.thread_token_map(thread_id)
        return ThreadTokensResponse(tokens=token_map)

    max_body = int(os.getenv("PIIGHOST_MAX_BODY_BYTES", "1000000"))

    middleware = []
    rate_limit_env = os.getenv("PIIGHOST_RATE_LIMIT", "")
    if rate_limit_env:
        # Format: "<unit>:<count>", e.g. "minute:300".
        from litestar.middleware.rate_limit import RateLimitConfig

        valid_units = ("second", "minute", "hour", "day")
        unit, sep, count_str = rate_limit_env.partition(":")
        if not sep or unit not in valid_units:
            raise ValueError(
                "Invalid PIIGHOST_RATE_LIMIT "
                f"{rate_limit_env!r}. Expected format '<unit>:<count>' "
                f"where <unit> is one of {valid_units} and <count> is a "
                "positive integer, e.g. 'minute:300'."
            )
        try:
            count = int(count_str)
        except ValueError as exc:
            raise ValueError(
                "Invalid PIIGHOST_RATE_LIMIT "
                f"{rate_limit_env!r}. The <count> must be a positive integer, "
                f"got {count_str!r}. Expected format '<unit>:<count>', "
                "e.g. 'minute:300'."
            ) from exc
        if count <= 0:
            raise ValueError(
                "Invalid PIIGHOST_RATE_LIMIT "
                f"{rate_limit_env!r}. The <count> must be a positive integer, "
                f"got {count}. Expected format '<unit>:<count>', "
                "e.g. 'minute:300'."
            )
        middleware.append(
            RateLimitConfig(
                rate_limit=(unit, count),  # pyrefly: ignore[bad-argument-type]
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
            anonymize,
            anonymize_corrected,
            deanonymize,
            forget_thread,
            thread_tokens,
            openai_router,
        ],
        guards=[create_auth_guard(auth_state)],
        lifespan=[lifespan],
        request_max_body_size=max_body,
        middleware=middleware,
        openapi_config=OpenAPIConfig(
            title="piighost-api",
            version=API_VERSION,
            description="REST API for piighost PII anonymization inference.",
        ),
    )
