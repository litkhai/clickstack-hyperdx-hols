-- Infrastructure metrics: the Kubernetes cluster the shop runs on, and the hosts of the twelve game servers.
--
--   SELECT * FROM gen_infra_gauge(start_minute = <DateTime>, n_minutes = <UInt32>)   -> otel_metrics_gauge
--   SELECT * FROM gen_infra_sum(start_minute = <DateTime>, n_minutes = <UInt32>)     -> otel_metrics_sum
--
-- They are not written by views of their own: gen_metrics_gauge and gen_metrics_sum (sql/12_gen_metrics.sql) end with
-- UNION ALL of these, so the live views rmv_metrics_gauge / rmv_metrics_sum and the backfill write them with the rest of
-- the metrics, and the watermarks of those views stay correct.
-- ServiceName is '' on every row, as the collector receivers leave it (they set no service.name).
--
-- Kubernetes, as the collector's kubeletstats and k8s_cluster receivers name it (the metrics the ClickStack Kubernetes page
-- reads; k8s.pod.phase 2 = Running, k8s.node.condition_ready 1 = Ready, k8s.namespace.phase 1 = Active):
--   pods     every pod of topo_services, named exactly as gen_traces names it: pod_name(service, version, index), so a
--            deploy (deploy_events) replaces the pods with new names in the minute it happens, like the spans do.
--            k8s.pod.cpu.utilization (cores) grows with the spans the pod served that minute (otel_traces): the purchase
--            regression makes pricing work harder and its CPU shows it. A pod's first minute is its JVM warm-up.
--            k8s.pod.memory.usage (bytes), *_limit_utilization against a limit of 1 core / 1 GiB, k8s.pod.phase,
--            k8s.container.restarts (0), k8s.pod.filesystem.available, k8s.pod.uptime (s, since the deploy that made the pod).
--   nodes    worker-1 .. worker-4 (the node of a pod is a hash of its name, as in gen_traces): CPU and memory = their pods
--            plus the system share; condition ready; filesystem available; uptime.
--   namespaces  phase.
-- Game server hosts, as the hostmetrics receiver names them (resource: host.name = the server id). The host stays up
-- while the game process is down (fault server-crash, gen_game_state): CPU drops to idle and the network goes quiet, and
-- the logs and game.server.* gauges stop - "the process died, not the machine".
--   system.cpu.utilization (state user/system/iowait/idle), system.cpu.load_average.1m, system.memory.utilization,
--   system.filesystem.utilization (gauges); system.memory.usage (bytes, cumulative), system.network.io (bytes per
--   minute, delta: the collector's cumulativetodelta processor) (sums).
-- Two samples a minute (the receivers' collection_interval 30s), at :30 and :00, so a 30-second chart has no empty buckets.
-- MODELLED values; game.backfill = 'true' on minutes before lab_settings.install_minute, like the rest of the backfill.

CREATE OR REPLACE VIEW gen_infra_pods AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    w0 + toIntervalMinute({n_minutes:UInt32}) AS w1,
    (_x) -> lower(leftPad(hex(_x), 16, '0')) AS hex16,
    toUInt32(ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'install_minute'), 0)) AS install_s,
    (SELECT arraySort(x -> x.1, groupArray((toUInt64(toUnixTimestamp64Milli(ts)), service, version))) FROM deploy_events) AS deps,
    served AS
    (
        SELECT ResourceAttributes['k8s.pod.name'] AS pod, toStartOfMinute(Timestamp) AS m, count() AS n
        FROM otel_traces
        WHERE Timestamp >= w0 AND Timestamp < w1 AND ResourceAttributes['k8s.pod.name'] != ''
        GROUP BY pod, m
    )
