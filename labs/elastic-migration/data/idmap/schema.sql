-- ID translation: tables and dictionaries.
--
-- Applied by test_cases.py and bench.py; $DB$ is substituted with the target
-- database. Safe to re-apply: everything is IF NOT EXISTS.
--
-- The shape this models (see README.md): Elasticsearch and ClickHouse disagree
-- about identity in two different ways.
--
--   ItemSN   same type on both sides (long / Int64), different value for the
--            same item. Present as a column on some log types, inside a JSON
--            payload on others, absent on the rest.
--   user_id  a UUID in transaction and delivery logs, a numeric id
--            everywhere else, and a different value from the ClickHouse one.
--
-- The ItemSN case is the dangerous one: long -> Int64 is a direct conversion,
-- so a row that was never translated is indistinguishable from one that was.
-- No cast fails, nothing is null, and the value is a plausible integer. That
-- is why every translated field carries an explicit status, and why the row
-- counts have to satisfy a conservation law rather than merely look right.

CREATE DATABASE IF NOT EXISTS $DB$;

-- ---------------------------------------------------------------- raw input
-- What the export lands: Elasticsearch's values, untranslated. Nothing ever
-- writes a translated value back here -- see "never translate in place" in
-- README.md. This is the table that makes re-running safe.
CREATE TABLE IF NOT EXISTS $DB$.user_logs_raw
(
    `_id`        String,
    `@timestamp` DateTime64(3),
    log_type     LowCardinality(String),
    item_sn      Nullable(Int64),   -- top level, on the log types that have it
    user_id_raw  String,            -- UUID in transaction/delivery, numeric otherwise, '' if absent
    payload      String             -- JSON as text; may carry ItemSN, may not
)
ENGINE = MergeTree
ORDER BY (`@timestamp`, `_id`);

