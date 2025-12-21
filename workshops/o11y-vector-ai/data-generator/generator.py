import requests
import time
import random
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE_URL = "http://sample-app:8000"

def generate_traffic():
    """Generate realistic e-commerce traffic patterns"""

    endpoints = [
        ("GET", "/products", 0.5),      # 50% of traffic
        ("POST", "/cart/add", 0.3),     # 30% of traffic
        ("POST", "/checkout", 0.2),     # 20% of traffic
    ]

    session_id = None

    while True:
        try:
            # Choose endpoint based on weights
            method, endpoint, _ = random.choices(
                endpoints,
                weights=[e[2] for e in endpoints]
            )[0]

            url = f"{API_BASE_URL}{endpoint}"
            headers = {}

            # Maintain session ID across requests
            if session_id:
                headers["X-Session-ID"] = session_id

            if method == "GET":
                response = requests.get(url, headers=headers, timeout=5)
            else:
                # Prepare POST data
                if endpoint == "/cart/add":
                    # Random product_id between 1 and 10
                    product_id = random.randint(1, 10)
                    data = {"product_id": product_id}
                    full_url = f"{url}?product_id={product_id}"
                    logger.info(f"Requesting: {full_url} with headers: {headers}")
                    response = requests.post(url, params=data, headers=headers, timeout=5)
                else:
                    response = requests.post(url, json={}, headers=headers, timeout=5)

            # Extract session ID from response
            if "X-Session-ID" in response.headers:
                session_id = response.headers["X-Session-ID"]

            # Log with more details
            if response.status_code >= 400:
                logger.warning(f"{method} {endpoint} - Status: {response.status_code} - Session: {session_id} - Response: {response.text[:200]}")
            else:
                logger.info(f"{method} {endpoint} - Status: {response.status_code} - Session: {session_id}")

            # Random delay between requests (1-5 seconds)
            time.sleep(random.uniform(1, 5))

        except Exception as e:
            logger.error(f"Request failed: {e}")
            time.sleep(5)

if __name__ == "__main__":
    logger.info("Starting data generator...")
    logger.info(f"Target API: {API_BASE_URL}")

    # Wait for sample-app to be ready
    time.sleep(10)

    generate_traffic()
