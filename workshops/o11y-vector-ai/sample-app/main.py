from fastapi import FastAPI, Request, Query, HTTPException
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource

# Logging imports
import logging
from opentelemetry import _logs
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

# Metrics imports
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

import random
import time
import os
import uuid
from datetime import datetime
import requests

# Setup OpenTelemetry
resource = Resource.create({
    "service.name": "sample-ecommerce-app",
    "service.version": "1.0.0"
})

trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)

# OTLP Trace Exporter (HTTP)
otlp_trace_exporter = OTLPSpanExporter(
    endpoint="http://otel-collector:4318/v1/traces"
)

span_processor = BatchSpanProcessor(otlp_trace_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

# Setup Logging with OpenTelemetry
logger_provider = LoggerProvider(resource=resource)
_logs.set_logger_provider(logger_provider)

# OTLP Log Exporter (HTTP)
otlp_log_exporter = OTLPLogExporter(
    endpoint="http://otel-collector:4318/v1/logs"
)

log_processor = BatchLogRecordProcessor(otlp_log_exporter)
logger_provider.add_log_record_processor(log_processor)

# Attach OTEL handler to root logger
handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)

# Create a logger instance
logger = logging.getLogger(__name__)

# Setup Metrics
metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint="http://otel-collector:4318/v1/metrics"),
    export_interval_millis=5000
)
meter_provider = MeterProvider(metric_readers=[metric_reader], resource=resource)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(__name__)

# Create metrics
request_counter = meter.create_counter(
    "http_requests_total",
    description="Total HTTP requests",
    unit="1"
)
response_time_histogram = meter.create_histogram(
    "http_response_time_ms",
    description="HTTP response time in milliseconds",
    unit="ms"
)
active_sessions_gauge = meter.create_up_down_counter(
    "active_sessions",
    description="Number of active sessions",
    unit="1"
)
cart_items_gauge = meter.create_up_down_counter(
    "cart_items_total",
    description="Total items in cart",
    unit="1"
)

# Session storage (in-memory for demo)
sessions = {}

# FastAPI app
app = FastAPI(title="E-commerce Demo")

# Session middleware
@app.middleware("http")
async def session_middleware(request: Request, call_next):
    # Get or create session
    session_id = request.headers.get("X-Session-ID") or str(uuid.uuid4())
    request.state.session_id = session_id

    # Track session
    if session_id not in sessions:
        sessions[session_id] = {
            "created_at": datetime.now(),
            "requests": 0,
            "cart_items": 0
        }
        active_sessions_gauge.add(1, {"status": "new"})

        # Log session start to otel_sessions
        logger.info(f"Session started", extra={
            "session.id": session_id,
            "session.event": "start",
            "timestamp": datetime.now().isoformat()
        })

    sessions[session_id]["requests"] += 1

    # Process request
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000

    # Record metrics
    request_counter.add(1, {
        "method": request.method,
        "endpoint": request.url.path,
        "status": response.status_code
    })
    response_time_histogram.record(duration_ms, {
        "method": request.method,
        "endpoint": request.url.path
    })

    # Add session ID to response
    response.headers["X-Session-ID"] = session_id

    return response

# Instrument FastAPI and Requests
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()

# Service URLs
INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8001")

@app.get("/")
def read_root(request: Request):
    session_id = request.state.session_id
    logger.info("Root endpoint accessed", extra={
        "session.id": session_id,
        "endpoint": "/"
    })
    return {"message": "E-commerce API", "session_id": session_id}

@app.get("/products")
def get_products(request: Request):
    session_id = request.state.session_id
    with tracer.start_as_current_span("get_products") as span:
        span.set_attribute("session.id", session_id)

        logger.info("Fetching product list from inventory service", extra={
            "session.id": session_id
        })

        # Call inventory service
        try:
            response = requests.get(f"{INVENTORY_SERVICE_URL}/products", timeout=5)
            response.raise_for_status()
            products_data = response.json()
            span.set_attribute("product.count", len(products_data.get("products", {})))

            logger.info("Successfully fetched products from inventory service", extra={
                "session.id": session_id,
                "product.count": len(products_data.get("products", {}))
            })

            return {"products": products_data.get("products", {}), "session_id": session_id}
        except Exception as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            logger.error(f"Failed to fetch products from inventory service: {e}", extra={
                "session.id": session_id
            })
            raise HTTPException(status_code=503, detail="Inventory service unavailable")

@app.get("/products/{product_id}")
def get_product(product_id: int, request: Request):
    session_id = request.state.session_id
    with tracer.start_as_current_span("get_product_detail") as span:
        span.set_attribute("product.id", product_id)
        span.set_attribute("session.id", session_id)
        logger.info(f"Fetching product detail", extra={
            "product.id": product_id,
            "session.id": session_id
        })

        # Simulate 404
        if product_id > 100:
            span.set_attribute("error", True)
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Product not found"))
            logger.error(f"Product not found: {product_id}", extra={
                "product.id": product_id,
                "session.id": session_id
            })
            return {"error": "Product not found"}, 404

        return {"id": product_id, "name": f"Product {product_id}", "price": 99.99, "session_id": session_id}

