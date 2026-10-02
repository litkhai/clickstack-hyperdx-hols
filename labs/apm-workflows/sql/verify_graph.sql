-- V9: the service graph by SQL. Distinct (parent service -> child service, kind) edges between spans of different services in
-- a window, plus the calls that leave the system (HTTP CLIENT spans whose server.address is not a cluster service), compared
-- with the expected graph of the topology. The two result columns `unexpected` and `missing` must both be empty.
-- Parameters: {start:DateTime} {end:DateTime} (UTC).
WITH
    edges AS
    (
        SELECT p.ServiceName AS parent_service, c.ServiceName AS child, concat(p.SpanKind, ' -> ', c.SpanKind) AS kind, count() AS calls
        FROM otel_traces AS p
        INNER JOIN otel_traces AS c ON c.TraceId = p.TraceId AND c.ParentSpanId = p.SpanId
        WHERE p.Timestamp >= {start:DateTime} AND p.Timestamp < {end:DateTime}
          AND c.Timestamp >= {start:DateTime} AND c.Timestamp < {end:DateTime} + INTERVAL 20 MINUTE
          AND c.ServiceName != p.ServiceName
        GROUP BY parent_service, child, kind
        UNION ALL
        SELECT ServiceName, SpanAttributes['server.address'], 'Client -> external', count()
        FROM otel_traces
        WHERE SpanKind = 'Client' AND SpanAttributes['http.request.method'] != ''
          AND NOT endsWith(SpanAttributes['server.address'], '.svc.cluster.local')
          AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
        GROUP BY ServiceName, SpanAttributes['server.address']
    ),
    expected AS
    (
        SELECT * FROM values('parent_service String, child String, kind String',
            ('web-bff', 'catalog', 'Client -> Server'), ('web-bff', 'cart', 'Client -> Server'), ('web-bff', 'checkout', 'Client -> Server'),
            ('web-bff', 'order', 'Client -> Server'), ('web-bff', 'customer', 'Client -> Server'),
            ('checkout', 'cart', 'Client -> Server'), ('checkout', 'pricing', 'Client -> Server'), ('checkout', 'inventory', 'Client -> Server'),
            ('checkout', 'payment', 'Client -> Server'), ('checkout', 'order', 'Client -> Server'),
            ('order', 'notification', 'Producer -> Consumer'), ('order', 'fulfillment', 'Producer -> Consumer'),
            ('payment', 'pg.example.com', 'Client -> external'), ('notification', 'mail.example.com', 'Client -> external'))
    )
SELECT 'edge' AS part, parent_service, child, kind, calls FROM edges
UNION ALL
SELECT 'unexpected', parent_service, child, kind, calls FROM edges WHERE (parent_service, child, kind) NOT IN (SELECT parent_service, child, kind FROM expected)
UNION ALL
SELECT 'missing', parent_service, child, kind, 0 FROM expected WHERE (parent_service, child, kind) NOT IN (SELECT parent_service, child, kind FROM edges)
ORDER BY part, parent_service, child
