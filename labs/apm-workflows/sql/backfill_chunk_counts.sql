-- Rows of one finished chunk. Parameters: chunk_start, chunk_minutes.
SELECT 'spans' AS signal, count() AS n FROM otel_traces
 WHERE Timestamp >= {chunk_start:DateTime} AND Timestamp < {chunk_start:DateTime} + toIntervalMinute({chunk_minutes:UInt32}) AND ResourceAttributes['apm.backfill'] = 'true'
UNION ALL SELECT 'logs', count() FROM otel_logs
 WHERE Timestamp >= {chunk_start:DateTime} AND Timestamp < {chunk_start:DateTime} + toIntervalMinute({chunk_minutes:UInt32}) AND ResourceAttributes['apm.backfill'] = 'true'
UNION ALL SELECT 'metrics', (SELECT count() FROM otel_metrics_histogram WHERE TimeUnix > {chunk_start:DateTime} AND TimeUnix <= {chunk_start:DateTime} + toIntervalMinute({chunk_minutes:UInt32}) AND ResourceAttributes['apm.backfill'] = 'true')
 + (SELECT count() FROM otel_metrics_sum WHERE TimeUnix > {chunk_start:DateTime} AND TimeUnix <= {chunk_start:DateTime} + toIntervalMinute({chunk_minutes:UInt32}) AND ResourceAttributes['apm.backfill'] = 'true')
 + (SELECT count() FROM otel_metrics_gauge WHERE TimeUnix > {chunk_start:DateTime} AND TimeUnix <= {chunk_start:DateTime} + toIntervalMinute({chunk_minutes:UInt32}) AND ResourceAttributes['apm.backfill'] = 'true');
