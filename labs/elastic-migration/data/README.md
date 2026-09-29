# Elastic migration: data

[English](#english) | [한국어](#한국어)

## English

Tools for the data part of #19: a sizing step that cuts the move into
bounded chunks, `_mapping` to ClickHouse DDL, a parallel export that resumes,
and parity checks that are query pairs rather than a UI screenshot. The [official documentation for this
migration](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)
already covers the JSON-over-HTTP path and states its own ceiling: below
roughly ten million rows. What is here fills the gap above that ceiling, plus
the field-by-field judgement calls the [type mapping
page](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/types)
does not make for you.

Needs only Python 3's standard library, curl and Docker -- no `elasticsearch`
client, no `requests`, nothing installed beyond what's already in this
repository's other labs.

### Prerequisite: a source to migrate from, and a target to migrate to

`_base/` carries both behind compose profiles, off by default, plus a
seeding script -- so this lab has something real to run against and
somewhere real to land:

```bash
cd _base
cp .env.example .env         # if you have not already
docker compose --profile elastic up -d
./bin/seed_elasticsearch.py  # 300,000 documents by default
```

| | Where | Version |
|---|---|---|
| source | `http://localhost:9200`, index `logs-demo` | Elasticsearch 8.17.0 |
| target | `http://localhost:8124`, user `default`, no password | ClickHouse 26.6.8.7 |

**The target is not the ClickHouse inside the ClickStack all-in-one image**
(port 8123, 26.8.7.19), and that is deliberate: a migration lands in
ClickHouse Cloud, whose regular release channel is on the 26.6 line, and
verifying against a *newer* ClickHouse than the destination can prove a
feature the destination does not have yet. `_base/.env.example` sets
`CH_TARGET_URL` and friends; `load.sh` prefers them over `CH_*` and prints
which server it is loading into. Point them at your own Cloud service for a
real migration.

The seeded mapping is deliberately not a flat shape -- it exists to give
`mapping_to_ddl.py` every judgement call below to actually make: a `keyword`
field, a `text` field with a `.keyword` multi-field, a `nested` field, a
`flattened` field, an `ip` and a `geo_point` field, an `alias`, an
unsupported `completion` field, and roughly 500 fields under `labels.*` that
each seeded document introduces dynamically -- real dynamic mapping growth,
not a simulation of it. See `_base/bin/seed_elasticsearch.py` for the exact
mapping.

Security is disabled on this Elasticsearch (`xpack.security.enabled=false`).
That is only acceptable because it holds nothing but this synthetic seed
data on localhost.

### Connecting to a real cluster

Security is **on by default** in Elasticsearch 8.x, so the throwaway cluster
above -- `xpack.security.enabled=false` -- is the exception and not the rule.
Every tool here takes credentials, and takes them from the environment:

```bash
export ES_URL=https://es.internal:9200
export ES_USER=migration ES_PASSWORD=...        # or ES_API_KEY=...
export ES_CA_CERT=./http_ca.crt                 # 8.x generates its own CA
```

| Variable | What |
|---|---|
| `ES_USER` / `ES_PASSWORD` | basic auth |
| `ES_API_KEY` | the `encoded` value from `POST /_security/api_key` -- scopable and revocable, which a user's password is not |
| `ES_CA_CERT` | PEM bundle. 8.x writes one to `config/certs/http_ca.crt` inside the container: `docker cp <container>:/usr/share/elasticsearch/config/certs/http_ca.crt .` |
| `ES_INSECURE=1` | skip TLS verification. Warns on every call |

Three deliberate refusals, each because the alternative is a worse habit:

- **Both an API key and basic auth is an error**, not a precedence rule. A
  tool that silently picks one gets debugged against the wrong identity.
- **Credentials in the URL are refused.** `https://user:pass@host` ends up in
  shell history, in `ps`, and in error messages.
- **`run.py` passes credentials to the `export.py` it spawns through the
  environment, never in `argv`**, for the same reason: an argument is visible
  to every user on the machine.

**The minimum privileges, found by narrowing an API key until each tool
broke:**

```json
{"cluster": ["monitor"],
 "index": [{"names": ["logs-*"],
            "privileges": ["read", "view_index_metadata", "monitor"]}]}
```

The index-level `monitor` is the one that gets left out: `_cat/indices` needs
*both* the cluster `monitor` privilege (`cluster:monitor/state`) and the index
`monitor` privilege (`indices:monitor/stats`). Without it every tool fails at
its first call with a 403 that names an action rather than a privilege, so the
tools translate both 401 and 403 into the thing to change.

To exercise this locally, `_base/` has the same Elasticsearch with security
on, behind its own profile:

```bash
cd _base && docker compose --profile elastic-secure up -d    # port 9201
ES_URL=http://localhost:9201 ES_USER=elastic ES_PASSWORD=elastic-local-only \
    ./bin/seed_elasticsearch.py --recreate --docs 20000
```

**Verified on:** Elasticsearch 8.17.0 with `xpack.security.enabled=true`, and
separately against a default-configuration 8.17.0 container over https with
its own generated CA.

| Checked | Result |
|---|---|
| whole pipeline with basic auth | seed → `plan.py` → `mapping_to_ddl.py` → `run.py` → `parity_checks.py`: 20,000 documents, 3 chunks, 3/3 verified |
| whole pipeline with an API key scoped to exactly the privileges above | same, 20,000 rows and 20,000 distinct `_id` in ClickHouse 26.6.8.7 |
| the privilege set itself | narrowed an API key until each tool broke. Dropping index `monitor` fails `_cat/indices` with `indices:monitor/stats`; dropping cluster `monitor` fails it with `cluster:monitor/state` |
| https with `ES_CA_CERT` | connects and plans; the summary line names the CA it verified against |
| https without it | `CERTIFICATE_VERIFY_FAILED`, one line of error and one line of what to do -- not a traceback |
| `--es-insecure` | runs, and warns on every call |
| an API key **and** basic auth together | refused |
| credentials in the URL | refused, with the URL redacted in the message |
| the unauthenticated `elastic` profile | still runs end to end: 300,000 documents, 4 chunks, parity passing -- adding auth did not cost the quick path |

### `plan.py`: how large is this, and in how many pieces

Run this before exporting anything. It answers the question that comes
first -- *is this movable in one pass, and if not, what is the queue of
passes* -- and writes the queue out as `plan.json`.

```bash
./plan.py --index 'logs-*' --target-rows 100000 --out plan.json
```

```
logs-*  ->  4 chunk(s) of <= 100000 rows
index                                docs       size  bytes/doc  shards   probe
logs-demo                          300000    89.3MiB        312       3     15m
TOTAL                              300000    89.3MiB

chunk rows: min 384, median 99858, max 99900

Calibration (3 timed batches of 5000, cold single stream):
  66,607 rows/s per stream  x 3 slices = 199,821 rows/s
  export of 300000 rows: ~2s (Elasticsearch read only -- excludes the load and the checks)
```

**Chunks are equal in rows, not in time width.** Equal-width time chunks are
the obvious thing and the wrong thing: observability data is bursty, so one
day can hold forty times another, and a fixed `1d` chunk is then either
uselessly small or over the ceiling. So `plan.py` probes the real
distribution with a `date_histogram`, packs consecutive buckets up to
`--target-rows`, and re-probes at a finer interval any single bucket that
exceeds the target on its own (`--refine-depth`, default 2).

**A chunk is the unit that verifies and fails independently.** That is the
whole reason to chunk rather than only to parallelise. One four-billion-row
run that dies at 90% tells you nothing about the 90%; two thousand chunks
tell you exactly which ones are done. It also bounds everything that can end
a run -- PIT keep-alive, disk for the NDJSON parts, insert pressure -- to one
chunk's worth. The ~10M row ceiling the [official data
page](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)
states is a ceiling *per pass*: chunking is what turns a dataset above it
into a queue of passes below it.

**Chunk ranges tile the whole time span with no holes**, and the plan is
checkable by adding up its own ranges. Packing leaves a gap wherever the
probe found empty time; `plan.py` closes each gap by starting a chunk where
the previous one ended, so a row landing there later is still inside some
chunk's query. Boundaries are `gte`/`lt` on `epoch_millis`, not a date
string: no format or timezone can be misread, and adjacent chunks are
provably disjoint.

**`_cat/indices` is not the row count, and the difference is not small.**
`docs.count` counts Lucene documents, one per element of every `nested`
field, so this repository's own seeded index reports 750,255 for 300,000
documents. Sizing off `_cat` would overestimate the work by 2.5x, so
`plan.py` uses `_count` and says so when the two disagree:

```
WARNING: logs-demo: _cat/indices reports 750255 docs but _count says 300000.
_cat counts one Lucene doc per `nested` element; sizing uses _count (2.5x difference here)
```

**An index pattern is checked for disagreement before anything is planned.**
`plan.py` plans across a pattern, `mapping_to_ddl.py` reads one index and
`run.py` loads into one table -- so a pattern whose indices disagree lands two
shapes in one table, or fails on the first chunk from the odd index out. A
rollover alias with months of backing indices that each grew their own fields
is the normal Elastic case, not the exotic one. One `_field_caps` request
answers it:

