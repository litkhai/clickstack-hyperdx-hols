-- S1 check: the consumer-lag metric the Kafka client reports (kafka.consumer.records_lag_max), per consumer service, in a window.
-- Parameters: {start:DateTime} {end:DateTime}
SELECT ServiceName AS service, max(Value) AS max_records_lag
FROM otel_metrics_gauge
WHERE MetricName = 'kafka.consumer.records_lag_max' AND TimeUnix > {start:DateTime} AND TimeUnix <= {end:DateTime}
GROUP BY service
ORDER BY service
