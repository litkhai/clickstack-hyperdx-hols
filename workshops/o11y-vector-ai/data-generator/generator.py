"""
Traffic Generator for E-commerce Sample Application
Generates realistic user journeys with error injection
"""
import os
import sys
import time
import random
import asyncio
import logging
from typing import Dict, List, Any
import httpx
import yaml
from faker import Faker
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
SAMPLE_APP_URL = os.getenv("SAMPLE_APP_URL", "http://localhost:8000")
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.yaml")

fake = Faker()

class TrafficGenerator:
    def __init__(self, config_file: str):
        """Initialize traffic generator"""
        self.config = self._load_config(config_file)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.stats = {
            "requests_sent": 0,
            "requests_succeeded": 0,
            "requests_failed": 0,
            "errors_by_type": {}
        }

    def _load_config(self, config_file: str) -> Dict:
        """Load configuration from YAML file"""
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded configuration from {config_file}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            sys.exit(1)

    def _select_journey(self) -> Dict:
        """Select a user journey based on weights"""
        journeys = self.config['journeys']
        weights = [j['weight'] for j in journeys]
        selected = random.choices(journeys, weights=weights, k=1)[0]
        return selected

    def _get_delay(self, delay_range: List[int]) -> float:
        """Get random delay in seconds"""
        return random.uniform(delay_range[0] / 1000, delay_range[1] / 1000)

    def _prepare_endpoint(self, endpoint: str, user_id: str) -> str:
        """Prepare endpoint with dynamic values"""
        endpoint = endpoint.replace("{user_id}", user_id)
        endpoint = endpoint.replace("{product_id}", str(random.randint(1, 5)))
        return endpoint

    async def execute_journey(self, journey: Dict, user_id: str):
        """Execute a complete user journey"""
        journey_name = journey['name']
        logger.info(f"Starting journey '{journey_name}' for user {user_id}")

        for step in journey['steps']:
            endpoint = self._prepare_endpoint(step['endpoint'], user_id)
            url = f"{SAMPLE_APP_URL}{endpoint}"

            # Delay between steps
            if 'delay_ms' in step:
                delay = self._get_delay(step['delay_ms'])
                await asyncio.sleep(delay)

            # Execute request
            await self._make_request(url, endpoint, user_id)

        logger.info(f"Completed journey '{journey_name}' for user {user_id}")

    async def _make_request(self, url: str, endpoint: str, user_id: str):
        """Make HTTP request with error tracking"""
        self.stats["requests_sent"] += 1

        try:
            # Determine HTTP method and prepare request
            if endpoint.startswith("/cart/add") or endpoint.startswith("/checkout") or endpoint.startswith("/payment"):
                # POST requests
                if "/cart/add" in endpoint:
                    data = {
                        "user_id": user_id,
                        "product_id": random.randint(1, 5),
                        "quantity": random.randint(1, 3)
                    }
                    response = await self.client.post(url, params=data)
                elif "/checkout" in endpoint:
                    data = {"user_id": user_id}
                    response = await self.client.post(url, params=data)
                elif "/payment" in endpoint:
                    data = {
                        "order_id": random.randint(1, 100),
                        "payment_method": random.choice(["credit_card", "paypal", "bank_transfer"])
                    }
                    response = await self.client.post(url, params=data)
            else:
                # GET requests
                response = await self.client.get(url)

            # Track response
            if response.status_code < 400:
                self.stats["requests_succeeded"] += 1
                logger.debug(f"✓ {endpoint} - {response.status_code}")
            else:
                self.stats["requests_failed"] += 1
                error_type = f"{response.status_code}"
                self.stats["errors_by_type"][error_type] = self.stats["errors_by_type"].get(error_type, 0) + 1
                logger.warning(f"✗ {endpoint} - {response.status_code}: {response.text[:100]}")

        except httpx.TimeoutException:
            self.stats["requests_failed"] += 1
            self.stats["errors_by_type"]["timeout"] = self.stats["errors_by_type"].get("timeout", 0) + 1
            logger.warning(f"✗ {endpoint} - Timeout")
        except Exception as e:
            self.stats["requests_failed"] += 1
            self.stats["errors_by_type"]["exception"] = self.stats["errors_by_type"].get("exception", 0) + 1
            logger.error(f"✗ {endpoint} - Exception: {e}")

    async def generate_traffic(self):
        """Main traffic generation loop"""
        logger.info("Starting traffic generation...")
        logger.info(f"Target: {SAMPLE_APP_URL}")
        logger.info(f"Rate: {self.config['generation']['rate_per_minute']} requests/minute")

        rate_per_minute = self.config['generation']['rate_per_minute']
        interval = 60.0 / rate_per_minute

        duration_minutes = self.config['generation']['duration_minutes']
        start_time = time.time()

        iteration = 0
        while True:
            iteration += 1

            # Check duration limit
            if duration_minutes > 0:
                elapsed_minutes = (time.time() - start_time) / 60
                if elapsed_minutes >= duration_minutes:
                    logger.info(f"Reached duration limit: {duration_minutes} minutes")
                    break

            # Generate user ID
            user_id = f"user_{fake.uuid4()[:8]}"

            # Select and execute journey
            journey = self._select_journey()
            await self.execute_journey(journey, user_id)

            # Print stats every 50 iterations
            if iteration % 50 == 0:
                self._print_stats()

            # Wait for next iteration
            await asyncio.sleep(interval)

    def _print_stats(self):
        """Print current statistics"""
        total = self.stats["requests_sent"]
        succeeded = self.stats["requests_succeeded"]
        failed = self.stats["requests_failed"]
        success_rate = (succeeded / total * 100) if total > 0 else 0

        logger.info("=" * 60)
        logger.info(f"Traffic Generator Statistics:")
        logger.info(f"  Total Requests: {total}")
        logger.info(f"  Succeeded: {succeeded}")
        logger.info(f"  Failed: {failed}")
        logger.info(f"  Success Rate: {success_rate:.2f}%")

        if self.stats["errors_by_type"]:
            logger.info(f"  Errors by Type:")
            for error_type, count in sorted(self.stats["errors_by_type"].items(), key=lambda x: x[1], reverse=True):
                logger.info(f"    - {error_type}: {count}")

        logger.info("=" * 60)

    async def close(self):
        """Cleanup resources"""
        await self.client.aclose()
        self._print_stats()
        logger.info("Traffic generator stopped")

async def main():
    """Main entry point"""
    generator = TrafficGenerator(CONFIG_FILE)

    try:
        # Wait for sample app to be ready
        logger.info("Waiting for sample app to be ready...")
        max_retries = 30
        for i in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{SAMPLE_APP_URL}/health", timeout=5.0)
                    if response.status_code == 200:
                        logger.info("Sample app is ready!")
                        break
            except Exception:
                pass

            if i == max_retries - 1:
                logger.error("Sample app is not responding. Exiting.")
                sys.exit(1)

            logger.info(f"Waiting for sample app... ({i+1}/{max_retries})")
            await asyncio.sleep(2)

        # Start traffic generation
        await generator.generate_traffic()

    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await generator.close()

if __name__ == "__main__":
    asyncio.run(main())
