-- labs/sentry-migration: what reached ClickStack, from SQL (not the UI).
-- Run against the lab's ClickHouse:
--   curl -s 'localhost:18123/?user=api&password=api' --data-binary @sql/verify.sql
-- bin/verify.sh sends the statements one at a time.

-- 1. records per client, release and kind
SELECT ServiceName AS client, ResourceAttributes['service.version'] AS release,
       LogAttributes['event.name'] AS kind, count() AS records
FROM default.otel_logs
WHERE LogAttributes['event.name'] LIKE 'sentry.%'
GROUP BY ALL ORDER BY client, release, kind
FORMAT PrettyCompactMonoBlock;

-- 2. crash-free sessions per release, over distinct session ids (an SDK may
--    send the crashed update twice for one session; the Node SDK does)
SELECT ResourceAttributes['service.version'] AS release,
       uniqExact(LogAttributes['sentry.session.id']) AS sessions,
       uniqExactIf(LogAttributes['sentry.session.id'], LogAttributes['sentry.session.status'] = 'crashed') AS crashed,
       round(100 * (1 - crashed / sessions), 2) AS crash_free_pct
FROM default.otel_logs
WHERE LogAttributes['event.name'] = 'sentry.session'
  AND LogAttributes['sentry.session.status'] IN ('exited', 'crashed', 'abnormal')
GROUP BY release ORDER BY release
FORMAT PrettyCompactMonoBlock;

-- 3. error groups: one fingerprint per exception type + in-app frames
SELECT LogAttributes['sentry.fingerprint'] AS fingerprint, any(Body) AS sample,
       LogAttributes['sentry.handled'] AS handled, count() AS events,
       uniqExact(LogAttributes['user.id']) AS players,
       groupUniqArray(ResourceAttributes['service.version']) AS releases
FROM default.otel_logs
WHERE LogAttributes['event.name'] = 'sentry.error'
GROUP BY fingerprint, handled ORDER BY events DESC
FORMAT PrettyCompactMonoBlock;

-- 4. native minidumps: a receipt per upload, the file kept by the bridge
SELECT Timestamp, ResourceAttributes['service.version'] AS release,
       LogAttributes['sentry.attachment.name'] AS file,
       LogAttributes['sentry.attachment.size'] AS bytes,
       substring(LogAttributes['sentry.attachment.sha256'], 1, 12) AS sha256
FROM default.otel_logs
WHERE LogAttributes['event.name'] = 'sentry.attachment'
ORDER BY Timestamp
FORMAT PrettyCompactMonoBlock;

-- 5. one crash in full: the stack trace as it is stored
SELECT Timestamp, ServiceName, Body, LogAttributes['exception.stacktrace'] AS stacktrace,
       LogAttributes['sentry.event_id'] AS event_id, ResourceAttributes['os.name'] AS os,
       ResourceAttributes['device.model.identifier'] AS device
FROM default.otel_logs
WHERE LogAttributes['event.name'] = 'sentry.error' AND LogAttributes['sentry.handled'] = 'false'
ORDER BY Timestamp DESC LIMIT 1
FORMAT Vertical;