SELECT p.*, ifNull(s.n, 0) AS spans
FROM
(
    SELECT m, toUInt32(m) AS ms, svc, ns, ix,
        -- the version at the end of the minute, and when the deploy that brought it happened (0 = the base version)
        arrayLast(d -> d.2 = svc AND d.1 < (toUInt64(ms) + 60) * 1000, deps) AS dep,
        if(dep.3 = '', base_version, dep.3) AS ver,
        toUInt64(if(dep.3 = '', 0, dep.1)) AS born_ms,
        concat(svc, '-', substring(lower(hex(cityHash64('rs', svc, ver))), 1, 10), '-',
               substring(lower(hex(cityHash64('pod', svc, ver, ix))), 1, 5)) AS pod,
        concat('worker-', toString(1 + cityHash64('node', pod) % 4), '.example.com') AS node,
        concat(substring(hex16(cityHash64('uid1', pod)), 1, 8), '-', substring(hex16(cityHash64('uid1', pod)), 9, 4), '-',
               substring(hex16(cityHash64('uid1', pod)), 13, 4), '-', substring(hex16(cityHash64('uid2', pod)), 1, 4), '-',
               substring(hex16(cityHash64('uid2', pod)), 5, 12)) AS uid,
        if(ms < install_s, map('game.backfill', 'true'), CAST(map() AS Map(String, String))) AS bf
    FROM
    (
        SELECT w0 + toIntervalMinute(mn.number) AS m, t.service AS svc, t.namespace AS ns, t.base_version AS base_version,
               arrayJoin(range(t.pods)) AS ix
        FROM numbers({n_minutes:UInt32}) AS mn CROSS JOIN topo_services AS t
    )
) AS p
LEFT JOIN served AS s ON s.pod = p.pod AND s.m = p.m;

CREATE OR REPLACE VIEW gen_infra_gauge AS
WITH
    CAST(map(), 'Map(LowCardinality(String), String)') AS no_attrs,
    'game-live-apne2' AS cluster,
    'github.com/open-telemetry/opentelemetry-collector-contrib/receiver/kubeletstatsreceiver' AS kubelet_scope,
    'github.com/open-telemetry/opentelemetry-collector-contrib/receiver/k8sclusterreceiver' AS cluster_scope,
    'github.com/open-telemetry/opentelemetry-collector-contrib/receiver/hostmetricsreceiver/internal/scraper/cpuscraper' AS cpu_scope,
    1073741824 AS gib,
    pods AS
    (
        SELECT *,
            -- cores: a small idle share, a little per span served, the JVM warm-up in the pod's first minute
            round(0.05 + spans * 0.006 + (cityHash64(pod, ms, 'cpu') % 1000) / 50000.0
                  + if(born_ms > 0 AND intDiv(born_ms, 1000) >= ms AND intDiv(born_ms, 1000) < ms + 60, 0.55, 0), 4) AS cpu,
            -- bytes: heap and native around 420 MiB, growing with the pod's age up to +120 MiB over a day; smaller when it just started
            round((if(born_ms > 0 AND intDiv(born_ms, 1000) >= ms, 260, 420 + least(120, (ms - intDiv(born_ms, 1000)) / 720.0))
                   + spans * 0.8 + (cityHash64(pod, ms, 'mem') % 2000) / 100.0) * 1048576) AS mem
        FROM gen_infra_pods(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})
    ),
    nodes AS
    (
        SELECT m, node, any(bf) AS bf, sum(cpu) + 0.35 + (cityHash64(node, toUInt32(m), 'ncpu') % 1000) / 10000.0 AS cpu,
               sum(mem) + 1.8 * gib AS mem
        FROM pods GROUP BY m, node
    ),
    hosts AS
    (
        SELECT m, srv, ch, bf, state, ccu,
            -- the share of the minute the game process was running (the crash minute: up to the crash)
            multiIf(state = 'down', 0., state = 'crash', least(1., greatest(0., (crash_ms - toUInt64(ms) * 1000) / 60000.)), 1.) AS run_share,
            round(least(0.97, multiIf(state = 'restart', 0.55,
                                      0.015 + run_share * (0.16 + ccu / 1300.0 * 0.45))
                              + (cityHash64(srv, ms, 'hcpu') % 1000) / 25000.0), 4) AS busy,
            round(if(state = 'down', 0.07, 0.30 + ccu / 1300.0 * 0.38 * if(state = 'crash', run_share, 1)
                     + (cityHash64(srv, ms, 'hmem') % 1000) / 50000.0), 4) AS mem_used
        FROM gen_game_state(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})
    )
