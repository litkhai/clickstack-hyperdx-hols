-- Why S1 explains successful transactions only, and compares the purchase MEDIAN: over 4-minute windows of the backfill (no fault on),
-- how often would the old definitions have crossed the negative thresholds? Parameters: {start:DateTime} {end:DateTime} (UTC).
-- external_share: time in HTTPS calls to external hosts / time of the purchase root spans (negative threshold < 0.2);
-- p50 / p95: the purchase root's latency in the window vs the whole range (the kafka-consumer-lag assertion allows +-20 %).
WITH
    roots AS
    (
        SELECT TraceId, intDiv(toUnixTimestamp(Timestamp), 240) AS w, Duration AS root_ns, StatusCode
        FROM otel_traces
        WHERE ServiceName = 'web-bff' AND SpanName = 'POST /checkout' AND ParentSpanId = '' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
    ),
    ext AS
    (
        SELECT TraceId, sum(Duration) AS ext_ns
        FROM otel_traces
        WHERE SpanKind = 'Client' AND SpanAttributes['http.request.method'] != '' AND NOT endsWith(SpanAttributes['server.address'], '.svc.cluster.local')
          AND ServiceName NOT IN ('notification', 'fulfillment') AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + 60
        GROUP BY TraceId
    ),
    per_window AS
    (
        SELECT r.w AS w,
            sum(e.ext_ns) / sum(r.root_ns) AS share_all,
            sumIf(e.ext_ns, r.StatusCode != 'Error') / sumIf(r.root_ns, r.StatusCode != 'Error') AS share_ok,
            quantile(0.5)(r.root_ns) AS p50, quantile(0.95)(r.root_ns) AS p95,
            quantileIf(0.5)(r.root_ns, r.StatusCode != 'Error') AS p50_ok, quantileIf(0.95)(r.root_ns, r.StatusCode != 'Error') AS p95_ok
        FROM roots AS r LEFT JOIN ext AS e USING (TraceId)
        GROUP BY w
    ),
    ref AS (SELECT quantile(0.5)(root_ns) AS p50, quantile(0.95)(root_ns) AS p95 FROM roots WHERE StatusCode != 'Error')
SELECT count() AS windows,
    countIf(share_all >= 0.2) AS windows_over_0_2_all_roots, countIf(share_ok >= 0.2) AS windows_over_0_2_successful_roots_only,
    countIf(abs(p95 - (SELECT p95 FROM ref)) > 0.2 * (SELECT p95 FROM ref)) AS windows_p95_off_by_20pct,
    countIf(abs(p50 - (SELECT p50 FROM ref)) > 0.2 * (SELECT p50 FROM ref)) AS windows_p50_off_by_20pct
FROM per_window;
