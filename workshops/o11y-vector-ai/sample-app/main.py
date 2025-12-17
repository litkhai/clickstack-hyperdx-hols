"""
E-commerce Sample Application with OpenTelemetry
"""
import os
import sys
import logging
import time
import random
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import OpenTelemetry configuration
from otel_config import setup_otel, instrument_fastapi, get_tracer

# Setup OpenTelemetry before creating FastAPI app
setup_otel()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the application"""
    logger.info("Starting E-commerce Sample Application...")
    yield
    logger.info("Shutting down E-commerce Sample Application...")

# Create FastAPI app
app = FastAPI(
    title="E-commerce API",
    description="Sample E-commerce API for O11y Vector AI Demo",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument FastAPI with OpenTelemetry
instrument_fastapi(app)

# Get tracer for manual instrumentation
tracer = get_tracer()

# In-memory data store (for demo purposes)
products_db = [
    {"id": 1, "name": "Laptop Pro", "price": 1299.99, "stock": 50},
    {"id": 2, "name": "Wireless Mouse", "price": 29.99, "stock": 200},
    {"id": 3, "name": "Mechanical Keyboard", "price": 89.99, "stock": 150},
    {"id": 4, "name": "USB-C Hub", "price": 49.99, "stock": 100},
    {"id": 5, "name": "Monitor 27\"", "price": 349.99, "stock": 75},
]

carts_db = {}
orders_db = []

# ===================================================================
# Root Endpoint
# ===================================================================
@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "healthy", "service": "ecommerce-api", "version": "1.0.0"}

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

# ===================================================================
# Products Endpoints
# ===================================================================
@app.get("/products")
async def get_products(request: Request):
    """Get all products"""
    with tracer.start_as_current_span("get_products") as span:
        span.set_attribute("product.count", len(products_db))

        # Simulate occasional database timeout
        if random.random() < 0.05:  # 5% chance
            logger.error("Database timeout error in get_products")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "DatabaseTimeout")
            time.sleep(2)
            raise HTTPException(status_code=504, detail="Database timeout")

        logger.info(f"Fetched {len(products_db)} products")
        return {"products": products_db}

@app.get("/products/{product_id}")
async def get_product(product_id: int):
    """Get product by ID"""
    with tracer.start_as_current_span("get_product") as span:
        span.set_attribute("product.id", product_id)

        product = next((p for p in products_db if p["id"] == product_id), None)

        if not product:
            logger.warning(f"Product not found: {product_id}")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "NotFound")
            raise HTTPException(status_code=404, detail="Product not found")

        logger.info(f"Fetched product: {product_id}")
        return product

# ===================================================================
# Cart Endpoints
# ===================================================================
@app.post("/cart/add")
async def add_to_cart(user_id: str, product_id: int, quantity: int = 1):
    """Add item to cart"""
    with tracer.start_as_current_span("add_to_cart") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("product.id", product_id)
        span.set_attribute("quantity", quantity)

        product = next((p for p in products_db if p["id"] == product_id), None)

        if not product:
            logger.error(f"Product not found: {product_id}")
            raise HTTPException(status_code=404, detail="Product not found")

        # Check stock
        if product["stock"] < quantity:
            logger.warning(f"Insufficient stock for product {product_id}: requested={quantity}, available={product['stock']}")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "InsufficientStock")
            raise HTTPException(status_code=400, detail="Insufficient stock")

        # Initialize cart if not exists
        if user_id not in carts_db:
            carts_db[user_id] = []

        # Add to cart
        cart_item = {"product_id": product_id, "quantity": quantity, "price": product["price"]}
        carts_db[user_id].append(cart_item)

        logger.info(f"Added to cart: user={user_id}, product={product_id}, quantity={quantity}")
        return {"message": "Added to cart", "cart": carts_db[user_id]}

@app.get("/cart/{user_id}")
async def get_cart(user_id: str):
    """Get user's cart"""
    with tracer.start_as_current_span("get_cart") as span:
        span.set_attribute("user.id", user_id)

        cart = carts_db.get(user_id, [])
        span.set_attribute("cart.item_count", len(cart))

        logger.info(f"Fetched cart for user: {user_id}")
        return {"cart": cart}

# ===================================================================
# Checkout Endpoint
# ===================================================================
@app.post("/checkout")
async def checkout(user_id: str):
    """Process checkout"""
    with tracer.start_as_current_span("checkout") as span:
        span.set_attribute("user.id", user_id)

        cart = carts_db.get(user_id, [])

        if not cart:
            logger.warning(f"Empty cart for user: {user_id}")
            raise HTTPException(status_code=400, detail="Cart is empty")

        span.set_attribute("cart.item_count", len(cart))

        # Calculate total
        total = sum(item["price"] * item["quantity"] for item in cart)
        span.set_attribute("order.total", total)

        # Simulate payment gateway error (10% chance)
        if random.random() < 0.10:
            logger.error(f"Payment gateway error for user: {user_id}")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "PaymentGatewayError")
            raise HTTPException(status_code=502, detail="Payment gateway error")

        # Process order
        order_id = len(orders_db) + 1
        order = {
            "order_id": order_id,
            "user_id": user_id,
            "items": cart,
            "total": total,
            "status": "completed"
        }
        orders_db.append(order)

        # Clear cart
        carts_db[user_id] = []

        logger.info(f"Checkout completed: user={user_id}, order_id={order_id}, total={total}")
        return {"message": "Checkout successful", "order": order}

# ===================================================================
# Payment Endpoint
# ===================================================================
@app.post("/payment")
async def process_payment(order_id: int, payment_method: str):
    """Process payment"""
    with tracer.start_as_current_span("process_payment") as span:
        span.set_attribute("order.id", order_id)
        span.set_attribute("payment.method", payment_method)

        # Simulate payment processing delay
        time.sleep(random.uniform(0.1, 0.5))

        # Simulate timeout (5% chance)
        if random.random() < 0.05:
            logger.error(f"Payment timeout for order: {order_id}")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "PaymentTimeout")
            time.sleep(5)
            raise HTTPException(status_code=504, detail="Payment processing timeout")

        # Simulate duplicate payment error (3% chance)
        if random.random() < 0.03:
            logger.error(f"Duplicate payment detected for order: {order_id}")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "DuplicatePayment")
            raise HTTPException(status_code=409, detail="Duplicate payment detected")

        logger.info(f"Payment processed: order_id={order_id}, method={payment_method}")
        return {"message": "Payment successful", "order_id": order_id, "status": "paid"}

# ===================================================================
# Inventory Endpoint
# ===================================================================
@app.get("/inventory/check")
async def check_inventory(product_id: int):
    """Check inventory status"""
    with tracer.start_as_current_span("check_inventory") as span:
        span.set_attribute("product.id", product_id)

        # Simulate DB connection pool exhaustion (2% chance)
        if random.random() < 0.02:
            logger.error("Database connection pool exhausted")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "DBConnectionPoolExhausted")
            time.sleep(10)
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")

        product = next((p for p in products_db if p["id"] == product_id), None)

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        logger.info(f"Inventory checked: product_id={product_id}, stock={product['stock']}")
        return {"product_id": product_id, "stock": product["stock"], "available": product["stock"] > 0}

# ===================================================================
# Exception Handlers
# ===================================================================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )

# ===================================================================
# Main
# ===================================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")

    logger.info(f"Starting server on {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
