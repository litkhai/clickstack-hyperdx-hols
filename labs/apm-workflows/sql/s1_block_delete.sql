-- S1 replay: delete the generated rows of a block of past minutes (lightweight DELETE, this database only).
-- Same selection as s1_block_counts.sql. Parameters: {s:DateTime} {e:DateTime}.
DELETE FROM otel_traces
 WHERE Timestamp >= {s:DateTime} AND Timestamp < {e:DateTime} + INTERVAL 20 MINUTE
   AND reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8)))) >= toUInt32({s:DateTime})
   AND reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8)))) < toUInt32({e:DateTime});
DELETE FROM otel_logs WHERE Timestamp >= {s:DateTime} AND Timestamp < {e:DateTime};
DELETE FROM otel_metrics_histogram WHERE TimeUnix > {s:DateTime} AND TimeUnix <= {e:DateTime};
DELETE FROM otel_metrics_sum WHERE TimeUnix > {s:DateTime} AND TimeUnix <= {e:DateTime};
DELETE FROM otel_metrics_gauge WHERE TimeUnix > {s:DateTime} AND TimeUnix <= {e:DateTime};
