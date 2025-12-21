import os
import json
import time
import clickhouse_connect
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ClickHouse connection
CH_HOST = os.getenv("CH_HOST")
CH_USER = os.getenv("CH_USER", "default")
CH_PASSWORD = os.getenv("CH_PASSWORD")
CH_DATABASE = os.getenv("CH_DATABASE", "o11y")

TRACES_FILE = "/var/log/otel/otel-traces.json"
LOGS_FILE = "/var/log/otel/otel-logs.json"

def connect_clickhouse():
    """Connect to ClickHouse Cloud"""
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST,
            user=CH_USER,
            password=CH_PASSWORD,
            secure=True,
            port=8443
        )
        logger.info(f"Connected to ClickHouse: {CH_HOST}")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to ClickHouse: {e}")
        return None

def parse_trace_span(span, resource_attrs, scope_name):
    """Parse a single span from OTLP format"""

    # Convert nanoseconds to DateTime64(9)
    timestamp = datetime.fromtimestamp(int(span.get('startTimeUnixNano', 0)) / 1e9)

    # Extract attributes
    span_attrs = {}
    for attr in span.get('attributes', []):
        key = attr.get('key', '')
        value = attr.get('value', {})
        if 'stringValue' in value:
            span_attrs[key] = value['stringValue']
        elif 'intValue' in value:
            span_attrs[key] = str(value['intValue'])
        elif 'boolValue' in value:
            span_attrs[key] = str(value['boolValue'])

    # Calculate duration
    start_time = int(span.get('startTimeUnixNano', 0))
    end_time = int(span.get('endTimeUnixNano', 0))
    duration = end_time - start_time

    # Get status
    status = span.get('status', {})
    status_code = 'OK'
    if status.get('code') == 2:
        status_code = 'ERROR'

    return {
        'Timestamp': timestamp,
        'TraceId': span.get('traceId', ''),
        'SpanId': span.get('spanId', ''),
        'ParentSpanId': span.get('parentSpanId', ''),
        'SpanName': span.get('name', ''),
        'SpanKind': str(span.get('kind', 0)),
        'ServiceName': resource_attrs.get('service.name', 'unknown'),
        'Duration': duration,
        'StatusCode': status_code,
        'StatusMessage': status.get('message', ''),
        'SpanAttributes': span_attrs,
        'ResourceAttributes': resource_attrs
    }

def load_traces(client):
    """Load traces from file and insert to ClickHouse"""
    try:
        if not os.path.exists(TRACES_FILE):
            logger.debug(f"Traces file not found: {TRACES_FILE}")
            return 0

        count = 0
        with open(TRACES_FILE, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())

                    for resource_span in data.get('resourceSpans', []):
                        # Extract resource attributes
                        resource_attrs = {}
                        for attr in resource_span.get('resource', {}).get('attributes', []):
                            key = attr.get('key', '')
                            value = attr.get('value', {})
                            if 'stringValue' in value:
                                resource_attrs[key] = value['stringValue']

                        # Process each scope
                        for scope_span in resource_span.get('scopeSpans', []):
                            scope_name = scope_span.get('scope', {}).get('name', '')

                            # Process each span
                            for span in scope_span.get('spans', []):
                                parsed_span = parse_trace_span(span, resource_attrs, scope_name)

                                # Insert to ClickHouse
                                client.insert(
                                    f'{CH_DATABASE}.otel_traces',
                                    [list(parsed_span.values())],
                                    column_names=list(parsed_span.keys())
                                )
                                count += 1

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.error(f"Error processing trace line: {e}")
                    continue

        if count > 0:
            logger.info(f"Loaded {count} traces to ClickHouse")

        return count

    except Exception as e:
        logger.error(f"Error loading traces: {e}")
        return 0

def main():
    """Main loader loop"""
    logger.info("Starting ClickHouse Loader...")
    logger.info(f"ClickHouse Host: {CH_HOST}")
    logger.info(f"Database: {CH_DATABASE}")

    # Wait for files to be created
    time.sleep(10)

    client = connect_clickhouse()
    if not client:
        logger.error("Cannot connect to ClickHouse. Exiting.")
        return

    last_position = 0

    while True:
        try:
            # Load traces
            traces_loaded = load_traces(client)

            if traces_loaded > 0:
                # Clear the file after successful load to avoid duplicates
                # Note: In production, use file rotation or offset tracking
                pass

            # Wait before next iteration
            time.sleep(10)

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
