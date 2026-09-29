# Elastic migration: data

[English](#english) | [한국어](#한국어)

## English

Tools for the data part of #19: `_mapping` to ClickHouse DDL, a parallel
export that resumes, and parity checks that are query pairs rather than a UI
screenshot. The [official documentation for this
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

### Try it end to end

```bash
cd _base && docker compose --profile elastic up -d
./bin/seed_elasticsearch.py

cd ../labs/elastic-migration/data
./mapping_to_ddl.py --index logs-demo --table logs_demo --manifest manifest.json > ddl.sql
curl -sS http://localhost:8124/ --data-binary @ddl.sql

./export.py --index logs-demo --out-dir out/logs-demo --manifest manifest.json
./load.sh --out-dir out/logs-demo --table logs_demo
./parity_checks.py --es-index logs-demo --ch-table logs_demo \
    --ch-url http://localhost:8124 --ch-user default --ch-password '' \
    --ch-database default --out-dir out/logs-demo
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

#19의 데이터 부분을 위한 도구들입니다: `_mapping`을 ClickHouse DDL로 바꾸는
변환기, 재개 가능한 병렬 내보내기, 그리고 UI 스크린샷이 아니라 쿼리 쌍으로
하는 정합성 검증. [이 마이그레이션의 공식
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

### 처음부터 끝까지 해보기

```bash
cd _base && docker compose --profile elastic up -d
./bin/seed_elasticsearch.py

cd ../labs/elastic-migration/data
./mapping_to_ddl.py --index logs-demo --table logs_demo --manifest manifest.json > ddl.sql
curl -sS http://localhost:8124/ --data-binary @ddl.sql

./export.py --index logs-demo --out-dir out/logs-demo --manifest manifest.json
./load.sh --out-dir out/logs-demo --table logs_demo
./parity_checks.py --es-index logs-demo --ch-table logs_demo \
    --ch-url http://localhost:8124 --ch-user default --ch-password '' \
    --ch-database default --out-dir out/logs-demo
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
