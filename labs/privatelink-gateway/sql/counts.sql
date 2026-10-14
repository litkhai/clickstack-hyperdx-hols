-- Rows per signal for one run. Templated: {{DB}} (database) and {{RUN_ID}} (verify.run_id).
-- traces: n = distinct traces, rows = spans. telemetrygen emits 2 spans per trace (parent + one child)
-- even with --child-spans 0, observed 2026-10-14 with telemetrygen v0.155.0.
SELECT 'traces' AS signal, uniqExact(TraceId) AS n, count() AS row_count FROM {{DB}}.otel_traces WHERE ResourceAttributes['verify.run_id'] = '{{RUN_ID}}'
UNION ALL
SELECT 'logs', count(), count() FROM {{DB}}.otel_logs WHERE ResourceAttributes['verify.run_id'] = '{{RUN_ID}}'
UNION ALL
SELECT 'metrics_gauge', count(), count() FROM {{DB}}.otel_metrics_gauge WHERE ResourceAttributes['verify.run_id'] = '{{RUN_ID}}'
UNION ALL
SELECT 'metrics_sum', count(), count() FROM {{DB}}.otel_metrics_sum WHERE ResourceAttributes['verify.run_id'] = '{{RUN_ID}}'
