-- The game server fleet: twelve dedicated game servers that write event logs and two gauges, with no traces.
--
--   SELECT * FROM gen_game_state(start_minute = <DateTime>, n_minutes = <UInt32>)   one row per server and minute
--   SELECT * FROM gen_game_logs(start_minute = <DateTime>, n_minutes = <UInt32>)    -> otel_logs, ServiceName = 'game-server'
--   SELECT * FROM gen_game_gauge(start_minute = <DateTime>, n_minutes = <UInt32>)   -> otel_metrics_gauge, game.server.*
--
-- Servers gs-kr-01 .. gs-kr-12 on four channels (ch-1 .. ch-4). Concurrent users per server follow the same daily curve as the
-- requests (peak at 12:00 UTC = 21:00 KST), 600 to 1,300 at factor 1, +-5% minute to minute. Every event is a function of
-- (minute, server, index), so a window always yields the same rows and consecutive windows neither repeat nor skip one.
--
-- Per server and minute (state 'up'): a heartbeat `Tick ok`, logins and logouts (~0.3% of the users each), match start and end
-- (~0.15%), item drops (~0.3%), now and then an anti-cheat flag or a chat report (WARN / INFO), and a tick overrun (WARN) that
-- gets more likely the fuller the server is.
--
-- Switches (rows, like the other faults):
--   fault_events  fault = 'server-crash', target = a server id (or '*'). The minute it turns on, the server writes its events up
--                 to the crash, then one ERROR (the process exited) and one WARN (players dropped); while it stays on, the server writes
--                 nothing at all (no logs, no gauges: the gap is the signal); the minute it turns off, it writes its startup
--                 lines and comes back at 30% of its users.
--   deploy_events service = 'game-server': a rolling update. Server i restarts i x 15 s after the deploy time: a shutdown line,
--                 then a startup line with the new version; service.version switches at its own restart.
-- game.backfill = 'true' on minutes before lab_settings.install_minute, like the rest of the backfill.
CREATE OR REPLACE VIEW gen_game_state AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,
    toUInt32(ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'install_minute'), 0)) AS install_s,
    (SELECT arraySort(x -> x.1, groupArray((toUInt64(toUnixTimestamp64Milli(ts)), target, enabled))) FROM fault_events WHERE fault = 'server-crash') AS crashes,
    (SELECT arraySort(x -> x.1, groupArray((toUInt64(toUnixTimestamp64Milli(ts)), version))) FROM deploy_events WHERE service = 'game-server') AS gdeps,
    '1.8.0' AS base_version
SELECT
    m, ms, i, srv, ch, bf, prev_on, cur_on, crash_ms,
    multiIf(NOT prev_on AND cur_on, 'crash', prev_on AND cur_on, 'down', prev_on AND NOT cur_on, 'restart', 'up') AS state,
    -- the version a server runs: the newest deploy whose rolling restart reached it by the end of the minute
    if(ver_dep = '', base_version, ver_dep) AS ver,
    -- a rolling-update restart inside this minute (0 = none), and the version it brings
    upd_ms, if(upd_from = '', base_version, upd_from) AS upd_from_ver, upd_to,
    toUInt32(round(base_ccu * nf * (0.95 + 0.1 * u(ms, i, 'ccu')) * if(prev_on AND NOT cur_on, 0.3, 1))) AS ccu,
    round(12 + ccu / 100.0 + 6 * u(ms, i, 'tick'), 1) AS tick_ms
