"""Refuse to restore through a placeholder factory that cannot be reversed.

The proxies and the restoring routes map each placeholder back to one value. That
needs a factory whose tokens preserve a recognizable identity: two distinct values
get two distinct tokens, and a token can be found again in text. piighost models
this with phantom tags, checked statically, but this server builds its pipeline
from a configuration file, so no type checker ever sees the factory. The tag is
checked here at runtime instead.

piighost's factories return tokens that are instances of their tag, so asking the
factory for one token and checking its type reads the tag the factory declares.
A redact factory gives every value the same <<REDACT>>, a label factory every
person the same <<PERSON>>, a mask factory a fragment two values can share. None
of them is a PreservesRecognizableIdentity, and restoring through them puts one
value in place of every token that shares it.
"""

from piighost.components.placeholder import PreservesRecognizableIdentity
from piighost.models import Detection, Entity, Span
from piighost.pipeline import ThreadAnonymizationPipeline

_PROBE = Entity(
    detections=(Detection(span=Span(0, 5), text="probe", label="PII", confidence=1.0),)
)
"""A synthetic entity the factory is asked to name, to read the tag of its token."""


class NonReversibleFactoryError(RuntimeError):
    """Raised when a restoring route is served over a non-reversible factory."""


def is_reversible(pipeline: ThreadAnonymizationPipeline) -> bool:
    """Whether the pipeline's tokens preserve a recognizable identity."""
    factory = pipeline.anonymizer.factory
    token = factory.create([_PROBE])[_PROBE]
    return isinstance(token, PreservesRecognizableIdentity)


def require_reversible(pipeline: ThreadAnonymizationPipeline) -> None:
    """Raise NonReversibleFactoryError unless the pipeline can restore unambiguously.

    Called before serving any route that restores placeholders, the proxies,
    /v1/deanonymize and /v1/threads/{id}/tokens.
    """
    if is_reversible(pipeline):
        return
    name = type(pipeline.anonymizer.factory).__name__
    raise NonReversibleFactoryError(
        f"The configured placeholder factory {name} cannot be reversed. It can give "
        "several values the same token, such as <<REDACT>> or <<PERSON>>, so "
        "restoring a reply would put one value in place of every token that shares "
        "it, a secret included. The proxies, /v1/deanonymize and "
        "/v1/threads/{id}/tokens restore replies, so the server refuses to serve "
        "them. Set [anonymizer.placeholder] type to label_counter or label_hash, or "
        "set PIIGHOST_ONE_WAY=true to serve only the routes that never restore."
    )
