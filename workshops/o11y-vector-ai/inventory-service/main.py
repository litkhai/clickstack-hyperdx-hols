from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
import random
import time
import logging

# Setup OpenTelemetry
resource = Resource.create({
    "service.name": "inventory-service",
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

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="Inventory Service")

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

# In-memory inventory
inventory = {
    1: {"name": "Laptop", "stock": 50},
    2: {"name": "Mouse", "stock": 100},
    3: {"name": "Keyboard", "stock": 75},
    4: {"name": "Monitor", "stock": 30},
    5: {"name": "Headphones", "stock": 60},
}

@app.get("/health")
def health():
    return {"status": "healthy", "service": "inventory-service"}

@app.get("/products")
def get_products():
    """Get all products with inventory"""
    with tracer.start_as_current_span("fetch_all_products") as span:
        span.set_attribute("product.count", len(inventory))

        # Simulate database query
        with tracer.start_as_current_span("database_query") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.statement", "SELECT * FROM products")
            time.sleep(random.uniform(0.05, 0.15))

        logger.info(f"Fetched {len(inventory)} products")
        return {"products": inventory}

@app.get("/products/{product_id}")
def get_product(product_id: int):
    """Get product details"""
    with tracer.start_as_current_span("fetch_product_detail") as span:
        span.set_attribute("product.id", product_id)

        # Simulate database query
        with tracer.start_as_current_span("database_query") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.statement", f"SELECT * FROM products WHERE id = {product_id}")
            time.sleep(random.uniform(0.02, 0.08))

        if product_id not in inventory:
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Product not found"))
            logger.warning(f"Product {product_id} not found")
            raise HTTPException(status_code=404, detail="Product not found")

        logger.info(f"Fetched product {product_id}")
        return {"id": product_id, **inventory[product_id]}

@app.post("/check-availability")
def check_availability(product_id: int):
    """Check if product is available"""
    with tracer.start_as_current_span("check_product_availability") as span:
        span.set_attribute("product.id", product_id)

        # Simulate cache check
        with tracer.start_as_current_span("cache_lookup") as cache_span:
            cache_span.set_attribute("cache.system", "redis")
            time.sleep(random.uniform(0.01, 0.03))
            cache_span.set_attribute("cache.hit", False)

        # Simulate database query
        with tracer.start_as_current_span("database_query") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.statement", f"SELECT stock FROM products WHERE id = {product_id}")
            time.sleep(random.uniform(0.02, 0.06))

        if product_id not in inventory:
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Product not found"))
            raise HTTPException(status_code=404, detail="Product not found")

        available = inventory[product_id]["stock"] > 0
        span.set_attribute("product.available", available)
        span.set_attribute("product.stock", inventory[product_id]["stock"])

        logger.info(f"Product {product_id} availability: {available}")
        return {
            "product_id": product_id,
            "available": available,
            "stock": inventory[product_id]["stock"]
        }

@app.post("/reserve")
def reserve_stock(product_id: int, quantity: int = 1):
    """Reserve stock for order"""
    with tracer.start_as_current_span("reserve_inventory") as span:
        span.set_attribute("product.id", product_id)
        span.set_attribute("quantity", quantity)

        # Simulate database transaction
        with tracer.start_as_current_span("database_transaction") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.operation", "UPDATE")
            time.sleep(random.uniform(0.05, 0.15))

            if product_id not in inventory:
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Product not found"))
                raise HTTPException(status_code=404, detail="Product not found")

            if inventory[product_id]["stock"] < quantity:
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Insufficient stock"))
                logger.warning(f"Insufficient stock for product {product_id}")
                raise HTTPException(status_code=400, detail="Insufficient stock")

            inventory[product_id]["stock"] -= quantity

        logger.info(f"Reserved {quantity} units of product {product_id}")
        return {
            "success": True,
            "product_id": product_id,
            "reserved": quantity,
            "remaining_stock": inventory[product_id]["stock"]
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
