"""OpenTelemetry configuration for mio-taskhub."""
import os
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION

logger = logging.getLogger("mio_taskhub.observability.otel")

# Global tracer provider
_tracer_provider: trace.TracerProvider | None = None
_meter_provider = None


def init_otel(service_name: str = "mio-taskhub", service_version: str = "0.2.0") -> None:
    """Initialize OpenTelemetry tracing and metrics."""
    global _tracer_provider, _meter_provider

    # Resource identification
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
    })

    # Tracer provider
    _tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(_tracer_provider)

    # Prometheus metrics reader
    prometheus_reader = PrometheusMetricReader()
    _meter_provider = MeterProvider(resource=resource, metric_readers=[prometheus_reader])
    set_meter_provider(_meter_provider)

    logger.info("OpenTelemetry initialized: service=%s version=%s", service_name, service_version)


def instrument_app(app) -> None:
    """Instrument FastAPI app with OpenTelemetry."""
    FastAPIInstrumentor.instrument_app(app)
    logger.info("FastAPI instrumented")


def instrument_sqlalchemy(engine) -> None:
    """Instrument SQLAlchemy engine."""
    SQLAlchemyInstrumentor().instrument(engine=engine)
    logger.info("SQLAlchemy instrumented")


def instrument_httpx() -> None:
    """Instrument httpx client."""
    HTTPXClientInstrumentor().instrument()
    logger.info("httpx instrumented")


def get_tracer(name: str = "mio_taskhub"):
    """Get a tracer instance."""
    return trace.get_tracer_provider().get_tracer(name)


def get_meter(name: str = "mio_taskhub"):
    """Get a meter instance."""
    return _meter_provider.get_meter(name) if _meter_provider else None


def shutdown_otel() -> None:
    """Shutdown OpenTelemetry providers."""
    global _tracer_provider, _meter_provider
    if _tracer_provider:
        _tracer_provider.shutdown()
    if _meter_provider:
        _meter_provider.shutdown()
    logger.info("OpenTelemetry shutdown complete")