@app.post("/cart/add")
def add_to_cart(request: Request, product_id: int = Query(...)):
    session_id = request.state.session_id
    with tracer.start_as_current_span("add_to_cart") as span:
        span.set_attribute("product.id", product_id)
        span.set_attribute("session.id", session_id)

        logger.info("Adding product to cart, checking availability", extra={
            "product.id": product_id,
            "session.id": session_id
        })

        # Check inventory availability
        try:
            availability_response = requests.post(
                f"{INVENTORY_SERVICE_URL}/check-availability",
                params={"product_id": product_id},
                timeout=5
            )
            availability_response.raise_for_status()
            availability_data = availability_response.json()

            if not availability_data.get("available"):
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Product unavailable"))
                logger.warning(f"Product {product_id} is out of stock", extra={
                    "product.id": product_id,
                    "session.id": session_id
                })
                raise HTTPException(status_code=400, detail="Product out of stock")

            # Update cart items
            sessions[session_id]["cart_items"] += 1
            cart_items_gauge.add(1, {"session.id": session_id})

            logger.info("Product added to cart successfully", extra={
                "product.id": product_id,
                "session.id": session_id,
                "cart_items": sessions[session_id]["cart_items"]
            })

            return {
                "message": "Added to cart",
                "product_id": product_id,
                "cart_items": sessions[session_id]["cart_items"],
                "session_id": session_id
            }
        except requests.exceptions.RequestException as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            logger.error(f"Failed to check inventory: {e}", extra={
                "session.id": session_id
            })
            raise HTTPException(status_code=503, detail="Inventory service unavailable")

@app.post("/checkout")
def checkout(request: Request):
    session_id = request.state.session_id
    with tracer.start_as_current_span("checkout") as span:
        span.set_attribute("session.id", session_id)
        cart_items = sessions[session_id]["cart_items"]
        span.set_attribute("cart.items", cart_items)

        logger.info("Starting checkout process", extra={
            "session.id": session_id,
            "cart_items": cart_items
        })

        if cart_items == 0:
            raise HTTPException(status_code=400, detail="Cart is empty")

        # Calculate total amount (simplified)
        total_amount = cart_items * 99.99

        # Reserve inventory
        try:
            with tracer.start_as_current_span("reserve_inventory") as reserve_span:
                reserve_span.set_attribute("cart.items", cart_items)
                # Simplified: reserve product_id=1 for demo
                reserve_response = requests.post(
                    f"{INVENTORY_SERVICE_URL}/reserve",
                    params={"product_id": 1, "quantity": cart_items},
                    timeout=5
                )
                reserve_response.raise_for_status()
                logger.info("Inventory reserved successfully", extra={"session.id": session_id})
        except requests.exceptions.RequestException as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            logger.error(f"Failed to reserve inventory: {e}", extra={"session.id": session_id})
            raise HTTPException(status_code=503, detail="Failed to reserve inventory")

        # Process payment via payment service
        try:
            with tracer.start_as_current_span("process_payment_request") as payment_span:
                payment_span.set_attribute("payment.amount", total_amount)
                payment_response = requests.post(
                    f"{PAYMENT_SERVICE_URL}/process",
                    params={"amount": total_amount, "session_id": session_id},
                    timeout=10
                )
                payment_response.raise_for_status()
                payment_data = payment_response.json()

                transaction_id = payment_data.get("transaction_id")
                span.set_attribute("payment.transaction_id", transaction_id)

                logger.info("Payment processed successfully", extra={
                    "session.id": session_id,
                    "transaction_id": transaction_id
                })
        except requests.exceptions.RequestException as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            logger.error(f"Payment failed: {e}", extra={"session.id": session_id})
            raise HTTPException(status_code=503, detail="Payment service unavailable")

        order_id = random.randint(1000, 9999)

        # Reset cart items after successful checkout
        cart_items_count = sessions[session_id]["cart_items"]
        if cart_items_count > 0:
            cart_items_gauge.add(-cart_items_count, {"session.id": session_id})
            sessions[session_id]["cart_items"] = 0

        logger.info("Order completed successfully", extra={
            "order.id": order_id,
            "session.id": session_id,
            "session.event": "checkout",
            "transaction_id": transaction_id
        })
        return {
            "message": "Order completed",
            "order_id": order_id,
            "transaction_id": transaction_id,
            "session_id": session_id
        }

@app.get("/health")
def health(request: Request):
    session_id = request.state.session_id
    return {"status": "healthy", "session_id": session_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
