-- labs/korean-log-search — what bin/run.sh measured, read back from the server's own logs.
-- Run after `bin/run.sh searches`; system.query_log is flushed by the script first.

-- R1. Index size per table (compressed), next to the data it indexes.
SELECT
    table,
    formatReadableSize(sum(data_compressed_bytes))              AS data,
    formatReadableSize(sum(secondary_indices_compressed_bytes)) AS body_index,
    round(sum(secondary_indices_compressed_bytes) / sum(data_compressed_bytes), 2) AS index_to_data
FROM system.parts
WHERE database = 'korean_log_search' AND active AND table IN ('logs_word', 'logs_ngram')
GROUP BY table
ORDER BY table
FORMAT PrettyCompactMonoBlock;

-- R2. CPU of copying the same rows into each table (the copy without an index is the baseline).
SELECT
    replaceOne(log_comment, 'korean_log_search:load:', '')                        AS copy_into,
    round(max(ProfileEvents['OSCPUVirtualTimeMicroseconds']) / 1e6, 2)            AS cpu_s,
    max(query_duration_ms)                                                         AS wall_ms
FROM clusterAllReplicas(default, system.query_log)
WHERE log_comment LIKE 'korean_log_search:load:%' AND type = 'QueryFinish' AND event_date >= today() - 1
GROUP BY copy_into
ORDER BY copy_into
FORMAT PrettyCompactMonoBlock;

-- R3. The UI-shaped searches over the last 15 minutes: granules read and rows returned.
-- s0 is the time range alone: the granules any search in that range starts from.
SELECT
    replaceOne(log_comment, 'korean_log_search:search:', '')   AS search,
    max(ProfileEvents['SelectedMarks'])                        AS granules_read,
    max(result_rows)                                           AS result_rows,
    max(query_duration_ms)                                     AS ms
FROM clusterAllReplicas(default, system.query_log)
WHERE log_comment LIKE 'korean_log_search:search:%' AND type = 'QueryFinish' AND event_date >= today() - 1
  AND event_time >= (SELECT max(event_time) - 600 FROM clusterAllReplicas(default, system.query_log)
                     WHERE log_comment LIKE 'korean_log_search:search:%' AND event_date >= today() - 1)
GROUP BY search
ORDER BY search
FORMAT PrettyCompactMonoBlock;
