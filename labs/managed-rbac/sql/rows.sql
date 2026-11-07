-- Synthetic rows: {n:UInt64} rows spread evenly from now() - {span_s:UInt64} seconds to now().
-- Three games (alpha, bravo, charlie), prod 80 % / qa 20 %, three services per game,
-- 200 players, 5 % ERROR and 10 % WARN. Every value is made up.
INSERT INTO rbac_lab.otel_logs
SELECT
    now64(9) - toIntervalMillisecond(intDiv(({n:UInt64} - number) * {span_s:UInt64} * 1000, {n:UInt64})) AS Timestamp,
    lower(hex(cityHash64(number, 't'))) AS TraceId,
    lower(hex(cityHash64(number, 's') % 4294967296)) AS SpanId,
    1 AS TraceFlags,
    multiIf(r % 100 < 5, 'ERROR', r % 100 < 15, 'WARN', 'INFO') AS SeverityText,
    multiIf(r % 100 < 5, 17, r % 100 < 15, 13, 9) AS SeverityNumber,
    concat(game, '-', svc) AS ServiceName,
    multiIf(
        svc = 'billing' AND r % 100 < 5, concat('payment failed player=', player, ' code=PG_TIMEOUT'),
        svc = 'billing', concat('purchase ok player=', player, ' item=gem_pack_', toString(r % 7)),
        svc = 'match' AND r % 100 < 5, concat('match server crashed room=', toString(r % 500), ' player=', player),
        svc = 'match', concat('match finished room=', toString(r % 500), ' player=', player, ' duration_s=', toString(60 + r % 600)),
        r % 100 < 15, concat('slow login player=', player, ' latency_ms=', toString(800 + r % 2000)),
        concat('login ok player=', player)) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', concat(game, '-', svc), 'game', game, 'deployment.environment.name', env,
        'team', concat(game, '-liveops'), 'cloud.region', if(game = 'charlie', 'us-west-2', 'ap-northeast-1')) AS ResourceAttributes,
    '' AS ScopeSchemaUrl, 'rbac-lab' AS ScopeName, '' AS ScopeVersion, map() AS ScopeAttributes,
    map('player.id', player, 'client.ip', concat('10.', toString(r % 256), '.', toString(intDiv(r, 256) % 256), '.', toString(number % 254 + 1)),
        'guild.id', concat('g-', toString(r % 40))) AS LogAttributes,
    '' AS EventName,
    game AS Game,
    env AS Env
FROM (
    SELECT number,
           cityHash64(number, {seed:String}) AS r,
           ['alpha', 'bravo', 'charlie'][number % 3 + 1] AS game,
           if(intDiv(r, 7) % 10 < 8, 'prod', 'qa') AS env,
           ['gateway', 'match', 'billing'][intDiv(r, 11) % 3 + 1] AS svc,
           concat('p-', leftPad(toString(intDiv(r, 13) % 200), 4, '0')) AS player
    FROM numbers({n:UInt64})
);
