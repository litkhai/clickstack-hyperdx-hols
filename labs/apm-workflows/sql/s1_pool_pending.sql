-- S1 check: the HikariCP pending-requests metric per pod in a window (data points whose interval ends in the window).
-- Parameters: {start:DateTime} {end:DateTime}
SELECT ResourceAttributes['k8s.pod.name'] AS pod, max(Value) AS max_pending, sum(Value) AS sum_pending
FROM otel_metrics_sum
WHERE MetricName = 'db.client.connections.pending_requests' AND ServiceName = 'shop'
  AND TimeUnix > {start:DateTime} AND TimeUnix <= {end:DateTime}
GROUP BY pod
ORDER BY pod
