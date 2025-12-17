"""
Embedding Batch Processor
Periodically processes logs and traces from ClickHouse and generates embeddings
"""
import os
import sys
import time
import logging
from typing import List, Dict, Any
from datetime import datetime
import clickhouse_connect
from openai import OpenAI
from dotenv import load_dotenv
import yaml
import schedule
from tenacity import retry, stop_after_attempt, wait_exponential

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EmbeddingProcessor:
    def __init__(self, config_file: str):
        """Initialize embedding processor"""
        self.config = self._load_config(config_file)
        self._init_clickhouse()
        self._init_openai()
        self.stats = {
            "logs_processed": 0,
            "traces_processed": 0,
            "errors": 0
        }

    def _load_config(self, config_file: str) -> Dict:
        """Load configuration"""
        try:
            with open(config_file, 'r') as f:
                config_str = f.read()

            # Replace environment variables
            for key, value in os.environ.items():
                config_str = config_str.replace(f"${{{key}}}", value)

            config = yaml.safe_load(config_str)
            logger.info(f"Loaded configuration from {config_file}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            sys.exit(1)

    def _init_clickhouse(self):
        """Initialize ClickHouse connection"""
        try:
            self.ch_client = clickhouse_connect.get_client(
                host=self.config['clickhouse']['host'],
                port=int(self.config['clickhouse']['port']),
                username=self.config['clickhouse']['user'],
                password=self.config['clickhouse']['password'],
                database=self.config['clickhouse']['database'],
                secure=self.config['clickhouse'].get('secure', True)
            )
            logger.info("Connected to ClickHouse")
        except Exception as e:
            logger.error(f"Failed to connect to ClickHouse: {e}")
            sys.exit(1)

    def _init_openai(self):
        """Initialize OpenAI client"""
        try:
            self.openai_client = OpenAI(api_key=self.config['openai']['api_key'])
            self.embedding_model = self.config['openai']['model']
            logger.info(f"Initialized OpenAI client with model: {self.embedding_model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI: {e}")
            sys.exit(1)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using OpenAI API"""
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=texts
            )
            embeddings = [item.embedding for item in response.data]
            return embeddings
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise

    def process_logs(self):
        """Process logs that need embeddings"""
        logger.info("Processing logs for embeddings...")

        try:
            # Query logs that need embeddings
            severity_filter = ", ".join([f"'{s}'" for s in self.config['filters']['severity_levels']])
            lookback_hours = self.config['schedule']['lookback_hours']
            max_logs = self.config['filters']['max_logs_per_run']

            query = f"""
            SELECT
                Timestamp,
                TraceId,
                SpanId,
                SeverityText,
                ServiceName,
                Body
            FROM o11y.otel_logs
            WHERE SeverityText IN ({severity_filter})
                AND Timestamp > now() - INTERVAL {lookback_hours} HOUR
                AND (Timestamp, TraceId, SpanId) NOT IN (
                    SELECT Timestamp, TraceId, SpanId
                    FROM o11y.logs_with_embeddings
                )
            ORDER BY Timestamp DESC
            LIMIT {max_logs}
            """

            result = self.ch_client.query(query)
            logs = result.result_rows

            if not logs:
                logger.info("No new logs to process")
                return

            logger.info(f"Found {len(logs)} logs to process")

            # Batch processing
            batch_size = self.config['openai']['batch_size']
            for i in range(0, len(logs), batch_size):
                batch = logs[i:i + batch_size]
                self._process_log_batch(batch)

            logger.info(f"Successfully processed {len(logs)} logs")
            self.stats["logs_processed"] += len(logs)

        except Exception as e:
            logger.error(f"Error processing logs: {e}")
            self.stats["errors"] += 1

    def _process_log_batch(self, batch: List[tuple]):
        """Process a batch of logs"""
        try:
            # Extract text for embedding
            texts = [log[5] for log in batch]  # Body field

            # Generate embeddings
            embeddings = self._generate_embeddings(texts)

            # Prepare data for insertion
            data = []
            for log, embedding in zip(batch, embeddings):
                data.append({
                    'Timestamp': log[0],
                    'TraceId': log[1],
                    'SpanId': log[2],
                    'SeverityText': log[3],
                    'ServiceName': log[4],
                    'Body': log[5],
                    'embedding': embedding,
                    'embedding_model': self.embedding_model
                })

            # Insert into ClickHouse
            self.ch_client.insert(
                'o11y.logs_with_embeddings',
                data,
                column_names=[
                    'Timestamp', 'TraceId', 'SpanId', 'SeverityText',
                    'ServiceName', 'Body', 'embedding', 'embedding_model'
                ]
            )

            logger.info(f"Inserted {len(data)} log embeddings")

        except Exception as e:
            logger.error(f"Error processing log batch: {e}")
            raise

    def process_traces(self):
        """Process traces that need embeddings"""
        logger.info("Processing traces for embeddings...")

        try:
            lookback_hours = self.config['schedule']['lookback_hours']
            max_traces = self.config['filters']['max_traces_per_run']

            # Get traces that need embeddings
            query = f"""
            WITH trace_summary AS (
                SELECT
                    TraceId,
                    min(Timestamp) AS Timestamp,
                    any(ServiceName) AS ServiceName,
                    groupArray(SpanName) AS span_names,
                    sum(Duration) AS total_duration,
                    count() AS span_count,
                    countIf(StatusCode = 'ERROR') AS error_count,
                    groupArray(StatusMessage) AS error_messages
                FROM o11y.otel_traces
                WHERE Timestamp > now() - INTERVAL {lookback_hours} HOUR
                GROUP BY TraceId
            )
            SELECT
                ts.TraceId,
                ts.Timestamp,
                ts.ServiceName,
                arrayStringConcat(ts.span_names, ' -> ') AS span_sequence,
                ts.total_duration,
                ts.span_count,
                ts.error_count,
                arrayFilter(x -> x != '', ts.error_messages) AS error_messages
            FROM trace_summary ts
            WHERE ts.TraceId NOT IN (
                SELECT TraceId
                FROM o11y.traces_with_embeddings
            )
            LIMIT {max_traces}
            """

            result = self.ch_client.query(query)
            traces = result.result_rows

            if not traces:
                logger.info("No new traces to process")
                return

            logger.info(f"Found {len(traces)} traces to process")

            # Batch processing
            batch_size = self.config['openai']['batch_size']
            for i in range(0, len(traces), batch_size):
                batch = traces[i:i + batch_size]
                self._process_trace_batch(batch)

            logger.info(f"Successfully processed {len(traces)} traces")
            self.stats["traces_processed"] += len(traces)

        except Exception as e:
            logger.error(f"Error processing traces: {e}")
            self.stats["errors"] += 1

    def _process_trace_batch(self, batch: List[tuple]):
        """Process a batch of traces"""
        try:
            # Create text representation for embedding
            texts = []
            for trace in batch:
                span_sequence = trace[3]
                error_messages = trace[7] if trace[7] else []
                text = f"Trace: {span_sequence}"
                if error_messages:
                    text += f" | Errors: {', '.join(error_messages)}"
                texts.append(text)

            # Generate embeddings
            embeddings = self._generate_embeddings(texts)

            # Prepare data for insertion
            data = []
            for trace, embedding in zip(batch, embeddings):
                data.append({
                    'TraceId': trace[0],
                    'Timestamp': trace[1],
                    'ServiceName': trace[2],
                    'span_sequence': trace[3],
                    'total_duration': trace[4],
                    'span_count': trace[5],
                    'error_count': trace[6],
                    'error_messages': trace[7] if trace[7] else [],
                    'embedding': embedding,
                    'embedding_model': self.embedding_model
                })

            # Insert into ClickHouse
            self.ch_client.insert(
                'o11y.traces_with_embeddings',
                data,
                column_names=[
                    'TraceId', 'Timestamp', 'ServiceName', 'span_sequence',
                    'total_duration', 'span_count', 'error_count', 'error_messages',
                    'embedding', 'embedding_model'
                ]
            )

            logger.info(f"Inserted {len(data)} trace embeddings")

        except Exception as e:
            logger.error(f"Error processing trace batch: {e}")
            raise

    def run_once(self):
        """Run one iteration of processing"""
        logger.info("=" * 60)
        logger.info("Starting embedding processing cycle...")

        try:
            self.process_logs()
            self.process_traces()
        except Exception as e:
            logger.error(f"Error in processing cycle: {e}")

        logger.info(f"Cycle complete. Stats: {self.stats}")
        logger.info("=" * 60)

    def run_continuously(self):
        """Run processing continuously"""
        interval = self.config['schedule']['interval_seconds']
        logger.info(f"Starting continuous processing (interval: {interval}s)")

        schedule.every(interval).seconds.do(self.run_once)

        # Run once immediately
        self.run_once()

        # Then run on schedule
        while True:
            schedule.run_pending()
            time.sleep(1)

def main():
    """Main entry point"""
    config_file = os.getenv("CONFIG_FILE", "config.yaml")

    logger.info("Starting Embedding Batch Processor...")

    processor = EmbeddingProcessor(config_file)

    try:
        processor.run_continuously()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
