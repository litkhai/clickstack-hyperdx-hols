-- A counter is rate * elapsed since a fixed origin (1793000000), with a slow
-- +-30 percent swing so graphs are not flat. Stateless: a refresh needs no
-- memory of the previous one, the series stays monotonic, and a bucket is
-- the request count times the share of requests at or under its `le`.
-- `time_series` is the samples column of the bundled server's TimeSeries
-- schema (newer servers call it `samples`; HyperDX handles both).
-- Three hours of history ending a minute ago, one sample per series every 15 s.
-- Run before 03-refresh.sql so the two do not overlap.
INSERT INTO promql.metrics (metric_name, tags, time_series)
SELECT
    k.1 AS metric_name,
    mapUpdate(map('service', service), k.2) AS tags,
    arrayMap(t -> (t, floor(k.3 * (r * (toUnixTimestamp(t) - 1793000000)
        - r * 0.3 * 600 * (cos(toUnixTimestamp(t) / 600) - cos(1793000000 / 600))))), ts) AS time_series
FROM
(
    -- (service, requests per second, error share, latency scale in seconds)
    SELECT s.1 AS service, s.2 AS r, s.3 AS err, s.4 AS lat,
        arrayMap(i -> toDateTime64(toUnixTimestamp(toStartOfInterval(now(), INTERVAL 15 SECOND)) - 60 - 3 * 3600 + i * 15, 3), range(721)) AS ts
    FROM (SELECT arrayJoin([('cart', 20., 0.02, 0.08), ('checkout', 8., 0.06, 0.30),
                            ('search', 35., 0.01, 0.05), ('payment', 5., 0.10, 0.15)]) AS s)
)
ARRAY JOIN
    arrayConcat(
        [('http_requests_total', map('status', '200'), 1 - err),
         ('http_requests_total', map('status', '500'), err),
         ('http_errors_total', map(), err)],
        arrayMap(le -> ('http_request_duration_seconds_bucket', map('le', toString(le)), 1 - exp(-le / lat)),
                 [0.05, 0.1, 0.25, 0.5, 1.0]),
        [('http_request_duration_seconds_bucket', map('le', '+Inf'), 1.)]
    ) AS k
;