-- ------------------------------------------------------------- mapping data
-- Plain MergeTree, and that is a decision rather than the default.
--
-- ReplacingMergeTree is the obvious engine for "one row per source id" and it
-- destroys the evidence this directory depends on. Measured on 26.6.8.7: two
-- conflicting rows for one key in a single INSERT -- (1004, 88001) and
-- (1004, 88002) -- come back as *one* row, 88002, immediately. In separate
-- INSERTs both are visible until a merge collapses them. So on a
-- ReplacingMergeTree, whether preflight.sql can see an ambiguous mapping at
-- all is a race against the merge scheduler, and the resolution it picks is
-- indistinguishable from a correct mapping afterwards.
--
-- On a MergeTree, a re-export of the same pair leaves duplicate identical
-- rows -- harmless, since a dictionary takes one and the value is the same,
-- and the ambiguity check only fires on two *different* targets. Dedupe
-- deliberately (a GROUP BY into a new table, or OPTIMIZE on a Replacing copy)
-- after the check has passed, not before it has run.
CREATE TABLE IF NOT EXISTS $DB$.item_sn_map
(
    es_item_sn Int64,
    ch_item_sn Int64,
    updated_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY es_item_sn;

-- The key is the *canonical* form of the UUID -- 32 lowercase hex digits, no
-- hyphens -- not the UUID type. Elasticsearch data carries uppercase and
-- unhyphenated variants of the same id, and a lookup keyed on the raw string
-- would miss them while looking perfectly healthy. Normalising on the way in
-- makes that a non-event, and makes "not a UUID at all" a regex away.
CREATE TABLE IF NOT EXISTS $DB$.user_id_map
(
    es_user_uuid_norm String,
    ch_user_id        Int64,
    updated_at        DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY es_user_uuid_norm;

-- ------------------------------------------------------------------ outputs
-- Translated rows. ReplacingMergeTree on _id so re-running the translation is
-- idempotent: the second pass replaces rather than appends.
CREATE TABLE IF NOT EXISTS $DB$.user_logs
(
    `_id`        String,
    `@timestamp` DateTime64(3),
    log_type     LowCardinality(String),
    item_sn      Nullable(Int64),   -- translated, or NULL when not applicable
    user_id      Nullable(Int64),
    payload      String,
    item_sn_status Enum8('translated' = 1, 'not_applicable' = 2, 'unmapped' = 3,
                         'malformed' = 4, 'native' = 5),
    user_id_status Enum8('translated' = 1, 'not_applicable' = 2, 'unmapped' = 3,
                         'malformed' = 4, 'native' = 5),
    -- an ItemSN that arrived as a JSON string ("1005") and was coerced under
    -- the declared rule, rather than silently cast
    item_sn_coerced UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree
ORDER BY `_id`;

-- Rows that could not be translated, kept whole so a later mapping row can
-- rescue them. A partially translated row in user_logs would be the silent
-- wrongness this whole directory exists to prevent, so a row with any
-- unmapped or malformed field lands here instead -- never half in each.
CREATE TABLE IF NOT EXISTS $DB$.user_logs_quarantine
(
    `_id`        String,
    `@timestamp` DateTime64(3),
    log_type     LowCardinality(String),
    item_sn_src  Nullable(Int64),
    user_id_src  String,
    payload      String,
    item_sn_status Enum8('translated' = 1, 'not_applicable' = 2, 'unmapped' = 3,
                         'malformed' = 4, 'native' = 5),
    user_id_status Enum8('translated' = 1, 'not_applicable' = 2, 'unmapped' = 3,
                         'malformed' = 4, 'native' = 5),
    reason       String,
    quarantined_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(quarantined_at)
ORDER BY `_id`;

-- Scratch: one classified row per raw row, before the split into the two
-- tables above. Materialised rather than computed twice, and it is also what
-- makes the conservation law one query instead of three.
CREATE TABLE IF NOT EXISTS $DB$.user_logs_staged
(
    `_id`        String,
    `@timestamp` DateTime64(3),
    log_type     LowCardinality(String),
    item_sn_src  Nullable(Int64),
    item_sn_out  Nullable(Int64),
    item_sn_status Enum8('translated' = 1, 'not_applicable' = 2, 'unmapped' = 3,
                         'malformed' = 4, 'native' = 5),
    item_sn_coerced UInt8,
    user_id_src  String,
    user_id_out  Nullable(Int64),
    user_id_status Enum8('translated' = 1, 'not_applicable' = 2, 'unmapped' = 3,
                         'malformed' = 4, 'native' = 5),
    payload      String,
    ok           UInt8   -- 1: goes to user_logs, 0: goes to quarantine
)
ENGINE = MergeTree
ORDER BY `_id`;

-- ------------------------------------------------------------- dictionaries
-- Two layouts per map, deliberately: the choice is the whole question when
-- the mapping table does not fit in memory, and the same translate.sql runs
-- against either, so the results can be compared rather than argued about.
--
-- LIFETIME(0): a migration's mapping is loaded once and then fixed for the
-- run. A LIFETIME that reloads would mean two chunks translated against two
-- different versions of the map, which is the sort of difference nobody finds
-- until much later.

-- In memory. Fastest, and needs the whole map resident.
CREATE DICTIONARY IF NOT EXISTS $DB$.item_sn_dict_hashed
(
    es_item_sn Int64,
    ch_item_sn Int64
)
PRIMARY KEY es_item_sn
SOURCE(CLICKHOUSE(TABLE 'item_sn_map' DB '$DB$'))
LIFETIME(0)
LAYOUT(HASHED());

-- On disk, bounded memory: the answer to "the mapping table will not fit in
-- RAM". Misses are cached too, so an unmapped id does not re-read per row.
CREATE DICTIONARY IF NOT EXISTS $DB$.item_sn_dict_ssd
(
    es_item_sn Int64,
    ch_item_sn Int64
)
PRIMARY KEY es_item_sn
SOURCE(CLICKHOUSE(TABLE 'item_sn_map' DB '$DB$'))
LIFETIME(0)
LAYOUT(SSD_CACHE(PATH '/var/lib/clickhouse/user_files/$DB$_item_sn_ssd'
                 MAX_PARTITIONS_COUNT 16
                 BLOCK_SIZE 4096
                 FILE_SIZE 268435456
                 WRITE_BUFFER_SIZE 1048576));

CREATE DICTIONARY IF NOT EXISTS $DB$.user_id_dict_hashed
(
    es_user_uuid_norm String,
    ch_user_id        Int64
)
PRIMARY KEY es_user_uuid_norm
SOURCE(CLICKHOUSE(TABLE 'user_id_map' DB '$DB$'))
LIFETIME(0)
LAYOUT(COMPLEX_KEY_HASHED());

CREATE DICTIONARY IF NOT EXISTS $DB$.user_id_dict_ssd
(
    es_user_uuid_norm String,
    ch_user_id        Int64
)
PRIMARY KEY es_user_uuid_norm
SOURCE(CLICKHOUSE(TABLE 'user_id_map' DB '$DB$'))
LIFETIME(0)
LAYOUT(COMPLEX_KEY_SSD_CACHE(PATH '/var/lib/clickhouse/user_files/$DB$_user_id_ssd'
                             MAX_PARTITIONS_COUNT 16
                             BLOCK_SIZE 4096
                             FILE_SIZE 268435456
                             WRITE_BUFFER_SIZE 1048576));
