-- S1 replay: the rows a block of past minutes consists of, per table. Parameters: {s:DateTime} {e:DateTime}.
-- traces: spans of the requests of those minutes (the minute is encoded in the TraceId; late Kafka consumers are included);
-- logs: by their own timestamp; metrics: by the request minute (a point's TimeUnix is the end of its minute).
SELECT * FROM
(
SELECT 'otel_traces' AS tbl, count() AS n FROM otel_traces
 WHERE Timestamp >= {s:DateTime} AND Timestamp < {e:DateTime} + INTERVAL 20 MINUTE
   AND reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8)))) >= toUInt32({s:DateTime})
   AND reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8)))) < toUInt32({e:DateTime})
UNION ALL SELECT 'otel_logs', count() FROM otel_logs WHERE Timestamp >= {s:DateTime} AND Timestamp < {e:DateTime}
UNION ALL SELECT 'otel_metrics_histogram', count() FROM otel_metrics_histogram WHERE TimeUnix > {s:DateTime} AND TimeUnix <= {e:DateTime}
UNION ALL SELECT 'otel_metrics_sum', count() FROM otel_metrics_sum WHERE TimeUnix > {s:DateTime} AND TimeUnix <= {e:DateTime}
UNION ALL SELECT 'otel_metrics_gauge', count() FROM otel_metrics_gauge WHERE TimeUnix > {s:DateTime} AND TimeUnix <= {e:DateTime}
)
ORDER BY tbl
