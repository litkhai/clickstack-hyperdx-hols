-- --force: remove the rows tagged game.backfill = true (live rows are never tagged).
DELETE FROM otel_traces WHERE ResourceAttributes['game.backfill'] = 'true';
DELETE FROM otel_logs WHERE ResourceAttributes['game.backfill'] = 'true';
DELETE FROM otel_metrics_histogram WHERE ResourceAttributes['game.backfill'] = 'true';
DELETE FROM otel_metrics_sum WHERE ResourceAttributes['game.backfill'] = 'true';
DELETE FROM otel_metrics_gauge WHERE ResourceAttributes['game.backfill'] = 'true';
