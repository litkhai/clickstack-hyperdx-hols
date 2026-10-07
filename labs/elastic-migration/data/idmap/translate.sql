-- ID translation, in ClickHouse, with a status per field.
--
-- $DB$, $SN_DICT$ and $UID_DICT$ are substituted by the caller, so the same
-- statements run against an in-memory dictionary or a disk-backed one and the
-- two can be compared rather than argued about.
--
-- Three rules this encodes, each from a failure that is silent otherwise:
--
--  1. It reads user_logs_raw and writes user_logs. It never updates a
--     translated value in place. ItemSN's source and target are the same type
--     in the same domain, so a second pass over an updated row would find the
--     *target* SN in the map and translate it again -- and everything else in
--     this lab is built to be re-run after a failure.
--  2. Every field gets a status, because the value cannot carry the
--     information. long -> Int64 is a direct conversion: an untranslated row
--     looks exactly like a translated one.
--  3. A row with any unmapped or malformed field goes to quarantine whole,
--     never half into each table. Half a translated row is the thing that is
--     found in production rather than during the migration.

TRUNCATE TABLE $DB$.user_logs_staged;

INSERT INTO $DB$.user_logs_staged
(`_id`, `@timestamp`, log_type, item_sn_src, item_sn_out, item_sn_status,
 item_sn_coerced, user_id_src, user_id_out, user_id_status, payload, ok)
SELECT
    `_id`,
    `@timestamp`,
    log_type,
    sn_src,
    sn_out,
    sn_status,
    sn_coerced,
    uid_src,
    uid_out,
    uid_status,
    payload,
    sn_status NOT IN ('unmapped', 'malformed')
        AND uid_status NOT IN ('unmapped', 'malformed') AS ok
FROM
(
    SELECT
        `_id`,
        `@timestamp`,
        log_type,
        payload,

        -- ItemSN as it actually arrives. JSONExtractRaw distinguishes the
        -- cases a cast would flatten: '' absent, 'null' null, '1002' a
        -- number, '"1005"' a string-encoded number, '"abc"' junk.
        JSONExtractRaw(payload, 'ItemSN') AS sn_json,
        startsWith(sn_json, '"') AS sn_from_json_string,
        if(item_sn IS NOT NULL,
           toString(item_sn),
           if(sn_json IN ('', 'null'), '', trim(BOTH '"' FROM sn_json))) AS sn_text,
        sn_text != '' AS sn_present,
        toInt64OrNull(sn_text) AS sn_src,
        -- 0 and -1 are "no item", not item 0: an id that is a sentinel is
        -- never used as a lookup key.
        sn_src IN (0, -1) AS sn_sentinel,
        if(sn_present AND isNotNull(sn_src) AND NOT sn_sentinel,
           assumeNotNull(sn_src), CAST(0, 'Int64')) AS sn_key,
        multiIf(
            NOT sn_present,               'not_applicable',
            isNull(sn_src),               'malformed',
            sn_sentinel,                  'not_applicable',
            dictHas('$DB$.$SN_DICT$', sn_key), 'translated',
                                          'unmapped') AS sn_status,
        if(sn_status = 'translated',
           dictGet('$DB$.$SN_DICT$', 'ch_item_sn', sn_key),
           CAST(NULL, 'Nullable(Int64)')) AS sn_out,
        -- Declared rule for a JSON string that holds a number: coerce, and
        -- say so in a column. Never a silent cast, never a quiet drop.
        toUInt8(sn_present AND sn_from_json_string AND isNotNull(sn_src)) AS sn_coerced,

        -- user_id. A UUID in transaction and delivery logs, already numeric
        -- everywhere else -- so "already in the target form" is its own
        -- status rather than a translation that happened to be a no-op.
        trim(BOTH ' ' FROM user_id_raw) AS uid_src,
        log_type IN ('transaction', 'delivery') AS uid_is_uuid_log,
        lower(replaceAll(uid_src, '-', '')) AS uid_norm,
        match(uid_norm, '^[0-9a-f]{32}$') AS uid_is_uuid,
        multiIf(
            uid_src = '',                 'not_applicable',
            NOT uid_is_uuid_log,          if(isNull(toInt64OrNull(uid_src)),
                                             'malformed', 'native'),
            NOT uid_is_uuid,              'malformed',
            dictHas('$DB$.$UID_DICT$', tuple(uid_norm)), 'translated',
                                          'unmapped') AS uid_status,
        multiIf(
            uid_status = 'translated', dictGet('$DB$.$UID_DICT$', 'ch_user_id', tuple(uid_norm)),
            uid_status = 'native',     toInt64OrNull(uid_src),
                                       CAST(NULL, 'Nullable(Int64)')) AS uid_out
    FROM $DB$.user_logs_raw
    -- The caller substitutes a chunk time range for $CHUNK_FILTER$, or 1 for the whole table.
    WHERE $CHUNK_FILTER$
);

-- The translated rows. ReplacingMergeTree on _id, so a second run replaces
-- rather than appends: re-running after fixing a mapping row is safe.
INSERT INTO $DB$.user_logs
(`_id`, `@timestamp`, log_type, item_sn, user_id, payload,
 item_sn_status, user_id_status, item_sn_coerced)
SELECT `_id`, `@timestamp`, log_type, item_sn_out, user_id_out, payload,
       item_sn_status, user_id_status, item_sn_coerced
FROM $DB$.user_logs_staged
WHERE ok = 1;

-- Everything else, kept whole and with its reason, so a mapping row that
-- arrives later can rescue it without going back to Elasticsearch.
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

-- A row that translates on this run must stop being quarantined on this run.
-- Without this, a quarantine table only ever grows and stops meaning
-- "outstanding", which is the one thing it is for.
DELETE FROM $DB$.user_logs_quarantine
WHERE `_id` IN (SELECT `_id` FROM $DB$.user_logs_staged WHERE ok = 1);