SELECT
    CAST(r.1 AS Map(LowCardinality(String), String)) AS ResourceAttributes, '' AS ResourceSchemaUrl,
    r.2 AS ScopeName, '0.140.0' AS ScopeVersion, no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, '' AS ServiceName,
    r.3 AS MetricName, r.4 AS MetricDescription, r.5 AS MetricUnit,
    CAST(r.6 AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(m + 30 * k, 9, 'UTC') AS StartTimeUnix, toDateTime64(m + 30 * (k + 1), 9, 'UTC') AS TimeUnix,
    -- the two samples of a minute differ a little where a real one would (CPU, memory); states and counts do not
    multiIf(r.3 LIKE 'k8s.%cpu%', round(r.7 * (0.94 + 0.12 * (cityHash64(r.1, m, k, 'j') % 1000) / 1000.0), 4),
            r.3 LIKE 'k8s.%memory%', round(r.7 * (0.99 + 0.02 * (cityHash64(r.1, m, k, 'j') % 1000) / 1000.0), 4),
            r.7) AS Value, toUInt32(0) AS Flags,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM
(
    SELECT m,
        mapConcat(map('k8s.cluster.name', cluster, 'k8s.namespace.name', ns, 'k8s.node.name', node, 'k8s.pod.name', pod,
                      'k8s.pod.uid', uid, 'k8s.deployment.name', svc,
                      'k8s.replicaset.name', concat(svc, '-', substring(lower(hex(cityHash64('rs', svc, ver))), 1, 10))), bf) AS res,
        [
            (res, kubelet_scope, 'k8s.pod.cpu.utilization', 'Total CPU usage (sum of all cores per second) averaged over the sample window', '1', map(), cpu),
            (res, kubelet_scope, 'k8s.pod.cpu_limit_utilization', 'Container cpu utilization as a ratio of the container''s limits', '1', map(), round(cpu / 1.0, 4)),
            (res, kubelet_scope, 'k8s.pod.memory.usage', 'Memory usage, including all memory regardless of when it was accessed', 'By', map(), toFloat64(mem)),
            (res, kubelet_scope, 'k8s.pod.memory_limit_utilization', 'Container memory utilization as a ratio of the container''s limits', '1', map(), round(mem / gib, 4)),
            (res, cluster_scope, 'k8s.pod.phase', 'Current phase of the pod (1 - Pending, 2 - Running, 3 - Succeeded, 4 - Failed, 5 - Unknown)', '1', map(), 2.),
            (mapConcat(res, map('k8s.container.name', svc)), cluster_scope, 'k8s.container.restarts', 'How many times the container has restarted in the recent past', '{restart}', map(), 0.),
            -- ephemeral storage left: 10 GiB less logs and temp files, a little more used the longer the pod runs
            (res, kubelet_scope, 'k8s.pod.filesystem.available', 'Filesystem available', 'By', map(),
                toFloat64(round((10 - 1.2 - least(1.5, (ms - intDiv(born_ms, 1000)) / 86400.0 * 0.1)) * gib)))
        ] AS rows
    FROM pods

    UNION ALL

    SELECT m, mapConcat(map('k8s.cluster.name', cluster, 'k8s.node.name', node), bf) AS res,
        [
            (res, kubelet_scope, 'k8s.node.cpu.utilization', 'Total CPU usage (sum of all cores per second) averaged over the sample window', '1', map(), round(cpu, 4)),
            (res, kubelet_scope, 'k8s.node.memory.usage', 'Memory usage, including all memory regardless of when it was accessed', 'By', map(), toFloat64(round(mem))),
            (res, cluster_scope, 'k8s.node.condition_ready', 'Whether this node is Ready (1), NotReady (0) or Unknown (-1)', '1', map(), 1.),
            (res, kubelet_scope, 'k8s.node.filesystem.available', 'Filesystem available', 'By', map(),
                toFloat64(round((100 - 31 - (cityHash64('nfs', node) % 50) / 10.0 - (toUInt32(m) % 604800) / 604800.0 * 4) * gib)))
        ]
    FROM nodes

    UNION ALL

    SELECT m, mapConcat(map('k8s.cluster.name', cluster, 'k8s.namespace.name', ns), any(bf)) AS res,
        [(res, cluster_scope, 'k8s.namespace.phase', 'The current phase of namespaces (1 for active and 0 for terminating)', '1', map(), 1.)]
    FROM pods GROUP BY m, ns

    UNION ALL

    SELECT m,
        mapConcat(map('host.name', srv, 'host.id', concat('i-0', substring(lower(hex(cityHash64('iid', srv))), 1, 16)),
                      'host.type', 'c6i.2xlarge', 'os.type', 'linux', 'cloud.provider', 'aws', 'cloud.region', 'ap-northeast-2',
                      'game.server.id', srv, 'game.channel', ch, 'deployment.environment.name', 'live'), bf) AS res,
        [
            (res, cpu_scope, 'system.cpu.utilization', 'Difference in system.cpu.time since the last measurement per logical CPU, divided by the elapsed time (value in interval [0,1]).', '1', map('state', 'user'), round(busy * 0.76, 4)),
            (res, cpu_scope, 'system.cpu.utilization', 'Difference in system.cpu.time since the last measurement per logical CPU, divided by the elapsed time (value in interval [0,1]).', '1', map('state', 'system'), round(busy * 0.19, 4)),
            (res, cpu_scope, 'system.cpu.utilization', 'Difference in system.cpu.time since the last measurement per logical CPU, divided by the elapsed time (value in interval [0,1]).', '1', map('state', 'iowait'), round(busy * 0.05, 4)),
            (res, cpu_scope, 'system.cpu.utilization', 'Difference in system.cpu.time since the last measurement per logical CPU, divided by the elapsed time (value in interval [0,1]).', '1', map('state', 'idle'), round(1 - busy, 4)),
            (res, replaceOne(cpu_scope, 'cpuscraper', 'loadscraper'), 'system.cpu.load_average.1m', 'Average CPU Load over 1 minute.', '{thread}', map(), round(busy * 8 * 1.1, 2)),
            (res, replaceOne(cpu_scope, 'cpuscraper', 'memoryscraper'), 'system.memory.utilization', 'Percentage of memory bytes in use.', '1', map('state', 'used'), mem_used),
            (res, replaceOne(cpu_scope, 'cpuscraper', 'memoryscraper'), 'system.memory.utilization', 'Percentage of memory bytes in use.', '1', map('state', 'cached'), 0.12),
            (res, replaceOne(cpu_scope, 'cpuscraper', 'memoryscraper'), 'system.memory.utilization', 'Percentage of memory bytes in use.', '1', map('state', 'free'), round(1 - mem_used - 0.12, 4)),
            (res, replaceOne(cpu_scope, 'cpuscraper', 'filesystemscraper'), 'system.filesystem.utilization', 'Fraction of filesystem bytes used.', '1',
                map('device', '/dev/nvme0n1p1', 'mountpoint', '/', 'type', 'xfs', 'mode', 'rw'),
                round(0.38 + (cityHash64('fs', srv) % 100) / 1000.0 + (toUInt32(m) % 604800) / 604800.0 * 0.04, 4))
        ]
    FROM hosts
)
ARRAY JOIN rows AS r
ARRAY JOIN [0, 1] AS k;

CREATE OR REPLACE VIEW gen_infra_sum AS
WITH
    CAST(map(), 'Map(LowCardinality(String), String)') AS no_attrs,
    'game-live-apne2' AS cluster,
    'github.com/open-telemetry/opentelemetry-collector-contrib/receiver/kubeletstatsreceiver' AS kubelet_scope,
    'github.com/open-telemetry/opentelemetry-collector-contrib/receiver/hostmetricsreceiver/internal/scraper/' AS host_scope,
    34359738368 AS host_mem,   -- 32 GiB, c6i.2xlarge
    toUInt32(ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'install_minute'), 0)) AS install_s,
    -- the nodes and the pods made at install time came up 20 days before it
    toInt64(install_s) - 20 * 86400 AS boot_s
