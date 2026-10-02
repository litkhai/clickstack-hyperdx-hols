-- S1 check: how far the live generators have got (all must pass the end of the last window).
SELECT
    (SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_traces WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 2 DAY) AS traces_next,
    (SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_logs WHERE Timestamp >= now() - INTERVAL 2 DAY) AS logs_next,
    (SELECT toStartOfMinute(maxOrNull(TimeUnix)) FROM otel_metrics_histogram WHERE TimeUnix >= now() - INTERVAL 2 DAY) AS histogram_next,
    (SELECT toStartOfMinute(maxOrNull(TimeUnix)) FROM otel_metrics_sum WHERE TimeUnix >= now() - INTERVAL 2 DAY) AS sum_next,
    (SELECT toStartOfMinute(maxOrNull(TimeUnix)) FROM otel_metrics_gauge WHERE TimeUnix >= now() - INTERVAL 2 DAY) AS gauge_next,
    toStartOfMinute(now()) AS now_minute
