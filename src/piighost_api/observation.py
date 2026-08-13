"""OpenTelemetry observation setup for piighost-api.

piighost v2 is OpenTelemetry-native: the pipeline emits per-stage spans through
``piighost.observation.get_tracer()``, which resolves the globally configured
OpenTelemetry tracer. Observation is therefore a deployment concern, driven by
the standard ``OTEL_*`` environment variables, and any OTLP-compatible backend
(Langfuse, Opik, Phoenix, Jaeger, ...) can receive the traces.

``configure_observation`` wires an OTLP span exporter into the global tracer
provider when an endpoint is configured, so the server exports piighost's spans
out of the box. When no endpoint is set it is a no-op and the spans stay
in-process.
"""

import logging
import os

logger = logging.getLogger(__name__)

# The standard OpenTelemetry variables that opt export in. The traces-specific
# one wins over the generic one, matching the OTel SDK's own precedence.
_OTLP_ENDPOINT_VARS = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


def otlp_endpoint() -> str | None:
    """Return the configured OTLP traces endpoint, or None when unset."""
    for var in _OTLP_ENDPOINT_VARS:
        value = os.getenv(var)
        if value:
            return value
    return None


def configure_observation() -> bool:
    """Install an OTLP span exporter on the global tracer provider.

    Reads the standard OTEL_* environment variables. When an OTLP endpoint is
    configured it builds an OpenTelemetry TracerProvider with a batching OTLP
    exporter and registers it globally, so piighost's per-stage spans are
    exported to the configured backend.

    Returns:
        True when export was configured, False when it stayed a no-op because no
        OTLP endpoint is set.

    Raises:
        ImportError: When an endpoint is set but the OpenTelemetry SDK is not
            installed (install the piighost-api[observation] extra).
    """
    endpoint = otlp_endpoint()
    if endpoint is None:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise ImportError(
            "Observation export requires the OpenTelemetry SDK. "
            "Install it with: pip install piighost-api[observation]"
        ) from exc

    service_name = os.getenv("OTEL_SERVICE_NAME", "piighost-api")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    # OTLPSpanExporter reads the endpoint and headers from the OTEL_* env vars.
    exporter = OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    logger.info("OpenTelemetry span export enabled -> %s", endpoint)
    return True
