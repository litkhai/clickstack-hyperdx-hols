-- Duplicate spans for one run (K3). Templated: {{DB}} and {{RUN_ID}}.
SELECT count() AS row_count, uniqExact(SpanId) AS uniq_spans, count() - uniqExact(SpanId) AS dupes
FROM {{DB}}.otel_traces
WHERE ResourceAttributes['verify.run_id'] = '{{RUN_ID}}'