FROM
(
    SELECT
        w0 + toIntervalMinute(mn.number) AS m,
        toUInt32(m) AS ms,
        toUInt32(sv.number + 1) AS i,
        concat('gs-kr-', leftPad(toString(i), 2, '0')) AS srv,
        concat('ch-', toString(1 + (i - 1) % 4)) AS ch,
        if(ms < install_s, map('game.backfill', 'true'), CAST(map() AS Map(String, String))) AS bf,
        arrayLast(x -> x.1 < toUInt64(ms) * 1000 AND x.2 IN (srv, '*', ''), crashes) AS prev_ev,
        arrayLast(x -> x.1 < (toUInt64(ms) + 60) * 1000 AND x.2 IN (srv, '*', ''), crashes) AS cur_ev,
        prev_ev.3 = 1 AS prev_on,
        cur_ev.3 = 1 AS cur_on,
        cur_ev.1 AS crash_ms,
        arrayLast(d -> d.1 + i * 15000 < (toUInt64(ms) + 60) * 1000, gdeps).2 AS ver_dep,
        arrayFirst(d -> d.1 + i * 15000 >= toUInt64(ms) * 1000 AND d.1 + i * 15000 < (toUInt64(ms) + 60) * 1000, gdeps) AS upd,
        if(upd.2 = '', toUInt64(0), upd.1 + i * 15000) AS upd_ms,
        upd.2 AS upd_to,
        arrayLast(d -> d.1 < upd.1, gdeps).2 AS upd_from,
        600 + cityHash64('base', i) % 700 AS base_ccu,
        1 + 0.3 * sin(2 * pi() * (toHour(m) + toMinute(m) / 60.0 - 6) / 24) AS nf
    FROM numbers({n_minutes:UInt32}) AS mn
    CROSS JOIN numbers(12) AS sv
);

CREATE OR REPLACE VIEW gen_game_logs AS
WITH
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,
    ['ranked', 'casual', 'arena', 'raid'] AS modes,
    ['Desert Ruins', 'Frost Harbor', 'Sky Temple', 'Iron Valley'] AS maps,
    ['android', 'ios', 'pc'] AS platforms,
    ['common', 'rare', 'epic', 'legendary'] AS rarities,
    ['speed_hack', 'aim_assist', 'packet_replay', 'wall_clip'] AS cheats,
    ['abuse', 'spam', 'cheating'] AS reports
SELECT
    toDateTime64(fromUnixTimestamp64Milli(toInt64(ts_ms), 'UTC'), 9, 'UTC') AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    '' AS TraceId, '' AS SpanId, toUInt8(0) AS TraceFlags,
    multiIf(kind = 'crash', 'ERROR', kind IN ('dropped', 'anticheat', 'overrun'), 'WARN', 'INFO') AS SeverityText,
    toUInt8(multiIf(SeverityText = 'ERROR', 17, SeverityText = 'WARN', 13, 9)) AS SeverityNumber,
    'game-server' AS ServiceName,
    multiIf(
        kind = 'tick',      concat('Tick ok ccu=', toString(ccu), ' tick_ms=', toString(tick_ms)),
        kind = 'login',     concat('Player login player_id=', pid, ' platform=', platform, ' latency_ms=', toString(lat)),
        kind = 'logout',    concat('Player logout player_id=', pid, ' session_min=', toString(sess)),
        kind = 'mstart',    concat('Match started match_id=', mid, ' mode=', mode, ' map=', map_, ' players=', toString(players)),
        kind = 'mend',      concat('Match ended match_id=', mid, ' mode=', mode, ' duration_s=', toString(dur), ' winner=team_', if(u(ms, i, k * 7 + 3) < 0.5, 'a', 'b')),
        kind = 'drop',      concat('Item dropped player_id=', pid, ' item=SKU-', sku, ' rarity=', rarity),
        kind = 'anticheat', concat('Anti-cheat flagged player_id=', pid, ' reason=', cheat, ' score=', toString(score)),
        kind = 'report',    concat('Chat report filed player_id=', pid, ' target=', pid2, ' category=', report),
        kind = 'overrun',   concat('Tick overrun tick_ms=', toString(round(tick_ms * 3 + 20 * u(ms, i, 'ov'), 1)), ' budget_ms=33 ccu=', toString(ccu)),
        kind = 'crash',     'Game server process exited unexpectedly: exit code 0xC0000005 (EXCEPTION_ACCESS_VIOLATION) in MatchSimulation::Tick',
        kind = 'dropped',   concat('Lost connection to ', toString(ccu), ' players on ', srv, ': server process gone'),
        kind = 'start',     concat('Game server started version=', ver, ' channel=', ch, ' map_pool=4'),
        kind = 'accept',    concat('Accepting players on ', ch),
        kind = 'upd_stop',  concat('Shutting down for update ', upd_from_ver, ' -> ', upd_to, ', draining ', toString(ccu), ' players'),
        kind = 'upd_start', concat('Game server started version=', upd_to, ' channel=', ch, ' map_pool=4'),
        '') AS Body,
    '' AS ResourceSchemaUrl,
    CAST(mapConcat(map('service.name', 'game-server', 'service.namespace', 'game-world',
                       'service.version', if(kind = 'upd_stop', upd_from_ver, if(kind = 'upd_start', upd_to, ver)),
                       'host.name', srv, 'game.server.id', srv, 'game.channel', ch,
                       'cloud.region', 'ap-northeast-2', 'deployment.environment.name', 'live'), bf)
         AS Map(LowCardinality(String), String)) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    multiIf(kind IN ('login', 'logout'), 'GameServer.Session', kind IN ('mstart', 'mend'), 'GameServer.Match',
            kind IN ('drop', 'anticheat', 'report'), 'GameServer.World', 'GameServer.Host') AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(mapFilter((key, v) -> v != '', map(
        'game.event', multiIf(kind = 'mstart', 'match_start', kind = 'mend', 'match_end', kind = 'drop', 'item_drop',
                              kind = 'report', 'chat_report', kind IN ('upd_stop', 'upd_start'), replaceOne(kind, 'upd_', 'update_'), kind),
        'player.id', if(kind IN ('login', 'logout', 'drop', 'anticheat', 'report'), pid, ''),
        'match.id', if(kind IN ('mstart', 'mend'), mid, ''),
        'game.mode', if(kind IN ('mstart', 'mend'), mode, ''),
        'game.map', if(kind = 'mstart', map_, ''),
        'client.platform', if(kind = 'login', platform, ''),
        'login.latency_ms', if(kind = 'login', toString(lat), ''),
        'item.sku', if(kind = 'drop', concat('SKU-', sku), ''),
        'item.rarity', if(kind = 'drop', rarity, ''),
        'anticheat.reason', if(kind = 'anticheat', cheat, ''),
        'game.ccu', if(kind IN ('tick', 'overrun', 'dropped', 'upd_stop'), toString(ccu), ''),
        'process.exit_code', if(kind = 'crash', '0xC0000005', ''))) AS Map(LowCardinality(String), String)) AS LogAttributes