| Finding | What happens |
|---|---|
| a field with **two types** across the pattern (`status` is a `long` here and a `keyword` there) | **refused.** One ClickHouse column cannot hold both. Narrow the pattern and plan each group separately, or pass `--allow-mapping-conflicts` once you have decided what the column should be |
| a field **only some indices have** | a warning, and it is recorded in `plan.json`. That column is empty for rows from the indices that lack it -- usually a mapping that grew over time, occasionally the sign that this pattern is really two datasets |

Both lists go into `plan.json` either way, so the decision is visible later
rather than only in a terminal that has scrolled away. `mapping_to_ddl.py`
makes the same distinction: a pattern whose indices have **identical**
mappings is read as one shape with a note, and one whose mappings differ is
refused with a pointer back here.

**The rate is measured, not guessed.** Calibration times a few real PIT +
`search_after` batches -- the same primitive `export.py` uses -- and
multiplies by the recommended slice count. It is a floor and is labelled
one: a cold single stream on one index, excluding the load and the checks,
with no competing live indexing. An estimate nobody measured is exactly the
kind of claim this lab does not make.

Other things it will tell you rather than let you discover later: an index
whose documents have no value for the time field (they are unreachable by a
time-chunked export -- it prints the `must_not exists` query that catches
them), a closed index, and buckets that no time predicate can split at all,
summarised in one line rather than one per bucket.

Slices default to the index's primary shard count, capped at 8:
Elasticsearch documents slicing as most effective at `slices <= shards`, and
`export.py` already warns about a slice that exported nothing.

Then run one chunk with the query the plan emitted:

```bash
./export.py --index logs-demo --out-dir out/logs-demo/chunk-0000 \
    --slices 3 --batch-size 5000 --manifest manifest.json \
    --query '{"bool": {"filter": [{"range": {"@timestamp": {"gte": 1790655553740, "lt": 1790955000000, "format": "epoch_millis"}}}]}}'
```

No change to `export.py` was needed for any of this -- a chunk is
expressible with the `--query` it already took.

**Verified on:** Elasticsearch 8.17.0, ClickHouse 26.6.8.7 (the pinned
migration target), ClickStack/HyperDX 2.39.1 (not on this path). Against the
300,000-document seed:

| What was checked | Result |
|---|---|
| chunk estimates sum to the index's `_count` | 300000 of 300000, and each chunk's estimate matched its actual export exactly (99816 / 99900 / 99900 / 384) |
| ranges tile with no hole or overlap | 0 boundary mismatches |
| every chunk exported and loaded | 300,000 rows in ClickHouse, `uniqExact(_id)` also 300,000 -- complete *and* disjoint, not just the right count |
| parity after a chunked load | all applicable checks `PASS` (count, 251 hourly buckets, 50-document field sample) |
| refinement of an oversized bucket | `--max-buckets 5` forces a `7d` probe whose buckets hold ~200,000 rows; with `--target-rows 50000` it refined down and produced 10 chunks, largest 45,984 |
| a bucket that cannot be split | `--target-rows 100 --refine-depth 0` flagged 1,000 of them, in one summary warning, with the row total still exact |

### `mapping_to_ddl.py`: `_mapping` to DDL, with a paper trail

```bash
./mapping_to_ddl.py --url http://localhost:9200 --index logs-demo \
    --table logs_demo --manifest manifest.json > ddl.sql
```

Prints the `CREATE TABLE` to stdout and a classification report to stderr.
Every field is exactly one of:

| Status | Meaning | In the DDL |
|---|---|---|
| **converted** | a direct equivalent exists | a real column |
| **needs review** | an equivalent exists, but behaviour differs at the edges | a real column, with the difference in an inline comment |
| **unsupported** | no mechanical conversion | commented **out** -- cannot run by accident |

An Elasticsearch type this script has never seen is `unsupported` with
"unknown Elasticsearch type", never silently dropped or silently guessed.