SELECT
    CAST(r.1 AS Map(LowCardinality(String), String)) AS ResourceAttributes, '' AS ResourceSchemaUrl,
    r.2 AS ScopeName, '0.140.0' AS ScopeVersion, no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, '' AS ServiceName,
    r.3 AS MetricName, r.4 AS MetricDescription, r.5 AS MetricUnit,
    CAST(r.6 AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(if(r.8 = 1, m + 30 * k, m), 9, 'UTC') AS StartTimeUnix, toDateTime64(m + 30 * (k + 1), 9, 'UTC') AS TimeUnix,
    -- delta (network bytes): half the minute per sample; cumulative uptime: up to the sample; memory in use: as it is
    multiIf(r.8 = 1, round(r.7 / 2), r.3 LIKE '%.uptime', r.7 - 30 * (1 - k), r.7) AS Value,
    toUInt32(0) AS Flags, toInt32(r.8) AS AggregationTemporality, r.9 = 1 AS IsMonotonic,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM
(
    SELECT m,
        mapConcat(map('k8s.cluster.name', cluster, 'k8s.namespace.name', ns, 'k8s.node.name', node, 'k8s.pod.name', pod,
                      'k8s.pod.uid', uid, 'k8s.deployment.name', svc,
                      'k8s.replicaset.name', concat(svc, '-', substring(lower(hex(cityHash64('rs', svc, ver))), 1, 10))), bf) AS res,
        [(res, kubelet_scope, 'k8s.pod.uptime', 'The time since the pod started', 's', map(),
          toFloat64(greatest(0, toInt64(ms) + 60 - if(born_ms = 0, toInt64(boot_s), toInt64(intDiv(born_ms, 1000))))), 2, 1)] AS rows
    FROM gen_infra_pods(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})

    UNION ALL

    SELECT m, mapConcat(map('k8s.cluster.name', cluster, 'k8s.node.name', node), any(bf)) AS res,
        [(res, kubelet_scope, 'k8s.node.uptime', 'The time since the node started', 's', map(), toFloat64(toInt64(toUInt32(m)) + 60 - boot_s), 2, 1)]
    FROM gen_infra_pods(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})
    GROUP BY m, node

    UNION ALL

    SELECT m,
        mapConcat(map('host.name', srv, 'host.id', concat('i-0', substring(lower(hex(cityHash64('iid', srv))), 1, 16)),
                      'host.type', 'c6i.2xlarge', 'os.type', 'linux', 'cloud.provider', 'aws', 'cloud.region', 'ap-northeast-2',
                      'game.server.id', srv, 'game.channel', ch, 'deployment.environment.name', 'live'), bf) AS res,
        [
            (res, concat(host_scope, 'memoryscraper'), 'system.memory.usage', 'Bytes of memory in use.', 'By', map('state', 'used'), round(mem_used * host_mem), 2, 0),
            -- bytes per minute: game traffic per connected player, a trickle (agents, ssh, the collector) while the process is down
            (res, concat(host_scope, 'networkscraper'), 'system.network.io', 'The number of bytes transmitted and received.', 'By',
                map('device', 'ens5', 'direction', 'receive'), round(60 * (3000 + run_share * ccu * 9000 * (0.9 + 0.2 * (cityHash64(srv, ms, 'rx') % 1000) / 1000.0))), 1, 1),
            (res, concat(host_scope, 'networkscraper'), 'system.network.io', 'The number of bytes transmitted and received.', 'By',
                map('device', 'ens5', 'direction', 'transmit'), round(60 * (2000 + run_share * ccu * 16000 * (0.9 + 0.2 * (cityHash64(srv, ms, 'tx') % 1000) / 1000.0))), 1, 1)
        ]
    FROM
    (
        SELECT m, ms, srv, ch, bf, ccu,
            multiIf(state = 'down', 0., state = 'crash', least(1., greatest(0., (crash_ms - toUInt64(ms) * 1000) / 60000.)), 1.) AS run_share,
            round(if(state = 'down', 0.07, 0.30 + ccu / 1300.0 * 0.38 * if(state = 'crash', run_share, 1)
                     + (cityHash64(srv, ms, 'hmem') % 1000) / 50000.0), 4) AS mem_used
        FROM gen_game_state(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})
    )
)
ARRAY JOIN rows AS r
ARRAY JOIN [0, 1] AS k;