FROM
(
    SELECT *, kind, k,
        concat('P', toString(1000000 + cityHash64(ms, i, k, kind, 'p') % 9000000)) AS pid,
        concat('P', toString(1000000 + cityHash64(ms, i, k, kind, 'p2') % 9000000)) AS pid2,
        concat('M', substring(lower(hex(cityHash64(ms, i, k, kind, 'm'))), 1, 10)) AS mid,
        modes[1 + cityHash64(ms, i, k, 'mode') % 4] AS mode,
        maps[1 + cityHash64(ms, i, k, 'map') % 4] AS map_,
        platforms[1 + cityHash64(ms, i, k, 'pf') % 3] AS platform,
        rarities[1 + toUInt8(least(3, floor(-log2(u(ms, i, k * 11 + 1)) / 2)))] AS rarity,
        cheats[1 + cityHash64(ms, i, k, 'ch') % 4] AS cheat,
        reports[1 + cityHash64(ms, i, k, 'rp') % 3] AS report,
        leftPad(toString(1 + cityHash64(ms, i, k, 'sku') % 5000), 4, '0') AS sku,
        toUInt32(40 + 120 * u(ms, i, k * 5 + 2)) AS lat,
        toUInt32(3 + 90 * u(ms, i, k * 13 + 4)) AS sess,
        toUInt32(240 + 900 * u(ms, i, k * 17 + 5)) AS dur,
        if(mode = 'raid', 8, if(mode = 'arena', 6, 10)) AS players,
        round(0.80 + 0.19 * u(ms, i, k * 19 + 6), 2) AS score,
        -- event time: a fixed offset for the host lines, otherwise spread over the part of the minute the server was up
        toUInt64(multiIf(kind = 'crash', toInt64(crash_ms) + 3, kind = 'dropped', toInt64(crash_ms) + 1500,
                kind = 'start', toInt64(crash_ms) + 4000, kind = 'accept', toInt64(crash_ms) + 6000,
                kind = 'upd_stop', toInt64(upd_ms) - 3000, kind = 'upd_start', toInt64(upd_ms),
                kind = 'tick', toInt64(ms) * 1000 + 59000 + toInt64(cityHash64(ms, i, 'tk') % 900),
                toInt64(ms) * 1000 + toInt64(from_ms) + toInt64(floor(u(ms, i, concat(kind, toString(k))) * (toInt64(to_ms) - toInt64(from_ms)))))) AS ts_ms
    FROM
    (
        SELECT *,
            toUInt64(if(state = 'restart', least(50000, greatest(0, toInt64(crash_ms) - toInt64(ms) * 1000 + 8000)), 0)) AS from_ms,
            -- the end of the part of the minute the server was up: the crash itself in a crash minute, else just before the heartbeat
            toUInt64(if(state = 'crash', greatest(toInt64(from_ms) + 1, toInt64(crash_ms) - toInt64(ms) * 1000 - 100), 58000)) AS to_ms,
            arrayConcat(
                arrayMap(k -> ('login', k), range(toUInt32(round(ccu * 0.003 * (0.6 + 0.8 * u(ms, i, 'nl')))))),
                arrayMap(k -> ('logout', k), range(toUInt32(round(ccu * 0.003 * (0.6 + 0.8 * u(ms, i, 'no')))))),
                arrayMap(k -> ('mstart', k), range(toUInt32(round(ccu * 0.0015 * (0.6 + 0.8 * u(ms, i, 'ns')))))),
                arrayMap(k -> ('mend', k), range(toUInt32(round(ccu * 0.0015 * (0.6 + 0.8 * u(ms, i, 'ne')))))),
                arrayMap(k -> ('drop', k), range(toUInt32(round(ccu * 0.003 * (0.6 + 0.8 * u(ms, i, 'nd')))))),
                if(u(ms, i, 'ac') < 0.03, [('anticheat', 0)], CAST([] AS Array(Tuple(String, UInt32)))),
                if(u(ms, i, 'rp') < 0.05, [('report', 0)], CAST([] AS Array(Tuple(String, UInt32)))),
                if(u(ms, i, 'ov') < 0.02 * ccu / 1000, [('overrun', 0)], CAST([] AS Array(Tuple(String, UInt32))))) AS normal,
            multiIf(
                -- a crash minute: what happened before the crash (scaled to the part of the minute), then the crash lines
                state = 'crash', arrayConcat([('crash', 0), ('dropped', 0)],
                                             arrayFilter((e, j) -> j <= length(normal) * to_ms / 58000, normal, arrayEnumerate(normal))),
                state = 'down', CAST([] AS Array(Tuple(String, UInt32))),
                arrayConcat(
                    if(state = 'restart', [('start', 0), ('accept', 0)], CAST([] AS Array(Tuple(String, UInt32)))),
                    if(upd_ms > 0, [('upd_stop', 0), ('upd_start', 0)], CAST([] AS Array(Tuple(String, UInt32)))),
                    [('tick', 0)],
                    normal)
            ) AS evs
        FROM gen_game_state(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})
    )
    ARRAY JOIN arrayMap(x -> x.1, evs) AS kind, arrayMap(x -> x.2, evs) AS k
);