What needs a human, specifically (the official type table covers the rest,
and is linked from the script's own output rather than repeated here):

- **`keyword` vs `text` and multi-fields.** `keyword` converts directly to
  `LowCardinality(String)`. `text` is analyzed -- tokenized, and stemmed
  depending on the analyzer -- and ClickHouse has nothing that reproduces
  that, so it is `needs review` even though the storage type (`String`) is
  obvious. A `.keyword` multi-field under a `text` field is folded into the
  same column rather than duplicated: ClickHouse does not need a separate
  exact-match copy of a string.
- **`nested`.** Elasticsearch's nested query matches each array element in
  independent isolation. `Array(Tuple(...))` is the closest ClickHouse
  shape, but nothing in ClickHouse reproduces that isolation automatically
  (`arrayExists()`/`arrayZip()` can emulate a single-element match by hand).
  Always `needs review`.
- **Dynamic mapping growth.** If an object has 20+ direct child fields that
  are (mostly) all the same shape, that is almost certainly one ES field
  created per distinct name seen at write time, not a designed schema --
  our seeded `labels.custom_0` .. `labels.custom_499` is exactly this. The
  script collapses the whole group into one `JSON` column and reports the
  field count plus a few example names, rather than emitting hundreds of
  columns. Override with `--dynamic-threshold` if a real mapping's fan-out
  is intentional and smaller than 20.
- **Static columns vs the `JSON` type.** `flattened` fields (an unbounded,
  schema-less bag of sub-keys) become `JSON` for the same reason: `flattened`
  treats every leaf as keyword-like text with no type inference, while
  ClickHouse's `JSON` type infers a real type per path -- richer, not
  equivalent, hence `needs review` rather than a silent `converted`.

Run against the seeded `logs-demo` index, the report reads:

```
converted:    10
needs review: 5
unsupported:  1
```

(One of those ten is `_id`, which is not part of `_mapping` at all -- it is
synthesized so export/load/parity have a stable row key.)

#### The sort key is a decision, and the script makes you make it

The DDL used to carry `ORDER BY (<time field>)` and say nothing about it. That
runs, passes every check downstream, and is the one output here that can be
*quietly wrong* -- the table works and is more expensive to query than it
needed to be, and the sort key is the most expensive thing to change once the
data has landed.

So the script now measures what the decision depends on and refuses to make
it for you:

```
-- sort key --
  ORDER BY (@timestamp)  <- DEFAULT, not a design
  candidates by cardinality (all 300000 documents):
            4 distinct  100.0% present  log.level
            6 distinct  100.0% present  service.name
            7 distinct  100.0% present  http.response.status_code
           45 distinct  100.0% present  service.version
       296744 distinct  100.0% present  client.ip  (too high to lead a sort key)
       299214 distinct  100.0% present  trace.id  (too high to lead a sort key)
```

One aggregation request gets cardinality and coverage for every keyword,
boolean, `ip` and integer field -- not `text`, which is analyzed and is not a
sort key. `--probe-shard-size N` measures inside a `sampler` aggregation
instead of over the whole index, and a distinct count that reaches the sample
is reported as *at least* rather than as a number. `--no-probe` skips it, and
then the DDL says candidates were not measured rather than that none were
usable.

**The half it cannot measure is which of those your queries filter on**, and
that is the half that decides. Give it with `--order-by`:

```bash
./mapping_to_ddl.py --index logs-demo --table logs_demo \
    --order-by 'log.level, service.name, @timestamp' > ddl.sql
```

**A low-cardinality prefix is not a free win, and measuring it said so.** Same
300,000 rows loaded twice into the pinned 26.6.8.7 target, `OPTIMIZE FINAL`
both:

| Sort key | On disk |
|---|---|
| `(@timestamp)` -- the default | **22.68 MiB** |
| `(log.level, service.name, @timestamp)` -- the textbook-looking prefix | **23.37 MiB** |

The prefix cost 3% *more*. This repository's seed is uniformly random, which
is the worst case for a prefix: it destroys the time ordering that makes
timestamps compress and creates no runs in exchange. Real observability data
is correlated -- one service emits runs of one level -- so a prefix usually
helps there. Both of those are reasons to decide it from the query pattern
and the data rather than from a rule of thumb, which is exactly why the
script prints numbers and an example it tells you not to take as advice.

No `TTL` and no per-column codecs are emitted at all. A TTL deletes data, so
it is not something to guess: it needs the retention answer from
[the parent README](../README.md).

The optional `--manifest` output is the contract with `export.py`: it lists,
per field, whether the raw Elasticsearch value must reach ClickHouse as
nested JSON (`JSON`, `Array(...)` and `Tuple(...)` columns) rather than being
dot-flattened into scalar keys.

### `export.py`: parallel, resumable

**Primitive: point-in-time (PIT) + `search_after`, sorted on `_shard_doc`,
one stream per slice -- not scroll.** Checked against the pinned 8.17.0
before writing anything: Elasticsearch's own docs now discourage scroll for
deep pagination and describe PIT + `search_after` + `slice` as the
replacement. `_shard_doc` is the cheapest sort available -- no business
ordering is needed for an export, only complete, non-overlapping coverage.

```bash
./export.py --url http://localhost:9200 --index logs-demo \
    --out-dir out/logs-demo --manifest manifest.json --slices 4
```

Each slice keeps its own checkpoint (`part-<n>.ckpt.json`: exported count,
last `_shard_doc` sort value, done flag). Re-running the same command resumes
every unfinished slice from its checkpoint instead of restarting; a finished
slice is skipped. **Resumability was verified against the running
Elasticsearch, not assumed**: closing a PIT mid-slice and then reopening a
brand-new one before resuming with `search_after` produced zero overlap and
zero gaps against the original export (checked `_id`-for-`_id`). That is
only safe because this lab's export window assumes a static source index --
see "whether both systems run in parallel" in the parent
[README](../README.md). A new PIT is a new snapshot; if the index is being
written to while a slice resumes, rows can be skipped or repeated.

At-least-once, not exactly-once, for a narrower reason too: if the process
is killed after a batch is fsynced to disk but before its checkpoint is
written, that batch is re-fetched on resume, leaving a handful of duplicate
`_id`s in the part file. `parity_checks.py`'s count check flags a real
discrepancy, not a handful of duplicates; dedupe on load if you need exact
counts (a `GROUP BY _id` pass, or a `ReplacingMergeTree` keyed on `_id`).

A slice that finishes having exported zero rows is reported as a `WARNING`
at export time already, in addition to the parity check below -- silent
undercounting is exactly the failure mode this is built to catch, so it is
checked twice.

### `load.sh`: local file, or `s3()` at real scale

There is no Elasticsearch table engine. `load.sh` takes the local path
(NDJSON straight into ClickHouse over HTTP, since `_base/` has no object
storage):

```bash
./load.sh --out-dir out/logs-demo --table logs_demo
```

It loads into `CH_TARGET_URL` when that is set and into `CH_URL` otherwise,
and prints the server it chose on its first line. The choice is all-or-
nothing rather than field by field: a connection assembled half from
`CH_TARGET_*` and half from `CH_*` is how a migration ends up in the wrong
server with a plausible-looking log.

Resumable at the part level: a `part-<n>.ndjson.loaded` marker means that
part is skipped on a re-run (`--force` reloads anyway). At real scale --
past the ~10M row ceiling this lab exists to get past -- upload the NDJSON
parts to object storage instead and load with `s3()` in one statement, so
ClickHouse parallelizes the read itself rather than this script sending one
curl per file from a single machine. The exact statement is in a comment at
the bottom of `load.sh`.

### `parity_checks.py`: query pairs, not a screenshot

```bash
./parity_checks.py --es-index logs-demo --ch-table logs_demo \
    --ch-url http://localhost:8124 --ch-user default --ch-password '' \
    --ch-database default --out-dir out/logs-demo
```

`PASS` / `FAIL` / `SKIP`, same convention as `_base/bin/check.sh` -- a `SKIP`
is not a `PASS`. Four pairs:

| # | Elasticsearch side | ClickHouse side |
|---|---|---|
| 1 | `_count` | `SELECT count()` |
| 2 | `date_histogram` aggregation | `GROUP BY toStartOfHour(...)` |
| 3 | `_mget` on a random sample of `_id`s | `SELECT ... WHERE _id IN (...)` |
| 4 | `_count` vs. `export.py`'s own checkpoints | (no ES query -- this is the "did a slice silently produce nothing" check) |

Check 4 needs `--out-dir` from the export step; without it, it `SKIP`s rather
than pretending to pass. It was exercised against a deliberately zeroed-out
checkpoint during development and correctly failed with the slice number
named, not just "counts don't match" (see the PR that introduced this file).

### `run.py`: one state for the whole migration

`export.py` checkpoints slices and `load.sh` skips parts it has loaded, so
the mechanism to resume already existed. What did not exist was anything
that knew the state of the migration *as a whole*: three steps with three
unrelated notions of "done", spread across checkpoint files in per-chunk
directories. For a run measured in hours, "rerun the command and it resumes"
is necessary and not sufficient -- the question is *is it progressing, and
what is stuck*, without reading four hundred checkpoint files.

```bash
./run.py --plan plan.json --table logs_demo --manifest manifest.json
./run.py --plan plan.json --table logs_demo --status
```

```
4 chunk(s) to do, into http://localhost:8124 (default.logs_demo)
[1/4] chunk 0000 verified (99816 rows, 4.1s)
[2/4] chunk 0001 verified (99900 rows, 3.6s)
...
chunks: 4/4 verified
rows:   300000 of ~300000 loaded and verified (100.0%)
rate:   25,424 rows/s over 12s of work  ->  ~0s remaining at that rate
```

It drives the existing tools as subprocesses rather than reimplementing
them, and keeps one state file next to the plan:

```
pending -> exported -> loaded -> verified
```

**Progress and failure are two separate fields.** Overwriting a chunk's
stage with `failed` would lose the step a retry should resume from, and the
retry would then have nothing to resume. So a failed chunk still says
`stopped at exported`, and retrying it loads rather than re-exports.

**Every transition is written atomically** -- tmp, `fsync`, rename, the same
discipline `export.py` uses for its checkpoints -- so a `kill -9` at any
moment leaves a state file that describes reality rather than a half-written
one.

**Each chunk is verified as it lands**, not only at the end: its own row
count on both sides, compared on `uniqExact(_id)` rather than `count()`. A
chunk that silently exported nothing is the failure this path exists to
catch, and finding it after two thousand chunks is finding it too late.
Distinct `_id` is the number that must match because `export.py` is
at-least-once by design: a resumed chunk can carry a few repeated rows
without having lost or invented any, so duplicates are reported as a note
and a wrong *distinct* count is a failure.

**`--status` reads nothing but the state file.** No Elasticsearch, no
ClickHouse, and non-zero exit if anything failed, so it works from cron or a
dashboard and not only from the terminal that started the run. For anything
failed it prints the exact command that retries just that:

```
chunks: 0/3 verified, 3 exported, 3 failed

  chunk 0000  stopped at exported  attempts 2
    load.sh exit 1: Code: 60. DB::Exception: Table default.logs_demo_fail does not exist. (UNKNOWN_TABLE)

3 chunk(s) failed. Retry just those:
  ./run.py --plan plan.json --table logs_demo_fail --only 0000,0001,0002
```

That error line is *chosen*, not truncated: a tail of the last few hundred
characters of a failing tool starts mid-sentence, and three hundred of those
are unreadable exactly when they matter.

**Retries are cheap because export is resumable.** `--max-attempts` (3) with
a doubling backoff; an attempt re-fetches one batch, not one chunk.
`--stop-on-error` halts at the first chunk that exhausts its attempts;
otherwise the run continues and the failures are collected.

**Two ways to lose a run that are guarded rather than documented.** A second
`run.py` on the same state file would double-load parts and interleave state
writes, and the symptom -- a row count that is too high -- looks like a
migration bug rather than an operator mistake, so the state file is locked.
A run that was OOM-killed cannot release its lock, and surviving that is the
whole point, so a lock whose process is provably gone *on this host* is
reclaimed with a note; one held by a live process, or taken on another
machine, is refused with `--force-unlock` named. And a plan regenerated
mid-run renumbers chunks, so a state file from an older plan is refused
rather than silently mixing two chunk sets.

`SIGINT` and `SIGTERM` finish the chunk in flight, flush, and exit 130 with
the resume command -- rather than leaving a state file that claims a run is
still going.

### Where memory goes, and the one dial

Worth stating plainly, because "it died on OOM" is what starts this
conversation:

| Process | Holds | Bounded by |
|---|---|---|
| `run.py` | the state file | number of chunks |
| `export.py` | one batch per slice | `--batch-size` x document size x `--slices` |
| `load.sh` | nothing; curl streams a part | -- |
| ClickHouse | the insert block | server-side settings |

Nothing in the path accumulates the index or a result set, which is exactly
what the `elasticdump`-style approach cannot say. So an OOM means
`--batch-size` is wrong for the document size: a number to lower (`run.py`
passes it through), not a redesign. `plan.py` reports bytes per document, so
the batch's rough footprint is knowable before the first run rather than
after the first kill.

**Verified on:** Elasticsearch 8.17.0, ClickHouse 26.6.8.7 (the pinned
migration target). Against the 300,000-document seed and a 4-chunk plan:

| What was checked | Result |
|---|---|
| a clean run | 4/4 chunks verified, 300,000 rows, `uniqExact(_id)` 300,000 |
| `kill -9` on the run **and** its `export.py` children, mid-chunk | state said `1/4 verified, 1 exported, 2 pending` and 99,816 rows; the lock was left behind |
| resume after that kill | reclaimed the dead run's lock, did the remaining 3 chunks, resumed the killed chunk at its `load` step rather than re-exporting, final table 300,000 rows / 300,000 distinct `_id` |
| a real failure (wrong table name) | 3 chunks `stopped at exported` after 2 attempts each, one-line cause per chunk, `--status` exit 1 |
| retry after fixing the cause | `--only 0000,0001,0002` resumed at `load`, all verified, 300,000 rows |
| duplicate detection | one chunk's parts deliberately re-loaded: 149,916 duplicates named in the note, still `verified` because distinct `_id` matched -- the documented at-least-once semantics, made visible |
| state against a regenerated plan | refused, naming both `generated_at` timestamps |
| lock held by a live process | refused, naming the pid, host and `--force-unlock` |

### `idmap/`: when the two systems disagree about identity

A separate problem from moving the rows, and the one with no official
coverage at all: the same entity carries a different id on each side, and the
mapping table is too large to hold in the exporter.

[`idmap/`](idmap/) answers it in ClickHouse rather than in flight -- load raw,
translate with a dictionary or a spilling `JOIN`, quarantine what does not
map. The hard part is not the SQL: `long` -> `Int64` is a direct conversion,
so a row that was never translated is indistinguishable from one that was. No
cast fails and nothing is null. So that directory is mostly a **case matrix**
and the checks that make a wrong translation loud -- 70 assertions across
four axes, including the two that fail silently in a hand-written
translation: translating twice, and conservation of row counts.

### Try it end to end

```bash
cd _base && docker compose --profile elastic up -d
./bin/seed_elasticsearch.py

cd ../labs/elastic-migration/data
./plan.py --index logs-demo --target-rows 100000 --out plan.json
./mapping_to_ddl.py --index logs-demo --table logs_demo --manifest manifest.json > ddl.sql
curl -sS http://localhost:8124/ --data-binary @ddl.sql

# one chunk at a time, resumable, with progress -- or run the three tools by hand
./run.py --plan plan.json --table logs_demo --manifest manifest.json
./run.py --plan plan.json --table logs_demo --status

# whole-table checks. No --out-dir here: check 4 compares one export's
# checkpoints against the whole index, which only holds for a single-pass
# export -- run.py already made the per-chunk version of that check, for
# every chunk.
./parity_checks.py --es-index logs-demo --ch-table logs_demo \
    --ch-url http://localhost:8124 --ch-user default --ch-password '' \
    --ch-database default
```

**Verified on:** Elasticsearch 8.17.0, ClickHouse 26.6.8.7 (the pinned
migration target), ClickStack/HyperDX 2.39.1 (`clickstack-all-in-one`, running
but not on this path -- the data path touches ClickHouse only) -- 300,000
seeded documents, 523 mapped fields, classified 10/5/1, exported across 4
slices, loaded, and all four parity checks passing (`total row count matches
(300000)`, 251 hourly buckets matching, 50-document field sample matching,
4-slice coverage with none empty).

26.6.8.7 is the newest public patch of the line ClickHouse Cloud's regular
release channel runs; a live Cloud service reports `26.6.1.2191`, the same
minor from a build that is not published.

Not run: `_base/bin/verify.sh` (needs `HYPERDX_INGESTION_KEY` from the
ClickStack UI, which is not obtainable non-interactively) -- not needed here,
since this lab's data path only touches ClickHouse, not HyperDX ingestion.

---

## 한국어

#19의 데이터 부분을 위한 도구들입니다: 이관을 크기가 제한된 청크로 자르는
규모 산정, `_mapping`을 ClickHouse DDL로 바꾸는 변환기, 재개 가능한 병렬
내보내기, 그리고 UI 스크린샷이 아니라 쿼리 쌍으로 하는 정합성 검증. [이 마이그레이션의 공식
문서](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)가
JSON·HTTP 경로를 이미 다루며 스스로 한계를 명시합니다: 약 1천만 행 미만.
여기 있는 것은 그 위쪽의 공백과, [type mapping
페이지](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/types)가
대신 판단해주지 않는 필드별 판단들을 채웁니다.

Python 3 표준 라이브러리, curl, Docker만 필요합니다 -- `elasticsearch`
클라이언트도, `requests`도, 이 저장소의 다른 실습에 없던 것은 아무것도
설치하지 않습니다.

### 사전 준비: 옮겨올 원본과 도착할 목적지

`_base/`가 둘 다 compose 프로파일 뒤에 (기본 비활성) 들고 있고 시딩
스크립트도 있습니다. 이 실습이 실제로 읽을 원본과 실제로 도착할 곳이
생겼습니다.

```bash
cd _base
cp .env.example .env         # 아직 안 했다면
docker compose --profile elastic up -d
./bin/seed_elasticsearch.py  # 기본 300,000건
```

| | 위치 | 버전 |
|---|---|---|
| 원본 | `http://localhost:9200`, 인덱스 `logs-demo` | Elasticsearch 8.17.0 |
| 목적지 | `http://localhost:8124`, 유저 `default`, 비밀번호 없음 | ClickHouse 26.6.8.7 |

**목적지는 ClickStack all-in-one 이미지 안의 ClickHouse(8123 포트,
26.8.7.19)가 아닙니다.** 의도적입니다. 마이그레이션은 ClickHouse Cloud에
도착하고 Cloud의 regular release 채널은 26.6 라인인데, 목적지보다 **더 새로운**
ClickHouse에서 검증하면 목적지에 아직 없는 기능을 증명할 수 있습니다.
`_base/.env.example`에 `CH_TARGET_URL` 등이 있고, `load.sh`는 `CH_*`보다 그쪽을
우선하며 어느 서버에 적재하는지 첫 줄에 출력합니다. 실제 마이그레이션에서는
여러분의 Cloud 서비스를 가리키게 하세요.

시딩되는 매핑은 의도적으로 단순하지 않습니다 -- 아래 `mapping_to_ddl.py`의
모든 판단 대상을 실제로 갖게 하기 위해서입니다: `keyword` 필드, `.keyword`
multi-field가 있는 `text` 필드, `nested` 필드, `flattened` 필드, `ip`와
`geo_point` 필드, `alias`, 지원 불가한 `completion` 필드, 그리고 각 문서가
동적으로 만들어내는 `labels.*` 아래 약 500개 필드 -- 시뮬레이션이 아니라
실제 동적 매핑 증식입니다. 정확한 매핑은 `_base/bin/seed_elasticsearch.py`를
보세요.

이 Elasticsearch는 보안이 꺼져 있습니다(`xpack.security.enabled=false`).
localhost에 이 합성 시드 데이터만 있기 때문에만 괜찮은 설정입니다.

### 실제 클러스터에 연결하기

Elasticsearch 8.x는 보안이 **기본 활성**입니다. 위의 임시 클러스터
(`xpack.security.enabled=false`)가 예외이고 규칙이 아닙니다. 여기의 모든 도구가
자격증명을 받고, 환경 변수에서 받습니다.

```bash
export ES_URL=https://es.internal:9200
export ES_USER=migration ES_PASSWORD=...        # 또는 ES_API_KEY=...
export ES_CA_CERT=./http_ca.crt                 # 8.x는 자체 CA를 만듭니다
```

| 변수 | 용도 |
|---|---|
| `ES_USER` / `ES_PASSWORD` | basic auth |
| `ES_API_KEY` | `POST /_security/api_key`의 `encoded` 값. 권한을 좁히고 폐기할 수 있습니다. 사용자 비밀번호는 그렇게 못 합니다 |
| `ES_CA_CERT` | PEM 번들. 8.x가 컨테이너 안 `config/certs/http_ca.crt`에 만들어 둡니다: `docker cp <컨테이너>:/usr/share/elasticsearch/config/certs/http_ca.crt .` |
| `ES_INSECURE=1` | TLS 검증 생략. 호출마다 경고합니다 |

의도적으로 거부하는 세 가지입니다. 각각 대안이 더 나쁜 습관이기 때문입니다.

- **API key와 basic auth를 같이 주면 오류입니다.** 우선순위 규칙이 아닙니다.
  조용히 하나를 고르는 도구는 엉뚱한 신원으로 디버깅하게 만듭니다.
- **URL 안의 자격증명은 거부합니다.** `https://user:pass@host`는 셸 히스토리,
  `ps`, 오류 메시지에 남습니다.
- **`run.py`는 자신이 띄우는 `export.py`에 자격증명을 환경 변수로 전달하고,
  `argv`로는 절대 전달하지 않습니다.** 같은 이유입니다 -- 인자는 그 머신의 모든
  사용자에게 보입니다.

**최소 권한 -- API key 권한을 좁혀가며 각 도구가 깨지는 지점을 찾아 확정했습니다.**

```json
{"cluster": ["monitor"],
 "index": [{"names": ["logs-*"],
            "privileges": ["read", "view_index_metadata", "monitor"]}]}
```

빠뜨리기 쉬운 것은 **인덱스 레벨의 `monitor`** 입니다. `_cat/indices`는 클러스터
`monitor`(`cluster:monitor/state`)와 인덱스 `monitor`(`indices:monitor/stats`)를
**둘 다** 요구합니다. 이게 없으면 모든 도구가 첫 호출에서 403으로 죽는데, 오류는
권한 이름이 아니라 액션 이름을 말합니다. 그래서 도구들이 401과 403을 "무엇을
바꿔야 하는지"로 번역해 출력합니다.

로컬에서 이 경로를 시험하려면, `_base/`에 보안을 켠 같은 Elasticsearch가 별도
프로파일로 있습니다.

```bash
cd _base && docker compose --profile elastic-secure up -d    # 9201 포트
ES_URL=http://localhost:9201 ES_USER=elastic ES_PASSWORD=elastic-local-only \
    ./bin/seed_elasticsearch.py --recreate --docs 20000
```

**Verified on:** `xpack.security.enabled=true`로 띄운 Elasticsearch 8.17.0, 그리고
기본 설정 8.17.0 컨테이너에 대해 https + 자체 생성 CA로 별도 검증.

| 확인한 것 | 결과 |
|---|---|
| basic auth로 전체 파이프라인 | 시딩 → `plan.py` → `mapping_to_ddl.py` → `run.py` → `parity_checks.py`: 문서 20,000건, 청크 3개, 3/3 verified |
| 위 최소 권한으로만 좁힌 API key로 전체 파이프라인 | 동일. ClickHouse 26.6.8.7에 20,000행, distinct `_id` 20,000 |
| 최소 권한 집합 자체 | API key 권한을 좁혀가며 각 도구가 깨지는 지점 확인. 인덱스 `monitor`를 빼면 `_cat/indices`가 `indices:monitor/stats`로, 클러스터 `monitor`를 빼면 `cluster:monitor/state`로 실패 |
| `ES_CA_CERT`와 함께 https | 연결·계획 성공. 요약 줄에 어떤 CA로 검증했는지 표시 |
| CA 없이 https | `CERTIFICATE_VERIFY_FAILED`. 트레이스백이 아니라 오류 한 줄 + 조치 한 줄 |
| `--es-insecure` | 동작하고, 호출마다 경고 |
| API key와 basic auth를 함께 | 거부 |
| URL 안의 자격증명 | 거부. 메시지에서 URL은 가려집니다 |
| 인증 없는 `elastic` 프로파일 | 여전히 처음부터 끝까지 동작: 문서 300,000건, 청크 4개, 정합성 통과 -- 인증 추가가 빠른 경로를 해치지 않았습니다 |

### `plan.py`: 얼마나 큰가, 그리고 몇 조각인가

무엇이든 내보내기 전에 먼저 실행하세요. 가장 먼저 와야 하는 질문 --
*한 번에 옮길 수 있는가, 아니면 몇 번의 패스가 필요한가* -- 에 답하고, 그
대기열을 `plan.json`으로 씁니다.

```bash
./plan.py --index 'logs-*' --target-rows 100000 --out plan.json
```

```
logs-*  ->  4 chunk(s) of <= 100000 rows
index                                docs       size  bytes/doc  shards   probe
logs-demo                          300000    89.3MiB        312       3     15m
TOTAL                              300000    89.3MiB

chunk rows: min 384, median 99858, max 99900

Calibration (3 timed batches of 5000, cold single stream):
  66,607 rows/s per stream  x 3 slices = 199,821 rows/s
  export of 300000 rows: ~2s (Elasticsearch read only -- excludes the load and the checks)
```

**청크는 시간 폭이 아니라 행 수로 균등합니다.** 같은 시간 폭으로 자르는 것은
당연해 보이지만 틀린 방법입니다. 관측성 데이터는 몰려 있어서 어떤 하루가 다른
하루의 40배일 수 있고, 그러면 고정 `1d` 청크는 쓸모없이 작거나 천장을
넘습니다. 그래서 `plan.py`는 `date_histogram`으로 실제 분포를 조사하고,
연속된 버킷을 `--target-rows`까지 묶고, 혼자서 목표를 넘는 버킷은 더 촘촘한
간격으로 다시 조사합니다(`--refine-depth`, 기본 2).

**청크는 독립적으로 검증되고 독립적으로 실패하는 단위입니다.** 병렬화만이
아니라 청크로 나누는 진짜 이유입니다. 40억 행을 한 번에 돌려 90%에서 죽으면 그
90%에 대해 아무것도 알 수 없지만, 2000개 청크는 어디까지 끝났는지 정확히
알려줍니다. 또한 실행을 끝장낼 수 있는 모든 것 -- PIT keep-alive, NDJSON
파트용 디스크, INSERT 부하 -- 을 청크 하나 분량으로 묶어둡니다.
[공식 데이터 문서](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)가
말하는 약 1천만 행 천장은 **패스당** 천장입니다. 청크로 나누는 것은 그 위의
데이터셋을 그 아래의 패스 대기열로 바꾸는 일입니다.

**청크 범위는 전체 시간 구간을 빈틈 없이 덮습니다.** 그래서 계획 자체를 범위
합으로 검산할 수 있습니다. 패킹은 조사에서 빈 시간이 나온 곳마다 틈을 남기는데,
`plan.py`는 각 청크를 이전 청크가 끝난 지점에서 시작시켜 그 틈을 닫습니다.
나중에 그 구간에 행이 들어와도 어느 청크의 쿼리 안에 있습니다. 경계는 날짜
문자열이 아니라 `epoch_millis`의 `gte`/`lt`입니다 -- 포맷이나 타임존을
잘못 읽을 여지가 없고, 인접 청크가 서로 겹치지 않음이 증명됩니다.

**`_cat/indices`는 행 수가 아니고, 차이가 작지도 않습니다.** `docs.count`는
Lucene 문서를 세고 `nested` 필드의 원소마다 하나씩 포함하므로, 이 저장소의
시드 인덱스는 문서 300,000건에 750,255를 보고합니다. `_cat`으로 규모를 잡으면
작업량을 2.5배 과대추정합니다. `plan.py`는 `_count`를 쓰고, 둘이 다르면
말합니다.

```
WARNING: logs-demo: _cat/indices reports 750255 docs but _count says 300000.
_cat counts one Lucene doc per `nested` element; sizing uses _count (2.5x difference here)
```

**인덱스 패턴은 계획을 세우기 전에 불일치를 검사합니다.** `plan.py`는 패턴 전체를
계획하고, `mapping_to_ddl.py`는 인덱스 하나를 읽고, `run.py`는 테이블 하나에
적재합니다. 그래서 패턴 안의 인덱스들이 서로 다르면 한 테이블에 두 가지 모양이
들어가거나, 다른 하나에서 온 첫 청크에서 실패합니다. 각자 필드를 키워온 백킹 인덱스가
몇 달치 쌓인 rollover 별칭은 Elastic에서 예외가 아니라 보통입니다. `_field_caps`
요청 한 번으로 답이 나옵니다.

| 발견 | 동작 |
|---|---|
| 패턴 안에서 **타입이 둘인** 필드 (`status`가 한쪽은 `long`, 다른 쪽은 `keyword`) | **거부합니다.** ClickHouse 컬럼 하나가 둘을 담을 수 없습니다. 패턴을 좁혀 그룹별로 계획하거나, 컬럼을 무엇으로 할지 정한 뒤 `--allow-mapping-conflicts`를 주세요 |
| **일부 인덱스에만 있는** 필드 | 경고하고 `plan.json`에 기록합니다. 그 컬럼은 해당 필드가 없는 인덱스에서 온 행에 대해 비어 있습니다 -- 대개 시간이 지나며 커진 매핑이고, 때로는 이 패턴이 실은 두 데이터셋이라는 신호입니다 |

두 목록은 어느 경우든 `plan.json`에 들어갑니다. 스크롤이 지나간 터미널이 아니라
나중에도 결정이 보이도록요. `mapping_to_ddl.py`도 같은 구분을 합니다: 인덱스들의
매핑이 **동일한** 패턴은 한 모양으로 읽고 그 사실을 알리며, 서로 다른 패턴은 여기를
가리키며 거부합니다.

**속도는 추측이 아니라 측정입니다.** 캘리브레이션은 실제 PIT +
`search_after` 배치 몇 개의 시간을 재고 -- `export.py`가 쓰는 것과 같은
기본 도구입니다 -- 권장 슬라이스 수를 곱합니다. 이것은 하한이고 그렇게
표시됩니다: 인덱스 하나에 대한 차가운 단일 스트림, 적재와 검증 제외, 경쟁하는
실시간 인덱싱 없음. 아무도 측정하지 않은 추정치는 이 실습이 하지 않는
종류의 주장입니다.

나중에 발견하게 두지 않고 미리 알려주는 것들: 시간 필드에 값이 없는 문서가
있는 인덱스(시간 청크 내보내기로는 도달할 수 없습니다 -- 그것들을 잡는
`must_not exists` 쿼리를 출력합니다), 닫힌 인덱스, 그리고 어떤 시간 조건으로도
쪼갤 수 없는 버킷(버킷마다 한 줄이 아니라 한 줄로 요약).

슬라이스 기본값은 인덱스의 주 샤드 수이고 최대 8입니다. Elasticsearch가
`slices <= shards`에서 가장 효과적이라고 문서화했고, 0건을 내보낸 슬라이스는
`export.py`가 이미 경고합니다.

그다음 계획이 만들어 준 쿼리로 청크 하나를 실행합니다.

```bash
./export.py --index logs-demo --out-dir out/logs-demo/chunk-0000 \
    --slices 3 --batch-size 5000 --manifest manifest.json \
    --query '{"bool": {"filter": [{"range": {"@timestamp": {"gte": 1790655553740, "lt": 1790955000000, "format": "epoch_millis"}}}]}}'
```

이 전부를 위해 `export.py`를 고치지 않았습니다 -- 청크는 이미 받고 있던
`--query`로 표현됩니다.

**Verified on:** Elasticsearch 8.17.0, ClickHouse 26.6.8.7(고정된 마이그레이션
목적지), ClickStack/HyperDX 2.39.1(이 경로에는 관여하지 않음). 문서 300,000건
시드 기준:

| 확인한 것 | 결과 |
|---|---|
| 청크 추정 합 = 인덱스 `_count` | 300000 / 300000, 각 청크 추정값이 실제 내보낸 수와 정확히 일치(99816 / 99900 / 99900 / 384) |
| 범위가 빈틈·겹침 없이 타일링 | 경계 불일치 0건 |
| 모든 청크 내보내기+적재 | ClickHouse에 300,000행, `uniqExact(_id)`도 300,000 -- 개수만 맞는 게 아니라 **완전하고 겹치지 않음** |
| 청크 적재 후 정합성 | 해당되는 검사 전부 `PASS`(개수, 시간별 버킷 251개, 문서 50개 필드 샘플) |
| 큰 버킷 재조사 | `--max-buckets 5`로 `7d` 조사를 강제하면 버킷당 약 200,000행인데, `--target-rows 50000`에서 더 촘촘히 재조사해 10개 청크, 최대 45,984행 |
| 쪼갤 수 없는 버킷 | `--target-rows 100 --refine-depth 0`에서 1,000개를 요약 경고 한 줄로 표시, 행 합계는 여전히 정확 |

### `mapping_to_ddl.py`: `_mapping` → DDL, 근거를 남기며

```bash
./mapping_to_ddl.py --url http://localhost:9200 --index logs-demo \
    --table logs_demo --manifest manifest.json > ddl.sql
```

`CREATE TABLE`은 stdout에, 분류 리포트는 stderr에 출력합니다. 모든 필드는
정확히 다음 중 하나로 분류됩니다.

| 상태 | 의미 | DDL에서 |
|---|---|---|
| **converted** | 직접 대응이 존재 | 실제 컬럼 |
| **needs review** | 대응은 있지만 경계에서 동작이 다름 | 실제 컬럼, 차이는 인라인 주석으로 |
| **unsupported** | 기계적 변환 불가 | 주석으로 처리되어 실행 **불가** |

이 스크립트가 모르는 Elasticsearch 타입은 "unknown Elasticsearch type"으로
unsupported 처리됩니다. 조용히 버려지거나 조용히 추측되지 않습니다.

구체적으로 사람이 필요한 부분 (나머지는 공식 type 문서가 다루며, 여기서
다시 쓰지 않고 스크립트 출력에서 링크합니다):

- **`keyword` 대 `text`와 multi-field.** `keyword`는 `LowCardinality(String)`로
  직접 변환됩니다. `text`는 분석됩니다 -- 토큰화되고, analyzer에 따라 어간
  추출까지 -- ClickHouse에는 이를 재현할 것이 없어서, 저장 타입(`String`)은
  명확해도 needs review입니다. `text` 필드 아래 `.keyword` multi-field는
  별도 컬럼으로 중복하지 않고 같은 컬럼에 합칩니다: ClickHouse는 문자열의
  별도 정확매칭 사본이 필요 없습니다.
- **`nested`.** Elasticsearch의 nested 쿼리는 배열의 각 원소를 독립적으로
  매칭합니다. `Array(Tuple(...))`가 가장 가까운 ClickHouse 형태지만, 그
  격리를 자동으로 재현하는 것은 없습니다(`arrayExists()`/`arrayZip()`으로
  수동 에뮬레이션 가능). 항상 needs review입니다.
- **동적 매핑 증식.** 한 객체에 같은 모양의 직계 자식 필드가 20개 이상
  있다면, 거의 확실히 쓰기 시점에 이름별로 하나씩 생성된 ES 필드이지
  설계된 스키마가 아닙니다 -- 시딩한 `labels.custom_0` ~ `labels.custom_499`가
  정확히 이 경우입니다. 이 스크립트는 그룹 전체를 하나의 `JSON` 컬럼으로
  합치고 필드 수와 예시 이름 몇 개를 리포트합니다. 실제 매핑의 분기가
  의도적이고 20보다 작다면 `--dynamic-threshold`로 조정하세요.
- **정적 컬럼 대 `JSON` 타입.** `flattened` 필드(경계 없는, 스키마 없는
  하위 키 묶음)는 같은 이유로 `JSON`이 됩니다: `flattened`는 모든 leaf를
  타입 추론 없이 keyword 같은 텍스트로 취급하는 반면, ClickHouse의 `JSON`
  타입은 경로별로 실제 타입을 추론합니다 -- 더 풍부하지만 동등하지 않아서
  조용히 converted 처리하지 않고 needs review로 둡니다.

시딩한 `logs-demo` 인덱스로 실행한 리포트:

```
converted:    10
needs review: 5
unsupported:  1
```

(이 열 개 중 하나는 `_id`입니다 -- `_mapping`에는 아예 없고,
export·load·정합성 검증이 안정적인 행 키를 갖도록 합성한 것입니다.)

#### 정렬 키는 결정이고, 이 스크립트는 그 결정을 하게 만듭니다

전에는 DDL이 `ORDER BY (<시간 필드>)`를 넣고 그에 대해 아무 말도 하지 않았습니다.
그래도 실행되고, 하위 검사도 모두 통과하며, 여기 있는 출력 중 **조용히 틀릴 수 있는**
유일한 것입니다 -- 테이블은 동작하고 필요 이상으로 비싼 쿼리가 되며, 정렬 키는
데이터가 들어간 뒤에 바꾸기 가장 비싼 것입니다.

그래서 이제 결정에 필요한 것을 **측정**하고, 결정 자체는 대신 하지 않습니다.

```
-- sort key --
  ORDER BY (@timestamp)  <- DEFAULT, not a design
  candidates by cardinality (all 300000 documents):
            4 distinct  100.0% present  log.level
            6 distinct  100.0% present  service.name
            7 distinct  100.0% present  http.response.status_code
           45 distinct  100.0% present  service.version
       296744 distinct  100.0% present  client.ip  (too high to lead a sort key)
       299214 distinct  100.0% present  trace.id  (too high to lead a sort key)
```

집계 요청 한 번으로 keyword·boolean·`ip`·정수 필드의 카디널리티와 존재 비율을
가져옵니다. `text`는 제외합니다 -- 분석되는 필드이고 정렬 키가 아닙니다.
`--probe-shard-size N`은 인덱스 전체가 아니라 `sampler` 집계 안에서 측정하고, 표본
크기에 도달한 distinct 값은 숫자가 아니라 **최소값**으로 보고합니다. `--no-probe`로
건너뛰면 DDL은 "쓸만한 후보가 없었다"가 아니라 "측정하지 않았다"고 말합니다.

**측정할 수 없는 절반은 그중 무엇을 여러분의 쿼리가 필터하는지**이고, 결정하는 쪽은
그 절반입니다. `--order-by`로 알려주세요.

```bash
./mapping_to_ddl.py --index logs-demo --table logs_demo \
    --order-by 'log.level, service.name, @timestamp' > ddl.sql
```

**저카디널리티 접두 컬럼은 공짜 이득이 아니고, 측정이 그렇게 말했습니다.** 같은
300,000행을 고정된 26.6.8.7 목적지에 두 번 적재하고 양쪽 `OPTIMIZE FINAL`:

| 정렬 키 | 디스크 |
|---|---|
| `(@timestamp)` -- 기본값 | **22.68 MiB** |
| `(log.level, service.name, @timestamp)` -- 교과서적으로 좋아 보이는 접두 | **23.37 MiB** |

접두를 넣은 쪽이 3% **더 큽니다**. 이 저장소의 시드는 균일 난수이고, 그것이 접두
컬럼에 최악의 조건입니다 -- 타임스탬프를 압축하게 해주는 시간 순서를 깨뜨리면서 그
대가로 얻는 연속 구간이 없습니다. 실제 관측성 데이터는 상관이 있어서(한 서비스가 같은
레벨을 연달아 내보냄) 접두가 대개 도움이 됩니다. 이 둘 모두가 정렬 키를 경험 법칙이
아니라 **쿼리 패턴과 데이터로** 결정해야 하는 이유이고, 그래서 스크립트가 숫자를
출력하면서 예시는 조언으로 받지 말라고 말합니다.

`TTL`과 컬럼별 코덱은 아예 생성하지 않습니다. TTL은 데이터를 지우므로 추측할 것이
아니라 [상위 README](../README.md)의 보존 기간 답이 필요합니다.

선택적 `--manifest` 출력은 `export.py`와의 계약입니다: 각 필드가 원본
Elasticsearch 값을 점(dot)으로 평탄화된 스칼라 키가 아니라 중첩 JSON
(`JSON`, `Array(...)`, `Tuple(...)` 컬럼)으로 ClickHouse에 전달해야 하는지
알려줍니다.

### `export.py`: 병렬, 재개 가능

**기본 도구: point-in-time(PIT) + `search_after`, `_shard_doc`로 정렬,
슬라이스당 하나의 스트림 -- scroll이 아닙니다.** 코드를 쓰기 전에 고정한
8.17.0에 대해 확인했습니다: Elasticsearch 공식 문서가 이제 deep pagination에
scroll을 권장하지 않고 PIT + `search_after` + `slice`를 대체재로 설명합니다.
`_shard_doc`은 가장 저렴한 정렬입니다 -- 내보내기에는 업무적 순서가 필요
없고, 완전하고 중복 없는 커버리지만 필요합니다.

```bash
./export.py --url http://localhost:9200 --index logs-demo \
    --out-dir out/logs-demo --manifest manifest.json --slices 4
```

각 슬라이스는 자신의 체크포인트(`part-<n>.ckpt.json`: 내보낸 개수, 마지막
`_shard_doc` 정렬값, 완료 플래그)를 유지합니다. 같은 명령을 다시 실행하면
끝나지 않은 슬라이스는 체크포인트에서 재개되고, 끝난 슬라이스는 건너뜁니다.
**재개 가능성은 가정이 아니라 실행 중인 Elasticsearch에 대해 실제로
검증했습니다**: 슬라이스 중간에 PIT를 닫고 완전히 새 PIT를 연 뒤
`search_after`로 재개했을 때, 원본 내보내기와 대비해 중복도 빠짐도
0이었습니다(`_id` 단위로 확인). 이것이 안전한 이유는 이 실습의 내보내기
구간이 정적인 원본 인덱스를 가정하기 때문입니다 -- 상위
[README](../README.md)의 "두 시스템을 병행 운영하는지" 참고. 새 PIT는 새
스냅샷입니다. 슬라이스가 재개되는 동안 인덱스에 쓰기가 계속되면 행이
빠지거나 중복될 수 있습니다.

정확히 한 번이 아니라 최소 한 번인 이유가 하나 더 있습니다: 배치가
디스크에 fsync된 뒤 체크포인트가 쓰이기 전에 프로세스가 죽으면, 재개 시 그
배치를 다시 가져와 part 파일에 중복 `_id`가 몇 개 남습니다.
`parity_checks.py`의 개수 검사는 진짜 불일치를 잡아내는 것이고 몇 개의
중복은 아닙니다. 정확한 개수가 필요하면 적재 시 중복 제거하세요(`_id`로
`GROUP BY`, 또는 `_id` 키의 `ReplacingMergeTree`).

내보낸 행이 0개인 슬라이스는 아래 정합성 검사뿐 아니라 내보내기 시점에도
`WARNING`으로 보고됩니다 -- 조용한 과소 집계가 바로 이 도구가 잡으려는
실패 모드이므로 두 번 확인합니다.

### `load.sh`: 로컬 파일, 실제 규모에서는 `s3()`

Elasticsearch 테이블 엔진은 없습니다. `load.sh`는 로컬 경로를 택합니다
(`_base/`에 오브젝트 스토리지가 없으므로 NDJSON을 HTTP로 바로 ClickHouse에):

```bash
./load.sh --out-dir out/logs-demo --table logs_demo
```

`CH_TARGET_URL`이 설정돼 있으면 그쪽으로, 없으면 `CH_URL`로 적재하고, 고른
서버를 첫 줄에 출력합니다. 필드별이 아니라 전부-아니면-전무로 고릅니다.
`CH_TARGET_*`에서 절반, `CH_*`에서 절반을 가져온 접속 정보는 그럴듯한 로그를
남기며 엉뚱한 서버에 적재되는 전형적인 경로입니다.

part 단위로 재개 가능합니다: `part-<n>.ndjson.loaded` 마커가 있으면 재실행
시 건너뜁니다(`--force`로 강제 재적재). 이 실습이 넘어서려는 ~1천만 행
한계를 넘는 실제 규모에서는, NDJSON part를 오브젝트 스토리지에 올리고 한
문장으로 `s3()`를 써서 적재하세요. 그러면 이 스크립트가 파일마다 curl을
보내는 대신 ClickHouse가 직접 읽기를 병렬화합니다. 정확한 문장은
`load.sh` 하단 주석에 있습니다.

### `parity_checks.py`: 스크린샷이 아니라 쿼리 쌍

```bash
./parity_checks.py --es-index logs-demo --ch-table logs_demo \
    --ch-url http://localhost:8124 --ch-user default --ch-password '' \
    --ch-database default --out-dir out/logs-demo
```

`_base/bin/check.sh`와 같은 `PASS`/`FAIL`/`SKIP` 규칙입니다 -- `SKIP`은
`PASS`가 아닙니다. 네 개의 쌍:

| # | Elasticsearch 쪽 | ClickHouse 쪽 |
|---|---|---|
| 1 | `_count` | `SELECT count()` |
| 2 | `date_histogram` 집계 | `GROUP BY toStartOfHour(...)` |
| 3 | 무작위 `_id` 샘플에 `_mget` | `SELECT ... WHERE _id IN (...)` |
| 4 | `_count` 대 `export.py`의 체크포인트 | (ES 쿼리 없음 -- "슬라이스가 조용히 0건을 냈는가" 검사) |

검사 4는 내보내기 단계의 `--out-dir`가 필요합니다. 없으면 통과한 것처럼
꾸미지 않고 `SKIP`합니다. 개발 중 체크포인트를 일부러 0으로 만들어 실행해
보았고, "개수가 안 맞음"이 아니라 슬라이스 번호를 짚어 정확히 실패했습니다
(이 파일을 추가한 PR 참고).

### `run.py`: 마이그레이션 전체를 위한 하나의 상태

`export.py`는 슬라이스를 체크포인트하고 `load.sh`는 적재한 part를 건너뛰므로,
재개할 **방법**은 이미 있었습니다. 없던 것은 마이그레이션 **전체**의 상태를 아는
무엇입니다. 세 단계가 각자 다른 "완료" 개념을 갖고, 청크별 디렉터리의 체크포인트
파일에 흩어져 있었습니다. 시간 단위로 도는 작업에서 "명령을 다시 실행하면 재개됨"은
필요하지만 충분하지 않습니다. 질문은 *진행되고 있는가, 무엇이 막혀 있는가*이고,
체크포인트 400개를 읽지 않고 답해야 합니다.

```bash
./run.py --plan plan.json --table logs_demo --manifest manifest.json
./run.py --plan plan.json --table logs_demo --status
```

```
4 chunk(s) to do, into http://localhost:8124 (default.logs_demo)
[1/4] chunk 0000 verified (99816 rows, 4.1s)
[2/4] chunk 0001 verified (99900 rows, 3.6s)
...
chunks: 4/4 verified
rows:   300000 of ~300000 loaded and verified (100.0%)
rate:   25,424 rows/s over 12s of work  ->  ~0s remaining at that rate
```

기존 도구들을 다시 구현하지 않고 서브프로세스로 구동하며, 계획 파일 옆에 상태
파일 하나를 둡니다.

```
pending -> exported -> loaded -> verified
```

**진행 단계와 실패는 별개의 필드입니다.** 청크의 단계를 `failed`로 덮어쓰면 재시도가
어느 단계에서 이어가야 하는지를 잃어버리고, 그러면 재시도할 것이 없어집니다. 그래서
실패한 청크도 `stopped at exported`라고 말하고, 재시도는 다시 내보내지 않고 적재부터
합니다.

**모든 전이는 원자적으로 기록됩니다** -- tmp, `fsync`, rename. `export.py`가
체크포인트에 쓰는 것과 같은 방식이므로, 어느 순간에 `kill -9`을 당해도 상태 파일은
반쯤 쓰인 것이 아니라 실제를 기술합니다.

**각 청크는 도착하는 즉시 검증됩니다.** 맨 끝이 아니라 그때그때, 양쪽의 행 수를
`count()`가 아니라 `uniqExact(_id)`로 비교합니다. 조용히 0건을 내보낸 청크가 이
경로가 존재하는 이유인데, 청크 2000개를 지나서 발견하는 것은 너무 늦게 발견하는
것입니다. 일치해야 하는 값이 distinct `_id`인 이유는 `export.py`가 설계상 최소 한
번이기 때문입니다. 재개된 청크는 잃거나 만들어내지 않고도 중복 행 몇 개를 가질 수
있으므로, 중복은 메모로 보고하고 **distinct** 개수가 틀린 것을 실패로 봅니다.

**`--status`는 상태 파일만 읽습니다.** Elasticsearch도 ClickHouse도 필요 없고,
실패가 있으면 0이 아닌 코드로 끝나므로 실행한 터미널이 아니라 cron이나 대시보드에서도
동작합니다. 실패한 것에 대해서는 그것만 재시도하는 정확한 명령을 출력합니다.

```
chunks: 0/3 verified, 3 exported, 3 failed

  chunk 0000  stopped at exported  attempts 2
    load.sh exit 1: Code: 60. DB::Exception: Table default.logs_demo_fail does not exist. (UNKNOWN_TABLE)

3 chunk(s) failed. Retry just those:
  ./run.py --plan plan.json --table logs_demo_fail --only 0000,0001,0002
```

이 오류 한 줄은 잘라낸 것이 아니라 **고른** 것입니다. 실패한 도구 출력의 마지막
수백 자를 자르면 문장 중간에서 시작하고, 그런 것 300개는 정작 필요한 순간에 읽을 수
없습니다.

**재시도가 싼 이유는 내보내기가 재개 가능하기 때문입니다.** `--max-attempts`(기본 3)와
2배씩 늘어나는 백오프. 한 번의 시도가 다시 가져오는 것은 배치 하나이고 청크 하나가
아닙니다. `--stop-on-error`는 시도를 모두 소진한 첫 청크에서 멈추고, 기본값은 계속
진행하며 실패를 모읍니다.

**문서로 적는 대신 막아둔, 실행을 잃는 두 가지 방법.** 같은 상태 파일에 대해 `run.py`를
두 번 돌리면 part를 이중 적재하고 상태 기록이 교차합니다. 증상 -- 너무 많은 행 수 -- 은
운영자의 실수가 아니라 마이그레이션 버그처럼 보입니다. 그래서 상태 파일을 잠급니다.
OOM으로 죽은 실행은 자기 잠금을 해제할 수 없고 바로 그것을 견디는 게 이 도구의
목적이므로, **이 호스트에서** 프로세스가 확실히 사라진 잠금은 메모를 남기고 회수합니다.
살아 있는 프로세스가 쥔 잠금이나 다른 머신에서 잡은 잠금은 `--force-unlock`을 알려주며
거부합니다. 그리고 실행 중간에 계획을 다시 만들면 청크 번호가 바뀌므로, 이전 계획에서
만든 상태 파일은 두 청크 집합을 조용히 섞지 않고 거부합니다.

`SIGINT`·`SIGTERM`은 진행 중인 청크를 마치고 상태를 내린 뒤 재개 명령과 함께 130으로
끝냅니다. 더 이상 돌지 않는 실행을 여전히 도는 것처럼 기술하는 상태 파일을 남기지
않습니다.

### 메모리는 어디로 가고, 조절 다이얼은 하나

"OOM으로 죽었다"가 이 대화의 출발점이므로 분명히 적어둡니다.

| 프로세스 | 무엇을 들고 있나 | 무엇으로 제한되나 |
|---|---|---|
| `run.py` | 상태 파일 | 청크 개수 |
| `export.py` | 슬라이스당 배치 하나 | `--batch-size` x 문서 크기 x `--slices` |
| `load.sh` | 없음. curl이 part를 스트리밍 | -- |
| ClickHouse | INSERT 블록 | 서버 설정 |

이 경로의 어디에서도 인덱스나 결과셋을 누적하지 않습니다. `elasticdump` 방식이
말할 수 없는 부분이 정확히 이것입니다. 따라서 OOM은 문서 크기에 대해 `--batch-size`가
잘못됐다는 뜻입니다 -- 재설계가 아니라 낮출 숫자 하나입니다(`run.py`가 그대로
전달합니다). `plan.py`가 문서당 바이트를 보고하므로, 첫 kill 뒤가 아니라 첫 실행
전에 배치의 대략적인 크기를 알 수 있습니다.

**Verified on:** Elasticsearch 8.17.0, ClickHouse 26.6.8.7(고정된 마이그레이션
목적지). 문서 300,000건 시드와 4개 청크 계획 기준:

| 확인한 것 | 결과 |
|---|---|
| 정상 실행 | 4/4 청크 verified, 300,000행, `uniqExact(_id)` 300,000 |
| 청크 중간에 실행과 그 `export.py` 자식들을 `kill -9` | 상태는 `1/4 verified, 1 exported, 2 pending`과 99,816행, 잠금 파일이 남음 |
| 그 kill 이후 재개 | 죽은 실행의 잠금을 회수하고 남은 3개 청크를 처리, 죽은 청크는 다시 내보내지 않고 `load` 단계에서 이어감, 최종 테이블 300,000행 / distinct `_id` 300,000 |
| 실제 실패(잘못된 테이블 이름) | 3개 청크가 2회 시도 후 `stopped at exported`, 청크별 원인 한 줄, `--status` 종료 코드 1 |
| 원인 수정 후 재시도 | `--only 0000,0001,0002`이 `load`에서 이어가 전부 verified, 300,000행 |
| 중복 감지 | 한 청크의 part를 의도적으로 재적재: 메모에 중복 149,916건을 명시하고, distinct `_id`가 맞으므로 여전히 `verified` -- 문서화된 최소 한 번 의미를 눈에 보이게 만든 것 |
| 다시 만든 계획에 대한 상태 파일 | 양쪽 `generated_at`을 짚어 거부 |
| 살아 있는 프로세스가 쥔 잠금 | pid·호스트와 `--force-unlock`을 알려주며 거부 |

### `idmap/`: 두 시스템이 동일성에 대해 다를 때

행을 옮기는 것과는 별개의 문제이고, 공식 문서가 전혀 다루지 않는 부분입니다.
같은 실체가 양쪽에서 다른 id를 갖고, 매핑 테이블은 익스포터에 올리기엔 너무
큽니다.

[`idmap/`](idmap/)은 이것을 전송 중이 아니라 ClickHouse 안에서 해결합니다 --
원본을 적재하고, 딕셔너리나 스필하는 `JOIN`으로 변환하고, 매핑되지 않는 것은
격리합니다. 어려운 부분은 SQL이 아닙니다. `long` → `Int64`는 직접 변환이라서,
변환되지 않은 행과 변환된 행을 구별할 수 없습니다. 캐스팅 오류도 없고 null도
없습니다. 그래서 그 디렉터리는 대부분 **케이스 매트릭스**와 잘못된 변환을 시끄럽게
만드는 검사들입니다 -- 네 개의 축에 걸친 70개 단정이고, 직접 작성한 변환에서 조용히
실패하는 두 가지(두 번 실행, 행 수 보존)를 포함합니다.

### 처음부터 끝까지 해보기

```bash
cd _base && docker compose --profile elastic up -d
./bin/seed_elasticsearch.py

cd ../labs/elastic-migration/data
./plan.py --index logs-demo --target-rows 100000 --out plan.json
./mapping_to_ddl.py --index logs-demo --table logs_demo --manifest manifest.json > ddl.sql
curl -sS http://localhost:8124/ --data-binary @ddl.sql

# one chunk at a time, resumable, with progress -- or run the three tools by hand
./run.py --plan plan.json --table logs_demo --manifest manifest.json
./run.py --plan plan.json --table logs_demo --status

# whole-table checks. No --out-dir here: check 4 compares one export's
# checkpoints against the whole index, which only holds for a single-pass
# export -- run.py already made the per-chunk version of that check, for
# every chunk.
./parity_checks.py --es-index logs-demo --ch-table logs_demo \
    --ch-url http://localhost:8124 --ch-user default --ch-password '' \
    --ch-database default
```

**Verified on:** Elasticsearch 8.17.0, ClickHouse 26.6.8.7(고정된 마이그레이션
목적지), ClickStack/HyperDX 2.39.1(`clickstack-all-in-one` -- 실행 중이지만 이
경로에는 관여하지 않음. 데이터 경로는 ClickHouse만 다룹니다) -- 시딩한 문서
300,000건, 매핑 필드 523개, 10/5/1로 분류, 4개 슬라이스로 내보내고, 적재하고,
네 개 정합성 검사 모두 통과(`total row count matches (300000)`, 시간별 버킷
251개 일치, 문서 50개 필드 샘플 일치, 4-슬라이스 커버리지에 빈 슬라이스 없음).

26.6.8.7은 ClickHouse Cloud regular release 채널이 도는 라인의 최신 공개
패치입니다. 실제 Cloud 서비스는 `26.6.1.2191`을 보고합니다 -- 같은 minor이고,
공개되지 않는 빌드입니다.

실행하지 않은 것: `_base/bin/verify.sh` (ClickStack UI에서 받는
`HYPERDX_INGESTION_KEY`가 필요한데 비대화식으로 얻을 수 없음) -- 이 실습의
데이터 경로는 HyperDX 수집이 아니라 ClickHouse만 다루므로 필요하지도
않습니다.
