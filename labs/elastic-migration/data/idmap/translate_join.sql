-- The same translation, with a JOIN instead of a dictionary.
--
-- Worth having as a real alternative rather than a footnote: a JOIN needs no
-- dictionary to define, reload or keep in step with the mapping table, and
-- `join_algorithm = 'grace_hash'` spills to disk, so a mapping table larger
-- than memory is not a special case. For one batch translation of everything
-- it is often the right answer; for repeated lookups over a long migration a
-- dictionary is loaded once instead of per query.
--
-- One difference that matters, and is the reason preflight.sql comes first:
-- an ambiguous mapping row behaves differently here. dictGet silently
-- returns one of the two targets; a JOIN *duplicates the row*. Neither is
-- acceptable, and they fail in different directions -- one loses data, the
-- other invents it.
--
-- join_use_nulls = 1 is what makes a miss detectable: without it a LEFT JOIN
-- fills an Int64 miss with 0, which is indistinguishable from a mapping row
-- that genuinely says 0.
--
-- grace_hash_join_initial_buckets = 32 is the difference between this working
-- and not. Measured against 20M + 20M mapping rows on a server with a 4.5 GiB
-- ceiling: at the default initial bucket count the query asked for 7.36 GiB
-- and was killed, which reads as "the JOIN does not fit" when what did not
-- fit was one bucket's hash table. With 32 buckets the same query finished in
-- 5.8s with a 657 MiB peak. grace_hash does grow its buckets on its own, but
-- it grows them after trying, and the first try is the one that gets killed.

TRUNCATE TABLE $DB$.user_logs_staged;

INSERT INTO $DB$.user_logs_staged
(`_id`, `@timestamp`, log_type, item_sn_src, item_sn_out, item_sn_status,
 item_sn_coerced, user_id_src, user_id_out, user_id_status, payload, ok)
SELECT
    `_id`,
    `@timestamp`,
    log_type,
    sn_src,
    if(sn_status = 'translated', ch_item_sn, CAST(NULL, 'Nullable(Int64)')) AS sn_out,
    sn_status,
    sn_coerced,
    uid_src,
    multiIf(uid_status = 'translated', ch_user_id,
            uid_status = 'native', toInt64OrNull(uid_src),
            CAST(NULL, 'Nullable(Int64)')) AS uid_out,
    uid_status,
    payload,
    sn_status NOT IN ('unmapped', 'malformed')
        AND uid_status NOT IN ('unmapped', 'malformed') AS ok
FROM
(
    SELECT
        c.*,
        m.ch_item_sn AS ch_item_sn,
        u.ch_user_id AS ch_user_id,
        multiIf(
            NOT c.sn_present,        'not_applicable',
            isNull(c.sn_src),        'malformed',
            c.sn_sentinel,           'not_applicable',
            isNotNull(m.ch_item_sn), 'translated',
                                     'unmapped') AS sn_status,
        multiIf(
            c.uid_src = '',          'not_applicable',
            NOT c.uid_is_uuid_log,   if(isNull(toInt64OrNull(c.uid_src)),
                                        'malformed', 'native'),
            NOT c.uid_is_uuid,       'malformed',
            isNotNull(u.ch_user_id), 'translated',
                                     'unmapped') AS uid_status
    FROM
    (
        SELECT
            `_id`,
            `@timestamp`,
            log_type,
            payload,
            JSONExtractRaw(payload, 'ItemSN') AS sn_json,
            startsWith(sn_json, '"') AS sn_from_json_string,
            if(item_sn IS NOT NULL,
               toString(item_sn),
               if(sn_json IN ('', 'null'), '', trim(BOTH '"' FROM sn_json))) AS sn_text,
            sn_text != '' AS sn_present,
            toInt64OrNull(sn_text) AS sn_src,
            sn_src IN (0, -1) AS sn_sentinel,
            if(sn_present AND isNotNull(sn_src) AND NOT sn_sentinel,
               assumeNotNull(sn_src), CAST(0, 'Int64')) AS sn_key,
            toUInt8(sn_present AND sn_from_json_string AND isNotNull(sn_src)) AS sn_coerced,
            trim(BOTH ' ' FROM user_id_raw) AS uid_src,
            log_type IN ('transaction', 'delivery') AS uid_is_uuid_log,
            lower(replaceAll(uid_src, '-', '')) AS uid_norm,
            match(uid_norm, '^[0-9a-f]{32}$') AS uid_is_uuid
        FROM $DB$.user_logs_raw
    ) AS c
    LEFT JOIN $DB$.item_sn_map AS m ON m.es_item_sn = c.sn_key
    LEFT JOIN $DB$.user_id_map AS u ON u.es_user_uuid_norm = c.uid_norm
)
SETTINGS join_use_nulls = 1, join_algorithm = 'grace_hash',
         grace_hash_join_initial_buckets = 32;

INSERT INTO $DB$.user_logs
(`_id`, `@timestamp`, log_type, item_sn, user_id, payload,
 item_sn_status, user_id_status, item_sn_coerced)
SELECT `_id`, `@timestamp`, log_type, item_sn_out, user_id_out, payload,
       item_sn_status, user_id_status, item_sn_coerced
FROM $DB$.user_logs_staged
WHERE ok = 1;

INSERT INTO $DB$.user_logs_quarantine
(`_id`, `@timestamp`, log_type, item_sn_src, user_id_src, payload,
 item_sn_status, user_id_status, reason)
SELECT `_id`, `@timestamp`, log_type, item_sn_src, user_id_src, payload,
       item_sn_status, user_id_status,
       arrayStringConcat(
           arrayFilter(x -> x != '', [
               if(item_sn_status IN ('unmapped', 'malformed'),
                  concat('item_sn ', toString(item_sn_status)), ''),
               if(user_id_status IN ('unmapped', 'malformed'),
                  concat('user_id ', toString(user_id_status)), '')]),
           ', ') AS reason
FROM $DB$.user_logs_staged
WHERE ok = 0;

DELETE FROM $DB$.user_logs_quarantine
WHERE `_id` IN (SELECT `_id` FROM $DB$.user_logs_staged WHERE ok = 1);
