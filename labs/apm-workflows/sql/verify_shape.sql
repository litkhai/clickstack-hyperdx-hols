-- V4: the shape of the generated spans: distinct (ServiceName, SpanKind, SpanName, ScopeName), then the
-- attribute keys each span kind carries. Compare with the sources listed in NOTES-dev.md.
SELECT ServiceName, SpanKind, SpanName, ScopeName, any(ScopeVersion) AS ScopeVersion, count() AS spans
FROM otel_traces
GROUP BY ServiceName, SpanKind, SpanName, ScopeName
ORDER BY ServiceName, SpanKind, SpanName;

SELECT ServiceName, SpanKind, ScopeName, arraySort(groupUniqArrayArray(mapKeys(SpanAttributes))) AS span_attribute_keys
FROM otel_traces
GROUP BY ServiceName, SpanKind, ScopeName
ORDER BY ServiceName, SpanKind, ScopeName;

SELECT ServiceName, arraySort(groupUniqArrayArray(mapKeys(ResourceAttributes))) AS resource_attribute_keys
FROM otel_traces
GROUP BY ServiceName
ORDER BY ServiceName;
