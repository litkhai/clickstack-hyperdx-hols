-- Switches are rows. The generator reads them; nothing here is "on" by default.

-- fault: slow-query | n-plus-one | pool-exhaustion | downstream-latency | exception-storm
-- target: '*' (or '') = every pod, otherwise a k8s.pod.name. A request is affected iff the
-- latest event for its fault whose ts <= the request's timestamp (and whose target is '*',
-- '' or the request's pod) has enabled = 1.
CREATE TABLE IF NOT EXISTS fault_events
(
    `ts` DateTime64(3),
    `run_id` String,
    `fault` LowCardinality(String),
    `target` String,
    `enabled` UInt8
)
ENGINE = MergeTree
ORDER BY (fault, ts)
TTL toDateTime(ts) + toIntervalDay(30);

-- The version of a request is the latest deploy <= its timestamp (default 1.4.0, no regression).
-- regression = 1: un-indexed order_items lookup in checkout and ~3% errors. A new version also
-- gives the service new pod names.
CREATE TABLE IF NOT EXISTS deploy_events
(
    `ts` DateTime64(3),
    `service` LowCardinality(String),
    `version` String,
    `regression` UInt8
)
ENGINE = MergeTree
ORDER BY (service, ts);

-- Name/value settings; the newest row per name wins. base_rpm = requests per minute at
-- diurnal factor 1.0 (the daily curve runs ~0.7 to ~1.3).
CREATE TABLE IF NOT EXISTS lab_settings
(
    `name` LowCardinality(String),
    `value` Float64,
    `ts` DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (name, ts);

-- Defaults, inserted once (the install minute is the end of the backfill window).
INSERT INTO lab_settings (name, value)
SELECT 'base_rpm', 60
WHERE NOT EXISTS (SELECT 1 FROM lab_settings WHERE name = 'base_rpm');

INSERT INTO lab_settings (name, value)
SELECT 'install_minute', toUnixTimestamp(toStartOfMinute(now()))
WHERE NOT EXISTS (SELECT 1 FROM lab_settings WHERE name = 'install_minute');
