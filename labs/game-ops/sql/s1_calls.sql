-- S1 check: latency of the called side, measured from the spans themselves (not from root spans, which only the edge has).
--   server: SERVER spans of a service in the window; external: CLIENT spans to a host (server.address).
-- Parameters: {start:DateTime} {end:DateTime} {service:String} {address:String}
SELECT
    (SELECT round(quantile(0.5)(Duration) / 1e6, 1) FROM otel_traces WHERE ServiceName = {service:String} AND SpanKind = 'Server'
      AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}) AS server_p50_ms,
    (SELECT count() FROM otel_traces WHERE ServiceName = {service:String} AND SpanKind = 'Server'
      AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}) AS server_spans,
    (SELECT round(quantile(0.5)(Duration) / 1e6, 1) FROM otel_traces WHERE SpanKind = 'Client' AND SpanAttributes['server.address'] = {address:String}
      AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}) AS external_p50_ms