CREATE OR REPLACE VIEW gen_game_gauge AS
WITH CAST(map(), 'Map(LowCardinality(String), String)') AS no_attrs
SELECT
    CAST(mapConcat(map('service.name', 'game-server', 'service.namespace', 'game-world', 'service.version', ver,
                       'host.name', srv, 'game.server.id', srv, 'game.channel', ch,
                       'cloud.region', 'ap-northeast-2', 'deployment.environment.name', 'live'), bf)
         AS Map(LowCardinality(String), String)) AS ResourceAttributes,
    '' AS ResourceSchemaUrl,
    'GameServer.Host' AS ScopeName, '' AS ScopeVersion, no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, 'game-server' AS ServiceName,
    metric.1 AS MetricName, metric.2 AS MetricDescription, metric.3 AS MetricUnit,
    no_attrs AS Attributes,
    toDateTime64(m, 9, 'UTC') AS StartTimeUnix, toDateTime64(m + 60, 9, 'UTC') AS TimeUnix,
    metric.4 AS Value,
    toUInt32(0) AS Flags,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM gen_game_state(start_minute = {start_minute:DateTime}, n_minutes = {n_minutes:UInt32})
ARRAY JOIN [('game.server.ccu', 'Players connected to the game server.', '{player}', toFloat64(ccu)),
            ('game.server.tick_duration', 'Average simulation tick time over the minute.', 'ms', toFloat64(tick_ms))] AS metric
WHERE state IN ('up', 'restart');
