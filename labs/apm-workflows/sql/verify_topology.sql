-- Topology consistency: for every failure kind, the number of spans on its path (origin up to the root of its branch, from
-- topo_spans.err_kinds) must equal the number of specs in topo_failures, and every spec must exist in topo_errors.
-- Both result sets must have no rows with mismatch = 1 / missing > 0.
SELECT f.fk AS fk, f.name AS name, length(f.specs) AS specs_len, p.path_len AS path_len, p.endpoint AS endpoint,
       toUInt8(length(f.specs) != p.path_len) AS mismatch
FROM topo_failures AS f
LEFT JOIN
(
    SELECT endpoint, arrayJoin(arrayDistinct(err_kinds)) AS fk, count() AS path_len
    FROM (SELECT endpoint, idx, err_kinds FROM topo_spans WHERE notEmpty(err_kinds))
    GROUP BY endpoint, fk
) AS p ON f.fk = p.fk
ORDER BY f.fk;

SELECT f.fk AS fk, arrayJoin(f.specs) AS spec_code,
       toUInt8(NOT has((SELECT groupArray(code) FROM topo_errors), replaceAll(spec_code, '{ix}', '1'))) AS missing
FROM topo_failures AS f
WHERE missing > 0;
