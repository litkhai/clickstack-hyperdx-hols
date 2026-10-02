-- V2: rows per signal and service, backfill vs live, with the per-day counts (UTC days, oldest first).
SELECT signal, service,
    sumIf(n, source = 'backfill') AS backfill_rows, sumIf(n, source = 'live') AS live_rows,
    sumMap(map(toString(day), n)) AS rows_per_day
FROM
(
    SELECT 'traces' AS signal, ServiceName AS service, toDate(Timestamp) AS day, if(ResourceAttributes['apm.backfill'] = 'true', 'backfill', 'live') AS source, count() AS n
    FROM otel_traces GROUP BY service, day, source
    UNION ALL
    SELECT 'logs', ServiceName, toDate(Timestamp), if(ResourceAttributes['apm.backfill'] = 'true', 'backfill', 'live'), count() FROM otel_logs GROUP BY ServiceName, toDate(Timestamp), 4
    UNION ALL
    SELECT 'metrics', ServiceName, toDate(TimeUnix), if(ResourceAttributes['apm.backfill'] = 'true', 'backfill', 'live'), count()
    FROM (SELECT ServiceName, TimeUnix, ResourceAttributes FROM otel_metrics_histogram
          UNION ALL SELECT ServiceName, TimeUnix, ResourceAttributes FROM otel_metrics_sum
          UNION ALL SELECT ServiceName, TimeUnix, ResourceAttributes FROM otel_metrics_gauge)
    GROUP BY ServiceName, toDate(TimeUnix), 4
)
GROUP BY signal, service
ORDER BY signal, service;

-- TTL of every table of the lab (the OTel tables and fault_events carry TTL 30 days; deploy_events, lab_settings and the topology are configuration)
SELECT name, extract(create_table_query, 'TTL [^S]+') AS ttl
FROM system.tables WHERE database = 'apm_workflows' AND engine LIKE '%MergeTree'
ORDER BY name;
