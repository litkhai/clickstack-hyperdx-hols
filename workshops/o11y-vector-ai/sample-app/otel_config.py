"""
OpenTelemetry configuration for FastAPI application
"""
import os
import logging
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Service configuration
SERVICE_NAME_VALUE = os.getenv("OTEL_SERVICE_NAME", "ecommerce-api")
SERVICE_VERSION_VALUE = os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
OTEL_COLLECTOR_ENDPOINT = os.getenv("OTEL_COLLECTOR_ENDPOINT", "http://localhost:4317")

def setup_otel():
    """
    Setup OpenTelemetry instrumentation
    """
    # Create resource
    resource = Resource(attributes={
        SERVICE_NAME: SERVICE_NAME_VALUE,
        SERVICE_VERSION: SERVICE_VERSION_VALUE,
        "deployment.environment": os.getenv("ENVIRONMENT", "production"),
    })

    # Setup Tracing
    tracer_provider = TracerProvider(resource=resource)

    # Configure OTLP exporter for traces
    otlp_trace_exporter = OTLPSpanExporter(
        endpoint=OTEL_COLLECTOR_ENDPOINT,
        insecure=True,
    )

    tracer_provider.add_span_processor(
        BatchSpanProcessor(otlp_trace_exporter)
    )

    trace.set_tracer_provider(tracer_provider)

    # Setup Metrics
    otlp_metric_exporter = OTLPMetricExporter(
        endpoint=OTEL_COLLECTOR_ENDPOINT,
        insecure=True,
    )

    metric_reader = PeriodicExportingMetricReader(otlp_metric_exporter, export_interval_millis=10000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Setup Logging instrumentation
    LoggingInstrumentor().instrument(set_logging_format=True)

    # Auto-instrument requests library
    RequestsInstrumentor().instrument()

    logging.info(f"OpenTelemetry initialized for service: {SERVICE_NAME_VALUE}")
    logging.info(f"OTLP Collector endpoint: {OTEL_COLLECTOR_ENDPOINT}")

def instrument_fastapi(app):
    """
    Instrument FastAPI application
    """
    FastAPIInstrumentor.instrument_app(app)
    logging.info("FastAPI instrumented with OpenTelemetry")

def get_tracer():
    """Get the tracer for manual instrumentation"""
    return trace.get_tracer(__name__)

def get_meter():
    """Get the meter for manual metrics"""
    return metrics.get_meter(__name__)
