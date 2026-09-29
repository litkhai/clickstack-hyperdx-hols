-- Mapping-table health, as one query returning (check, severity, n, detail).
--
-- Run before translating. Every check here is something that produces a
-- plausible wrong answer rather than an error, which is why it is a check and
-- not a comment in a runbook. $DB$ is substituted by the caller.
--
-- FAIL: translating would produce wrong rows. WARN: legitimate in some
-- migrations and a mistake in most -- decide, do not skip. INFO: a number
-- worth reading before starting.

SELECT * FROM
(
    -- FAIL. Two different targets for one source id. ReplacingMergeTree will
    -- eventually keep one of them, chosen by updated_at, so translating now
    -- means "whichever row merged last" -- an answer that is stable, wrong,
    -- and impossible to distinguish from a right one later.
    SELECT 'item_sn: ambiguous source ids' AS check,
           'FAIL' AS severity,
           count() AS n,
           arrayStringConcat(arraySlice(groupArray(toString(es_item_sn)), 1, 5), ', ') AS detail
    FROM (SELECT es_item_sn FROM $DB$.item_sn_map
          GROUP BY es_item_sn HAVING uniqExact(ch_item_sn) > 1)

    UNION ALL
    SELECT 'user_id: ambiguous source ids', 'FAIL', count(),
           arrayStringConcat(arraySlice(groupArray(es_user_uuid_norm), 1, 5), ', ')
    FROM (SELECT es_user_uuid_norm FROM $DB$.user_id_map
          GROUP BY es_user_uuid_norm HAVING uniqExact(ch_user_id) > 1)

    UNION ALL
    -- FAIL. A key that is not in canonical form can never be hit, because
    -- every lookup normalises first. The map looks full and matches nothing:
    -- the failure mode is 100% unmapped with no error anywhere.
    SELECT 'user_id: keys not in canonical form (32 lowercase hex)', 'FAIL', count(),
           arrayStringConcat(arraySlice(groupArray(es_user_uuid_norm), 1, 5), ', ')
    FROM $DB$.user_id_map
    WHERE NOT match(es_user_uuid_norm, '^[0-9a-f]{32}$')

    UNION ALL
    -- FAIL, and the one that is specific to ItemSN being the same type on
    -- both sides. If a *target* SN is also a *source* key, then translating
    -- an already-translated value succeeds and returns something different
    -- again. Structurally this lab never does that -- it reads raw and writes
    -- a separate table -- but an in-place UPDATE, or a second translation
    -- pass over the output, would corrupt silently. This counts how much room
    -- there is for that mistake.
    SELECT 'item_sn: target ids that are also source ids (double-translation risk)',
           'FAIL', count(),
           arrayStringConcat(arraySlice(groupArray(toString(ch_item_sn)), 1, 5), ', ')
    FROM $DB$.item_sn_map
    WHERE ch_item_sn GLOBAL IN (SELECT es_item_sn FROM $DB$.item_sn_map)
      AND ch_item_sn != es_item_sn

    UNION ALL
    -- WARN. Several source ids pointing at one target merges those items.
    -- Sometimes deliberate (a deduplication done during the migration),
    -- usually a mapping export that lost a join condition.
    SELECT 'item_sn: several source ids share one target', 'WARN', count(),
           arrayStringConcat(arraySlice(groupArray(toString(ch_item_sn)), 1, 5), ', ')
    FROM (SELECT ch_item_sn FROM $DB$.item_sn_map
          GROUP BY ch_item_sn HAVING uniqExact(es_item_sn) > 1)

    UNION ALL
    SELECT 'user_id: several source ids share one target', 'WARN', count(),
           arrayStringConcat(arraySlice(groupArray(toString(ch_user_id)), 1, 5), ', ')
    FROM (SELECT ch_user_id FROM $DB$.user_id_map
          GROUP BY ch_user_id HAVING uniqExact(es_user_uuid_norm) > 1)

    UNION ALL
    -- INFO. Identity rows are fine and are a real case (T6). A map that is
    -- *entirely* identity usually means the mapping export joined a table to
    -- itself, and the migration would then look completely successful.
    SELECT 'item_sn: identity rows (source == target)', 'INFO', count(), ''
    FROM $DB$.item_sn_map WHERE es_item_sn = ch_item_sn

    UNION ALL
    SELECT 'item_sn: rows in map', 'INFO', count(), '' FROM $DB$.item_sn_map

    UNION ALL
    SELECT 'user_id: rows in map', 'INFO', count(), '' FROM $DB$.user_id_map
)
ORDER BY severity = 'INFO', severity = 'WARN', check
