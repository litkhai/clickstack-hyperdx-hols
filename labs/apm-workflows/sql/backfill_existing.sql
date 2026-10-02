-- Rows already tagged apm.backfill = true, per table.
SELECT 'otel_traces' AS tbl, count() AS n FROM otel_traces WHERE ResourceAttributes['apm.backfill'] = 'true'
UNION ALL SELECT 'otel_logs', count() FROM otel_logs WHERE ResourceAttributes['apm.backfill'] = 'true'
UNION ALL SELECT 'otel_metrics_histogram', count() FROM otel_metrics_histogram WHERE ResourceAttributes['apm.backfill'] = 'true'
UNION ALL SELECT 'otel_metrics_sum', count() FROM otel_metrics_sum WHERE ResourceAttributes['apm.backfill'] = 'true'
UNION ALL SELECT 'otel_metrics_gauge', count() FROM otel_metrics_gauge WHERE ResourceAttributes['apm.backfill'] = 'true';
