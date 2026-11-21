-- Rows already tagged game.backfill = true, per table.
SELECT 'otel_traces' AS tbl, count() AS n FROM otel_traces WHERE ResourceAttributes['game.backfill'] = 'true'
UNION ALL SELECT 'otel_logs', count() FROM otel_logs WHERE ResourceAttributes['game.backfill'] = 'true'
UNION ALL SELECT 'otel_metrics_histogram', count() FROM otel_metrics_histogram WHERE ResourceAttributes['game.backfill'] = 'true'
UNION ALL SELECT 'otel_metrics_sum', count() FROM otel_metrics_sum WHERE ResourceAttributes['game.backfill'] = 'true'
UNION ALL SELECT 'otel_metrics_gauge', count() FROM otel_metrics_gauge WHERE ResourceAttributes['game.backfill'] = 'true';
