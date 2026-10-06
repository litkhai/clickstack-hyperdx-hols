# labs/elastic-migration/dashboards

[English](#english) | [한국어](#한국어)

## English

Grafana dashboards that read Elasticsearch, repointed at ClickHouse — and
checked, through Grafana itself, that every converted panel shows the same
numbers. Grafana stays where it is. This is the case where dashboards have
already moved to Grafana but still query Elasticsearch, so the data migration
is not finished until every panel reads the new table.

| File | What it does |
|---|---|
| [`convert.py`](convert.py) | rewrites every Elasticsearch target of a dashboard as a SQL target on the ClickHouse data source, and classifies each one |
| [`lucene_sql.py`](lucene_sql.py) | Lucene `query_string` → ClickHouse SQL predicate. Output-agnostic: the HyperDX output ([#58](https://github.com/litkhai/clickstack-hyperdx-hols/issues/58)) reuses it |
| [`check.py`](check.py) | runs the original and the converted target through Grafana's `POST /api/ds/query` over the same window, and compares them per series and bucket |

### Try it

```bash
cd _base && docker compose --profile elastic --profile grafana up -d
./bin/seed_elasticsearch.py

# load the seed with ../data/ first -- see ../data/README.md "Try it end to end";
# keep its manifest.json, the converter reads column types from it

cd ../labs/elastic-migration/dashboards
./convert.py --dashboard fixtures/es-dashboard.json \
    --datasource-map fixtures/datasource-map.json \
    --manifest ../data/manifest.json --out out/ch-dashboard.json
./check.py --grafana http://localhost:3000 \
    --original fixtures/es-dashboard.json --converted out/ch-dashboard.json
```

`check.py` reads the Grafana login from `GRAFANA_USER` / `GRAFANA_PASSWORD`
(or `GRAFANA_ADMIN_PASSWORD`, as in `_base/.env.example`), never from the
command line. It also uploads both dashboards, so you can open them side by
side. Without `--from`/`--to` it takes a 12-hour window from the hour of the
first document (`--window-hours`). The seed is generated relative to the time
it ran, so a fixed absolute range drifts out of the data.

The data-source map names, for each Elasticsearch data source (uid or name),
the ClickHouse data source to use and the table `data/` loaded:

```json
{"es": {"uid": "ch", "table": "default.logs_demo",
        "time_column": "@timestamp", "aliases": {"level": "log.level"}}}
```

`aliases` is there because the manifest does not record Elasticsearch
aliases (see "What the manifest cannot tell it").

### Classification

Every target is exactly one of the vocabulary `data/` uses:

| Class | What happens |
|---|---|
| **converted** | a direct equivalent; the target is rewritten |
| **needs review** | rewritten, but the behaviour differs at the edges. The reason is appended to the panel's description (`[needs review] refId X: …`) and printed in the report |
| **unsupported** | **not guessed.** The panel is kept, its targets emptied, its title prefixed `[NOT CONVERTED]`, with the reason in the description |

A panel with one unsupported target is emptied **entirely**. Half a panel that
still renders is exactly the failure this lab is built against: it looks
finished. `convert.py` exits 0 whenever it wrote output — the report is the
decision, as with `mapping_to_ddl.py` — 1 when it could not run, and with
`--strict` 2 when anything is unsupported.

What it reproduces is **Grafana's Elasticsearch backend** (plugin 12.9.1), not
Elasticsearch. A `terms` without `orderBy` sorts by term, descending. A missing
or `"0"` size becomes 500. The time filter is on the data source's
`timeField`, not the target's. Series columns are named the way the plugin
names its frames (`Count`, `Average <field>`, `p95.0 <field>`), so legends
read the same.

### Lucene

The query is read the way Grafana sends it: `query_string` with
`analyze_wildcard: true`, **no `default_operator` (so OR)** and **no
`default_field` (so every field)**. It is parsed by Lucene's classic
`QueryParser` rules, not SQL precedence.

| Lucene | SQL | Class |
|---|---|---|
| `service.name:checkout` (keyword) | `` `service.name` = 'checkout' `` | converted |
| `http.response.status_code:404` (numeric) | `= 404` | converted |
| `f:"a b"` on a keyword | `= 'a b'` | converted |
| `f:[a TO b]`, `{a TO b}`, `f:>a` | `BETWEEN`, `>` / `<` (keyword ranges are bytewise, as in Lucene) | converted |
| explicit `AND` `OR` `NOT`, parentheses; implicit operator | the same, implicit = **OR** | converted |
| `f:ab*`, `f:a?c` on a keyword | `LIKE`, metacharacters escaped | converted |
| `_exists_:f`, `f:*` on a Nullable column | `isNotNull(f)` | converted |
| `_exists_:f` on a **non-Nullable** column | `` f != '' `` (the column default) | needs review |
| a term or phrase on `message` (text) | `hasToken(lowerUTF8(...))` — approximates the standard analyzer: no stemming, stop words or Unicode word breaks; a phrase loses its word order | needs review |
| `message.keyword:…` | the `message` column — Elasticsearch indexes the keyword multi-field with `ignore_above` (256 here), the column has no such cut-off | needs review |
| `a AND b OR c` | `a AND b` — in the classic parser `c` only scores when a required clause is present, so it does not widen the match | needs review |
| a bare term or phrase (no field) | — it searches every field | unsupported |
| regex `/…/`, fuzzy `~`, proximity, boost `^` | — | unsupported |
| a field the manifest does not have, a path inside a JSON column, a `nested` field | — | unsupported |

Two rows above are needs review because of how `data/` built the table, not
because of Lucene. `mapping_to_ddl.py` makes no column Nullable, so a
document that lacked a field was loaded with the column's default. After the
load, "missing" and "empty" are the same thing, and `_exists_` can only test
for the default. Where a column *is* Nullable, every leaf predicate is wrapped
in `ifNull(…, 0)`, so `NOT f:x` keeps the rows that lack `f` — Elasticsearch
keeps those documents too.

Run the translator on a single query to see what it does with it:

```bash
./lucene_sql.py --manifest ../data/manifest.json --alias level=log.level \
    'service.name:checkout AND http.response.status_code:[500 TO 599]'
```

**Measured, 2026-10-02 (a one-off differential run, not committed).** 1,500
random Lucene queries over the seed fields, each compared as Elasticsearch
`_count` against `SELECT count()` over the same 300,000 rows. The first
400-query run found a precedence bug (`(a OR b) OR NOT c` rendered as
`a OR b AND NOT c`); it is fixed and has a regression test. After the fix,
all 1,254 queries the translator converted (436 converted, 818 needs review)
gave identical counts. The 246 it refused were all queries Elasticsearch
itself rejected with HTTP 400.

### Aggregations

| Elasticsearch | ClickHouse | Class |
|---|---|---|
| `count`, `sum`, `avg`, `min`, `max` | the same, float columns cast with `toFloat64()` | converted |
| `cardinality` | `uniqExact` | needs review |
| `percentiles` | `quantileExactInclusive` on `toFloat64(col)` | needs review |
| `extended_stats` | population standard deviation (`stddevPop`), as Elasticsearch | needs review |
| `raw_data`, `raw_document`, `logs` | a table or logs query of the table's columns | needs review |
| `date_histogram` `auto` | `$__timeInterval_ms(col)` | converted |
| `date_histogram` fixed interval that divides a day (`1h`) | `toStartOfInterval` | converted |
| `5h` and other intervals that do not divide a day | the interval in **milliseconds**, which aligns to the epoch like Elasticsearch | needs review |
| calendar `1w` `1M` `1q` `1y` | `toStartOfWeek` / `toStartOfMonth` … (week starts Monday, UTC) | needs review |
| non-UTC `time_zone`, `offset`, `trimEdges` | — alignment differs | needs review |
| `terms` (size, order by `_count` / `_term` / a metric) | `ORDER BY … LIMIT`; terms → date_histogram as a top-N subquery | converted |
| a nested `auto` date_histogram under `terms` with 500 parent buckets | the same SQL, but see below | needs review |
| `filters`, `histogram` | a `filter` label per query, `floor(x / interval) * interval` | converted |
| pipeline aggregations (`derivative`, `moving_avg`, `cumulative_sum`, `bucket_script`, …), `top_metrics`, `rate`, `geohash_grid`, `nested`, `queryType` `dsl` / `esql` | — | unsupported |

What the measurements behind those classes said (2026-10-02, the 300,000-document seed):

- **Percentiles.** `quantileExactInclusive` equalled Elasticsearch's t-digest
  exactly — difference 0 — on hourly, 48-minute and 7.2-minute buckets of up
  to about 1,200 documents. With all 300,000 documents in **one** bucket, it
  differed by up to 2.5% (p5). The other candidates were worse even on the
  hourly buckets (`quantileExact` and `quantileTiming` up to 6.9e-2 at p99,
  `quantileTDigest` up to 2.0e-2).
- **Cardinality.** `uniqExact` equals HyperLogLog++ below its
  `precision_threshold` (3,000 by default). Over all 300,000 documents it was
  298,195 (Elasticsearch) against 300,000 (exact): 0.6%.
- **`5h`.** `toStartOfInterval(…, INTERVAL 5 hour)` aligns to midnight (first
  bucket 1790917200000). `INTERVAL 18000000 millisecond` aligns to the epoch,
  exactly like Elasticsearch (first bucket 1790910000000, counts 1613 / 6001 /
  5999 on both sides). The converter emits the millisecond form.
- **Grafana widens nested auto intervals.** Under a `terms` with Grafana's
  default 500 parent buckets, its Elasticsearch backend widens an `auto`
  date_histogram to stay under 65,535 buckets; the ClickHouse macro never
  does. Seen at 24 hours and 300 points: Elasticsearch buckets 900,000 ms
  apart, `intervalMs` 288,000. At 12 hours and 100 points it did not happen.

### `check.py`

For each converted target it sends the original Elasticsearch target and the
ClickHouse target in **one** `POST /api/ds/query`, with identical `from`/`to`
(whole seconds), `intervalMs` and `maxDataPoints`. It normalises both to
`{(series, bucket): value}` and compares.

| Result | Meaning |
|---|---|
| `PASS` | identical |
| `PASS~` | inside a **stated** tolerance, printed on the line — never shown as `PASS` |
| `EMPTIED` | a `[NOT CONVERTED]` panel that really has no targets |
| `MISMATCH` | anything else, including a series or a bucket on one side only, or a `[NOT CONVERTED]` panel that has a target |

Tolerances: exact for counts and integer sums. For a `Float32` column, one
float32 ulp (2^-23 relative) — see the next section for why. Cardinality
0.01 (`--cardinality-tol`), percentiles 0.05 (`--percentile-tol`). `--exact`
turns every tolerance off and shows each non-identical result.

What it normalises rather than hides:

- **Empty buckets.** Elasticsearch returns 0-count buckets (`min_doc_count` 0
  plus `extended_bounds`); the SQL returns no row. The check fills them as 0
  for counts and null otherwise, and says how many it filled (`filled N`).
- **Variables.** Dashboard variables and `$__conditionalAll` are expanded by
  the browser, not by `/api/ds/query`. Sent raw, the ClickHouse side fails
  with a syntax error at `${service:singlequote}`. The check substitutes them
  first, using the current values or `--var name=value` (`__all` for All).
- **Series names.** Elasticsearch frames carry no labels, only a generated
  name. The check gives its copy of the Elasticsearch target a canonical
  `alias`, so series can be paired.

### What the run turned up

- **ClickHouse 26.6.8.7 does not round some decimal strings correctly to
  Float32.** `toFloat32(3.64)` is 3.640000104904175, the same value
  Elasticsearch holds. `toFloat32('3.64')` — and the JSONEachRow and TSV
  inputs — give 3.6399998664855957. After the `data/` load, 993 of the 300,000
  `http.response.time_ms` values (0.33%) sat one float32 ulp away from
  Elasticsearch's. Avg and sum stayed within 1.21e-10; one `min` bucket out of
  100 differed by 6.5e-8. This is the load path in `data/`, not the
  dashboards, and it is why the float32 tier exists. `data/load.sh` has
  since avoided it (#61); this run predates the change and was not repeated,
  so the tier stays.
- **`min`, `max` and `stddevPop` on a `Float32` column return `Float32`,**
  which the plugin sends as its shortest decimal: the first run showed `Min`
  1.1800001 against Elasticsearch's 1.1799999475479126. The converter casts
  float columns with `toFloat64()`.
- **ClickHouse plugin 4.22.0, zero rows:** HTTP 200 with a frame that has no
  fields — not an error. **`format: 4` (multi)** with a string key returned
  HTTP 400 (`frame 0 is missing long type indicator`). `format: 0` with a
  `toString()`ed key works, so that is what the converter emits. A numeric
  group key that is not `toString()`ed stays a value column.

### What the manifest cannot tell it

`mapping_to_ddl.py --manifest` records each field's path, ClickHouse type and
status — not its Elasticsearch type, not alias targets. So the converter
reads `LowCardinality(String)` as a keyword and a plain `String` as analyzed
text. A `wildcard`, `match_only_text` or `ip_range` field would also be
treated as text, which is the conservative direction. Aliases come from the
data-source map.

### Verified on

**2026-10-02**, Grafana **13.2.3** (`grafana/grafana`, commit `90ffed0`),
`elasticsearch` data source **12.9.1**, `grafana-clickhouse-datasource`
**4.22.0**, Elasticsearch **8.17.0** (Lucene 9.12.0), ClickHouse **26.6.8.7**
(`clickhouse-target`, UTC), ClickStack/HyperDX **2.39.1** running but not on
this path (its bundled ClickHouse is 26.8.7.19), Python 3.9.6.

- The 300,000-document seed loaded with `data/`: 4/4 chunks verified, parity
  checks passing.
- `test_lucene_sql.py` and `test_convert.py`: 86 tests, all passing.
- `check.py` on `fixtures/es-dashboard.json` (40 panels, 50 targets, one per
  row of the tables above): **32 PASS, 4 PASS~, 14 EMPTIED, 0 MISMATCH.**
  Converter report: 23 converted, 14 needs review, 14 unsupported.
- Faults, each reported as `MISMATCH` with a non-zero exit:
  - one filter value changed in a converted query
  - a range bound narrowed from 599 to 502
  - a top-N `LIMIT 5` changed to `LIMIT 4`
  - a target added back to a `[NOT CONVERTED]` panel
  - the converter patched so a bare term matches everything

Not run: a non-UTC `time_zone`, `offset` and `trimEdges` against
Elasticsearch; calendar `1M` / `1q` / `1y` (only `1w` ran, over one week);
`terms` `missing` and `min_doc_count` variants; a Grafana restart with the
plugins already in the volume; Python 3.8 (syntax checked only).

### Not covered

- **Alerts**: the same query conversion, not built — open an issue if you
  need it.
- **Kibana** saved objects: [#5](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5).
- **HyperDX** tiles: [#58](https://github.com/litkhai/clickstack-hyperdx-hols/issues/58).
- **Other Elasticsearch queries in a dashboard**: annotation and variable
  queries are reported and left untouched.

---

## 한국어

Elasticsearch를 읽는 Grafana 대시보드를 ClickHouse로 바꾸고, 바꾼 패널마다 같은
숫자가 나오는지 Grafana 자신을 통해 확인합니다. Grafana는 그대로 둡니다. 대시보드는
이미 Grafana로 옮겼지만 아직 Elasticsearch를 조회하는 경우입니다. 모든 패널이 새
테이블을 읽기 전까지 데이터 이전은 끝난 것이 아닙니다.

| 파일 | 하는 일 |
|---|---|
| [`convert.py`](convert.py) | 대시보드의 Elasticsearch 타깃을 모두 ClickHouse 데이터 소스의 SQL 타깃으로 바꾸고, 하나씩 분류합니다 |
| [`lucene_sql.py`](lucene_sql.py) | Lucene `query_string` → ClickHouse SQL 조건. 출력과 무관해서 HyperDX 출력([#58](https://github.com/litkhai/clickstack-hyperdx-hols/issues/58))이 그대로 씁니다 |
| [`check.py`](check.py) | 원래 타깃과 바꾼 타깃을 같은 시간 범위로 Grafana `POST /api/ds/query`에 돌려, 시리즈·버킷마다 비교합니다 |

### 실행해 보기

```bash
cd _base && docker compose --profile elastic --profile grafana up -d
./bin/seed_elasticsearch.py

# 먼저 ../data/로 시드를 적재합니다 -- ../data/README.md "처음부터 끝까지 해보기" 참고.
# manifest.json을 남겨 두세요. 변환기가 컬럼 타입을 거기서 읽습니다

cd ../labs/elastic-migration/dashboards
./convert.py --dashboard fixtures/es-dashboard.json \
    --datasource-map fixtures/datasource-map.json \
    --manifest ../data/manifest.json --out out/ch-dashboard.json
./check.py --grafana http://localhost:3000 \
    --original fixtures/es-dashboard.json --converted out/ch-dashboard.json
```

`check.py`는 Grafana 로그인을 명령줄이 아니라 `GRAFANA_USER` / `GRAFANA_PASSWORD`
(또는 `_base/.env.example`의 `GRAFANA_ADMIN_PASSWORD`)에서 읽습니다. 두 대시보드를
업로드도 하므로 나란히 열어 볼 수 있습니다. `--from`/`--to`가 없으면 첫 문서가 있는
시각부터 12시간(`--window-hours`)을 씁니다. 시드는 실행한 시각을 기준으로 만들어지므로,
고정된 절대 범위는 데이터에서 벗어납니다.

데이터 소스 맵은 Elasticsearch 데이터 소스(uid 또는 이름)마다, 쓸 ClickHouse 데이터
소스와 `data/`가 적재한 테이블을 적습니다.

```json
{"es": {"uid": "ch", "table": "default.logs_demo",
        "time_column": "@timestamp", "aliases": {"level": "log.level"}}}
```

`aliases`가 있는 이유는 manifest에 Elasticsearch alias가 기록되지 않기 때문입니다
("manifest가 알려 주지 못하는 것" 참고).

### 분류

모든 타깃은 `data/`와 같은 용어로 정확히 하나입니다.

| 분류 | 처리 |
|---|---|
| **converted** | 직접 대응하는 것이 있어 타깃을 바꿉니다 |
| **needs review** | 바꾸지만 가장자리에서 동작이 다릅니다. 이유를 패널 설명에 덧붙이고(`[needs review] refId X: …`) 보고서에도 출력합니다 |
| **unsupported** | **추측하지 않습니다.** 패널은 남기되 타깃을 비우고, 제목 앞에 `[NOT CONVERTED]`를 붙이고, 이유를 설명에 적습니다 |

unsupported 타깃이 하나라도 있는 패널은 **통째로** 비웁니다. 그래도 그려지는 반쪽
패널이 바로 이 랩이 막으려는 실패입니다. 끝난 것처럼 보이기 때문입니다. `convert.py`는
결과를 썼으면 0으로 끝납니다(판단은 종료 코드가 아니라 보고서가 합니다.
`mapping_to_ddl.py`와 같습니다). 실행할 수 없었으면 1, `--strict`를 주면 unsupported가
있을 때 2입니다.

재현하는 것은 Elasticsearch가 아니라 **Grafana의 Elasticsearch 백엔드**(플러그인
12.9.1)입니다. `orderBy`가 없는 `terms`는 키 내림차순으로 정렬합니다. size가 없거나
`"0"`이면 500입니다. 시간 필터는 타깃이 아니라 데이터 소스의 `timeField`에
걸립니다. 시리즈 컬럼 이름은 플러그인이 frame에 붙이는 이름(`Count`,
`Average <field>`, `p95.0 <field>`)과 같게 해서 범례가 똑같이 읽힙니다.

### Lucene

쿼리는 Grafana가 보내는 방식대로 읽습니다. `query_string`, `analyze_wildcard: true`,
**`default_operator` 없음(그래서 OR)**, **`default_field` 없음(그래서 모든 필드)**.
SQL 우선순위가 아니라 Lucene 고전 `QueryParser` 규칙으로 파싱합니다.

| Lucene | SQL | 분류 |
|---|---|---|
| `service.name:checkout` (keyword) | `` `service.name` = 'checkout' `` | converted |
| `http.response.status_code:404` (숫자) | `= 404` | converted |
| keyword의 `f:"a b"` | `= 'a b'` | converted |
| `f:[a TO b]`, `{a TO b}`, `f:>a` | `BETWEEN`, `>` / `<` (keyword 범위는 Lucene처럼 바이트 비교) | converted |
| 명시적 `AND` `OR` `NOT`, 괄호, 생략된 연산자 | 같음. 생략된 연산자는 **OR** | converted |
| keyword의 `f:ab*`, `f:a?c` | `LIKE`, 메타 문자는 이스케이프 | converted |
| Nullable 컬럼의 `_exists_:f`, `f:*` | `isNotNull(f)` | converted |
| **Nullable이 아닌** 컬럼의 `_exists_:f` | `` f != '' `` (컬럼 기본값) | needs review |
| `message`(text)의 항·구 | `hasToken(lowerUTF8(...))` — 표준 analyzer의 근사입니다. 어간 추출·불용어·유니코드 단어 경계가 없고, 구는 단어 순서를 잃습니다 | needs review |
| `message.keyword:…` | `message` 컬럼 — Elasticsearch는 keyword 다중 필드를 `ignore_above`(여기서는 256)로 색인하지만 컬럼에는 그런 상한이 없습니다 | needs review |
| `a AND b OR c` | `a AND b` — 고전 파서에서 필수 절이 있으면 `c`는 점수에만 쓰이고 일치 범위를 넓히지 않습니다 | needs review |
| 필드 없는 항·구 | — 모든 필드를 찾습니다 | unsupported |
| 정규식 `/…/`, 퍼지 `~`, 근접, 부스트 `^` | — | unsupported |
| manifest에 없는 필드, JSON 컬럼 안의 경로, `nested` 필드 | — | unsupported |

위 표의 두 줄은 Lucene 때문이 아니라 `data/`가 테이블을 만든 방식 때문에 needs
review입니다. `mapping_to_ddl.py`는 어떤 컬럼도 Nullable로 만들지 않아서, 필드가 없던
문서는 컬럼 기본값으로 적재됐습니다. 적재 뒤에는 "없음"과 "빈 값"이 같아지고,
`_exists_`는 기본값인지만 확인할 수 있습니다. 컬럼이 Nullable*인* 경우에는 모든 단말
조건을 `ifNull(…, 0)`으로 감싸서, `NOT f:x`가 `f`가 없는 행을 남깁니다.
Elasticsearch도 그런 문서를 남깁니다.

쿼리 하나에 무엇을 하는지는 변환기를 직접 돌려 보면 됩니다.

```bash
./lucene_sql.py --manifest ../data/manifest.json --alias level=log.level \
    'service.name:checkout AND http.response.status_code:[500 TO 599]'
```

**측정, 2026-10-02 (일회성 차등 실행, 커밋하지 않음).** 시드 필드에 대한 무작위
Lucene 쿼리 1,500개를, 같은 300,000행에서 Elasticsearch `_count`와
`SELECT count()`로 하나씩 비교했습니다. 첫 400개 실행에서 우선순위 버그를 찾았습니다
(`(a OR b) OR NOT c`가 `a OR b AND NOT c`로 나옴). 고쳤고 회귀 테스트가 있습니다.
고친 뒤 변환기가 바꾼 1,254개(converted 436, needs review 818)는 모두 개수가
같았습니다. 거부한 246개는 모두 Elasticsearch도 HTTP 400으로 거부한 쿼리였습니다.

### 집계

| Elasticsearch | ClickHouse | 분류 |
|---|---|---|
| `count`, `sum`, `avg`, `min`, `max` | 같음. float 컬럼은 `toFloat64()`로 변환 | converted |
| `cardinality` | `uniqExact` | needs review |
| `percentiles` | `toFloat64(col)`의 `quantileExactInclusive` | needs review |
| `extended_stats` | Elasticsearch처럼 모표준편차(`stddevPop`) | needs review |
| `raw_data`, `raw_document`, `logs` | 테이블 컬럼의 table·logs 쿼리 | needs review |
| `date_histogram` `auto` | `$__timeInterval_ms(col)` | converted |
| 하루를 나누는 고정 간격(`1h`) | `toStartOfInterval` | converted |
| `5h`처럼 하루를 나누지 않는 간격 | **밀리초** 단위 간격. Elasticsearch처럼 epoch에 맞춰집니다 | needs review |
| 달력 간격 `1w` `1M` `1q` `1y` | `toStartOfWeek` / `toStartOfMonth` … (주는 월요일 시작, UTC) | needs review |
| UTC가 아닌 `time_zone`, `offset`, `trimEdges` | — 정렬 기준이 다릅니다 | needs review |
| `terms` (size, `_count` / `_term` / 지표 정렬) | `ORDER BY … LIMIT`. terms → date_histogram은 top-N 하위 쿼리 | converted |
| 부모 버킷 500개인 `terms` 아래의 `auto` date_histogram | 같은 SQL. 아래 참고 | needs review |
| `filters`, `histogram` | 쿼리마다 `filter` 라벨, `floor(x / interval) * interval` | converted |
| 파이프라인 집계(`derivative`, `moving_avg`, `cumulative_sum`, `bucket_script` …), `top_metrics`, `rate`, `geohash_grid`, `nested`, `queryType` `dsl` / `esql` | — | unsupported |

분류의 근거가 된 측정(2026-10-02, 문서 300,000건 시드):

- **percentiles.** 문서가 최대 약 1,200건인 1시간·48분·7.2분 버킷에서
  `quantileExactInclusive`는 Elasticsearch의 t-digest와 정확히 같았습니다(차이 0).
  300,000건을 **한** 버킷에 넣으면 최대 2.5%(p5) 달랐습니다. 다른 후보는 1시간
  버킷에서도 더 나빴습니다(`quantileExact`·`quantileTiming`은 p99에서 최대 6.9e-2,
  `quantileTDigest`는 최대 2.0e-2).
- **cardinality.** `precision_threshold`(기본 3,000) 아래에서는 `uniqExact`가
  HyperLogLog++와 같습니다. 300,000건 전체에서는 298,195(Elasticsearch) 대
  300,000(정확): 0.6%.
- **`5h`.** `toStartOfInterval(…, INTERVAL 5 hour)`는 자정 기준으로 맞춥니다(첫
  버킷 1790917200000). `INTERVAL 18000000 millisecond`는 Elasticsearch와 똑같이
  epoch 기준입니다(첫 버킷 1790910000000, 양쪽 개수 1613 / 6001 / 5999). 변환기는
  밀리초 형식을 씁니다.
- **Grafana는 중첩된 auto 간격을 넓힙니다.** Grafana 기본값인 부모 버킷 500개의
  `terms` 아래에서, Elasticsearch 백엔드는 버킷 65,535개 아래로 맞추려고 `auto`
  date_histogram을 넓힙니다. ClickHouse 매크로는 넓히지 않습니다. 24시간·300포인트에서
  관찰: Elasticsearch 버킷 간격 900,000 ms, `intervalMs` 288,000. 12시간·100포인트에서는
  일어나지 않았습니다.

### `check.py`

바꾼 타깃마다 원래 Elasticsearch 타깃과 ClickHouse 타깃을 **한** `POST /api/ds/query`로
보냅니다. `from`/`to`(초 단위), `intervalMs`, `maxDataPoints`는 같습니다. 둘을
`{(시리즈, 버킷): 값}`으로 정규화해 비교합니다.

| 결과 | 뜻 |
|---|---|
| `PASS` | 같음 |
| `PASS~` | 그 줄에 적힌, **밝혀 둔** 허용 오차 안 — `PASS`로 보이지 않습니다 |
| `EMPTIED` | 실제로 타깃이 없는 `[NOT CONVERTED]` 패널 |
| `MISMATCH` | 나머지 전부. 한쪽에만 있는 시리즈·버킷, 타깃이 남아 있는 `[NOT CONVERTED]` 패널 포함 |

허용 오차: 개수와 정수 합은 정확히 같아야 합니다. `Float32` 컬럼은 float32 ulp
하나(상대 2^-23) — 이유는 다음 절에 있습니다. cardinality 0.01(`--cardinality-tol`),
percentiles 0.05(`--percentile-tol`). `--exact`는 모든 허용 오차를 끄고 같지 않은
결과를 모두 보여 줍니다.

숨기지 않고 정규화하는 것:

- **빈 버킷.** Elasticsearch는 개수 0인 버킷을 돌려줍니다(`min_doc_count` 0과
  `extended_bounds`). SQL은 행을 돌려주지 않습니다. 검사는 개수면 0, 아니면 null로
  채우고, 몇 개를 채웠는지(`filled N`) 적습니다.
- **변수.** 대시보드 변수와 `$__conditionalAll`은 `/api/ds/query`가 아니라 브라우저가
  펼칩니다. 그대로 보내면 ClickHouse 쪽이 `${service:singlequote}`에서 구문 오류를
  냅니다. 검사는 현재 값이나 `--var name=value`(All은 `__all`)로 먼저 치환합니다.
- **시리즈 이름.** Elasticsearch frame에는 라벨이 없고 만들어진 이름만 있습니다.
  검사는 Elasticsearch 타깃의 사본에 정해진 `alias`를 붙여 시리즈를 짝짓습니다.

### 실행에서 드러난 것

- **ClickHouse 26.6.8.7은 일부 십진 문자열을 Float32로 올바르게 반올림하지 않습니다.**
  `toFloat32(3.64)`는 Elasticsearch가 가진 값과 같은 3.640000104904175입니다.
  `toFloat32('3.64')`와 JSONEachRow·TSV 입력은 3.6399998664855957입니다. `data/`로
  적재한 뒤 `http.response.time_ms` 300,000개 중 993개(0.33%)가 Elasticsearch 값과
  float32 ulp 하나만큼 달랐습니다. avg와 sum은 1.21e-10 안이었고, `min` 버킷 100개 중
  하나가 6.5e-8 달랐습니다. 대시보드가 아니라 `data/`의 적재 경로 문제이고, float32
  허용 단계가 있는 이유입니다. 그 뒤 `data/load.sh`가 이 문제를 피하도록
  바뀌었습니다(#61). 이 실행은 그 전의 것이고 다시 돌리지 않았으므로 허용 단계는
  그대로 둡니다.
- **`Float32` 컬럼의 `min`, `max`, `stddevPop`은 `Float32`를 돌려주고,** 플러그인은
  그것을 가장 짧은 십진수로 보냅니다. 첫 실행에서 `Min`이 1.1800001, Elasticsearch는
  1.1799999475479126이었습니다. 변환기는 float 컬럼을 `toFloat64()`로 바꿉니다.
- **ClickHouse 플러그인 4.22.0, 행이 0개일 때:** 오류가 아니라 필드 없는 frame과 함께
  HTTP 200. 문자열 키가 있는 **`format: 4`(multi)**는 HTTP 400
  (`frame 0 is missing long type indicator`). `toString()`한 키와 `format: 0`은
  동작하므로 변환기는 그렇게 씁니다. `toString()`하지 않은 숫자 그룹 키는 값 컬럼으로
  남습니다.

### manifest가 알려 주지 못하는 것

`mapping_to_ddl.py --manifest`는 필드마다 경로, ClickHouse 타입, 상태를 기록합니다.
Elasticsearch 타입과 alias 대상은 기록하지 않습니다. 그래서 변환기는
`LowCardinality(String)`을 keyword로, 그냥 `String`을 분석된 text로 읽습니다.
`wildcard`, `match_only_text`, `ip_range` 필드도 text로 다뤄지는데, 보수적인 쪽입니다.
alias는 데이터 소스 맵에서 옵니다.

### 검증 환경

**2026-10-02**, Grafana **13.2.3** (`grafana/grafana`, commit `90ffed0`),
`elasticsearch` 데이터 소스 **12.9.1**, `grafana-clickhouse-datasource` **4.22.0**,
Elasticsearch **8.17.0** (Lucene 9.12.0), ClickHouse **26.6.8.7**
(`clickhouse-target`, UTC), ClickStack/HyperDX **2.39.1**은 실행 중이지만 이 경로에
없음(번들 ClickHouse 26.8.7.19), Python 3.9.6.

- 문서 300,000건 시드를 `data/`로 적재: 청크 4/4 verified, parity 검사 통과.
- `test_lucene_sql.py`, `test_convert.py`: 테스트 86개 모두 통과.
- `fixtures/es-dashboard.json`(패널 40개, 타깃 50개, 위 표의 줄마다 하나)에 대한
  `check.py`: **PASS 32, PASS~ 4, EMPTIED 14, MISMATCH 0.** 변환기 보고: converted 23,
  needs review 14, unsupported 14.
- 고장 주입. 각각 `MISMATCH`와 0이 아닌 종료 코드로 보고됐습니다.
  - 바꾼 쿼리 하나의 필터 값 변경
  - 범위 상한을 599에서 502로 좁힘
  - top-N `LIMIT 5`를 `LIMIT 4`로 변경
  - `[NOT CONVERTED]` 패널에 타깃을 다시 넣음
  - 필드 없는 항이 모든 것과 일치하도록 변환기를 패치

실행하지 않은 것: Elasticsearch에 대한 UTC가 아닌 `time_zone`, `offset`,
`trimEdges`. 달력 간격 `1M` / `1q` / `1y`(`1w`만 한 주 범위에서 실행). `terms`의
`missing`과 `min_doc_count` 변형. 플러그인이 볼륨에 이미 있는 상태의 Grafana 재시작.
Python 3.8(구문만 확인).

### 다루지 않는 것

- **알림**: 같은 쿼리 변환이지만 만들지 않았습니다. 필요하면 이슈를 열어 주세요.
- **Kibana** saved object: [#5](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5).
- **HyperDX** 타일: [#58](https://github.com/litkhai/clickstack-hyperdx-hols/issues/58).
- **대시보드 안의 다른 Elasticsearch 쿼리**: 주석(annotation)과 변수 쿼리는 보고만
  하고 그대로 둡니다.
