-- --force: remove the rows tagged apm.backfill = true (live rows are never tagged).
DELETE FROM otel_traces WHERE ResourceAttributes['apm.backfill'] = 'true';
DELETE FROM otel_logs WHERE ResourceAttributes['apm.backfill'] = 'true';
DELETE FROM otel_metrics_histogram WHERE ResourceAttributes['apm.backfill'] = 'true';
DELETE FROM otel_metrics_sum WHERE ResourceAttributes['apm.backfill'] = 'true';
DELETE FROM otel_metrics_gauge WHERE ResourceAttributes['apm.backfill'] = 'true';
