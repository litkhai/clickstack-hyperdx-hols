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
    "service.name": "payment-service",
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
app = FastAPI(title="Payment Service")

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

@app.get("/health")
def health():
    return {"status": "healthy", "service": "payment-service"}

@app.post("/process")
def process_payment(amount: float, session_id: str = None):
    """Process payment and return success/failure"""
    with tracer.start_as_current_span("process_payment_transaction") as span:
        span.set_attribute("payment.amount", amount)
        if session_id:
            span.set_attribute("session.id", session_id)

        logger.info(f"Processing payment: ${amount}")

        # Simulate payment gateway call
        with tracer.start_as_current_span("payment_gateway_call") as gateway_span:
            gateway_span.set_attribute("gateway.provider", "stripe")
            time.sleep(random.uniform(0.3, 0.8))

            # Simulate 5% failure rate
            if random.random() < 0.05:
                gateway_span.set_status(trace.Status(trace.StatusCode.ERROR, "Gateway timeout"))
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Payment failed"))
                logger.error(f"Payment gateway timeout for amount ${amount}")
                raise HTTPException(status_code=500, detail="Payment gateway timeout")

        # Simulate fraud check
        with tracer.start_as_current_span("fraud_check") as fraud_span:
            fraud_span.set_attribute("fraud.check_type", "ml_model")
            time.sleep(random.uniform(0.1, 0.3))

            # Simulate 2% fraud detection
            if random.random() < 0.02:
                fraud_span.set_attribute("fraud.detected", True)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Fraud detected"))
                logger.warning(f"Fraud detected for amount ${amount}")
                raise HTTPException(status_code=403, detail="Fraud detected")

            fraud_span.set_attribute("fraud.detected", False)

        transaction_id = f"txn_{random.randint(100000, 999999)}"
        logger.info(f"Payment successful: {transaction_id}")

        return {
            "success": True,
            "transaction_id": transaction_id,
            "amount": amount
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
