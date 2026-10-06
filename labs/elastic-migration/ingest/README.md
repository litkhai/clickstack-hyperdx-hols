# labs/elastic-migration/ingest

[English](#english) | [한국어](#한국어)

## English

Elasticsearch ingest pipelines turned into a collector fragment for
ClickStack — and checked line by line: the same input through Elasticsearch's
own `_simulate` and through ClickStack's own collector into `otel_logs`, then
compared field by field by SQL.

This part is only for a migration whose processing lives **in** Elasticsearch
(`GET _ingest/pipeline`). A team that ran its own pipeline into Elasticsearch
has nothing to convert here. Filebeat processors and Logstash filters are
[#59](https://github.com/litkhai/clickstack-hyperdx-hols/issues/59); they will
parse into the same step list.

| File | What it does |
|---|---|
| [`convert.py`](convert.py) | one ingest pipeline → a profile-shaped directory (`custom.config.yaml` and the files [`otel-profiles/CONVENTIONS.md`](../../../otel-profiles/CONVENTIONS.md) rule 9 asks for), with a classification report on stderr |
| [`steps.py`](steps.py) | the source-neutral step list: every processor with its class, its `if`, its failure handling. #59 parses into it |
| [`grok.py`](grok.py) | grok definitions resolved recursively from the cluster, and the RE2 rewrite and refusals |
| [`check.py`](check.py) | the line-by-line check against `_simulate` and the collector |
| [`validate.sh`](validate.sh) | compiles every generated fragment with the collector in ClickStack's own image |

### Try it

```bash
cd _base && docker compose --profile elastic up -d     # Elasticsearch 8.17.0 + ClickStack

cd ../labs/elastic-migration/ingest
./convert.py --pipelines fixtures/pipelines.json --id access-log --name access-log \
    --include '/ingest-verify/in/access-log.*.log' --out-dir out/access-log
../../../otel-profiles/bin/lint.sh out/access-log
./validate.sh out/access-log
./check.py --install-nested --restore     # every fixture pipeline; recreates ClickStack twice
```

Against your own cluster, `--url` (plus `ES_USER` / `ES_PASSWORD` or
`ES_API_KEY`, `ES_CA_CERT` — the same client as [`../data/`](../data/))
replaces `--pipelines`, and nested `pipeline` processors are fetched too.
Grok definitions are read from the cluster's own
`GET _ingest/processor/grok`. Offline, save that response once and pass
`--grok-patterns`:

```bash
curl -s "$ES_URL/_ingest/processor/grok" > grok-patterns.json
```

`check.py` loads the generated fragments into ClickStack through
`_base/docker-compose.ingest-verify.yml` and recreates the container, because
a bind-mounted file that changed is not noticed. `--restore` puts it back
without the override when it is done. `--install-nested` puts the pipelines a
`pipeline` processor calls by name on the cluster, since `_simulate` resolves
them there.

### What the fragment looks like

A `filelog/<name>` receiver on your `--include` glob feeds a named pipeline:

```
logs/<name>: [memory_limiter, transform/<name>, transform, batch] -> [clickhouse]
```

- `memory_limiter` first and `batch` last, as rule 3 requires.
- ClickStack's own `transform` is included, as rule 3 also requires. It
  runs **after** the fragment, so it promotes a `level` the fragment parsed
  into `severity_text`. A `drop` adds `filter/<name>` after `transform/<name>`.
- `otel-profiles/bin/lint.sh` passes on every generated directory. It now
  takes a path as well as a profile name: an argument containing `/` is a
  directory.

**Field mapping** — defined once, in `convert.py`'s docstring:

| Elasticsearch | Collector |
|---|---|
| `message` | the log body |
| `@timestamp` | the record time |
| every other field | `log.attributes["<dotted.path>"]`, flat |

### Classification

Every processor is exactly one class. The report says why for each step.

| Class | What is emitted | Processors |
|---|---|---|
| converted | OTTL statements | set, remove, rename, append, lowercase, uppercase, drop, uri_parts, network_direction |
| needs review | statements, with the reason as a comment above them | grok, dissect, date, json, kv, convert, csv, gsub, split, trim, sort, user_agent, html_strip, pipeline, dot_expander |
| unsupported | **nothing executable** — only `# UNSUPPORTED <processor>: <reason>` | script, enrich, geoip, foreach, bytes, urldecode, registered_domain, join, fail, terminate, inference, set_security_user, date_index_name, reroute, circle, geo_grid, community_id, redact, fingerprint |

Three processors on the design's needs-review list stay unsupported, each
for a reason checked on 2026-10-06 (#64):
- `community_id`: `CommunityID()` exists in the bundled collector, but its
  protocol set and arguments differ from Elasticsearch's.
- `redact`: needs a platinum or enterprise license. A basic-license cluster
  fails it in `_simulate` too, so there was nothing to compare against.
- `fingerprint`: Elasticsearch emits the base64 of the raw digest. OTTL's
  hashes return hex, and no converter turns hex back into bytes.

- **An `if` condition** (Painless) becomes an OTTL `where` only for a
  whitelist of shapes: `ctx.a.b == 'x'`, `ctx.a?.b != null`,
  `.contains('x')`, `=~ /re/` when RE2 can compile it, and `&&` / `||` / `!`
  of those. Anything else is not guessed. The step is needs review and its
  statements are emitted with `where false`, so they cannot run, with the
  Painless text in the reason.
- **`ignore_failure`** becomes `error_mode: ignore` and a note in the
  report.
- **`on_failure`** is translated only for `grok` and `dissect`, where a
  failure can be seen: the extraction leaves `log.cache` empty. Their handlers
  (`set`, `remove`, `rename`, `append`) run behind `Len(log.cache) == 0`, that
  is, when nothing matched. Elasticsearch also runs them for other failures.
  Every other `on_failure` is not translated, and its step is needs review.
- **`network_direction`** expands Elasticsearch's named ranges (`private`,
  `public`, `loopback` …) to CIDRs for `IsInCIDR`, taken from its 8.17.0
  source. `public` and `unicast` are complements, so they become
  `not IsInCIDR(…)`. `internal_networks_field` is unsupported.
- **Times** are parsed in UTC unless the processor names a zone. The
  collector's `time_parser` defaults to `Local`; Elasticsearch defaults to
  UTC. Java date patterns are translated only where the translation is
  exact. `UNIX`, `UNIX_MS` and `TAI64N` are reported as not translated.

### grok — what RE2 cannot compile

Elasticsearch 8.17.0's default pattern set (`ecs_compatibility: disabled`, the
legacy names: `clientip`, `bytes` …) is not RE2:

- 129 of its 318 patterns use lookbehind, lookahead or atomic groups,
  including `IPV4`, `BASE10NUM`, `TIME`, `YEAR` and `QUOTEDSTRING`. So
  `%{COMBINEDAPACHELOG}` does not compile as it stands.
- **A definition that RE2 cannot compile stops the whole collector from
  starting**, not just the statement that uses it.

So `grok.py` makes two rewrites, and only two: `(?>X)` becomes `(?:X)`, and
lookarounds are removed. Both make matches **wider**. Every definition it
touched is named in the step's reasons, which makes the step needs review. It
refuses outright, so the step becomes unsupported, on:

- backreferences
- possessive quantifiers
- Oniguruma-only escapes (`\h` `\k` `\G` `\Z` `\R` `\X` `\K`) — only `RUUID`
  in the legacy set
- nested counted repeats past RE2's limit of 1000

That last one is why the **ECS-v1** `%{COMBINEDAPACHELOG}` is unsupported:
`EMAILLOCALPART` nests `{1,62}` inside `{0,63}`, which is 3,906 repeats.

`namedCapturesOnly` is always `true`. The definitions are passed to
`ExtractGrokPatterns` as a list of `"NAME=definition"` strings — runtime
rejected both a map and `"NAME definition"`. A multi-pattern `patterns: […]`
becomes guarded statements, where the first match wins, as in Elasticsearch.
A no-match returns an empty map and the record goes on, where Elasticsearch
fails the document. That is needs review, and `check.py` shows it.

The grok pattern files are not in this repository. Elasticsearch 8.17.0 is
not Apache-2.0: the distribution is Elastic License 2.0, and the grok jar
carries AGPL v3. So the converter reads the definitions from your cluster at
run time. The tests use a small pattern set written for them.

### `check.py`

For each pipeline and its sample lines:

1. **Elasticsearch.** `POST _ingest/pipeline/_simulate?verbose=true` with the
   pipeline inline. Each line gives a `_source`, a failure or a drop, plus
   per-processor statuses.
2. **Collector.** The lines are written to a uniquely named file in the
   mounted input directory, and the fragments are merged into one config —
   about 25 s for 15 pipelines. ClickStack is recreated, and the rows are
   read back **by SQL** from `otel_logs`, selected by
   `LogAttributes['log.file.name']`.
3. **Compare.** The Elasticsearch `_source` is flattened to dotted keys and
   stringified the way the exporter writes attributes, then compared with
   `Body`, `LogAttributes`, `SeverityText` and `Timestamp`.

| Result | Meaning |
|---|---|
| `PASS` | every field equal; extras only where ClickStack's `transform` or `filelog` explains them |
| `REVIEW` | a difference, explained by a needs-review step named on the line, with its reason |
| `UNSUPPORTED` | a difference explained by an unsupported step |
| `MISMATCH` | anything else — a converted step's field wrong or missing, an extra field nothing explains, a row that never arrived, Elasticsearch dropping a line the collector kept |

Extras are attributed only to what ClickStack's own `transform` actually does,
read from the running image (`/etc/otelcol-contrib/config.yaml`): it
JSON-parses a `{…}` body into attributes, promotes `level` / `severity`,
infers severity from the body and lowercases it, and **flattens maps and
arrays**. An Elasticsearch array therefore arrives as `tags.0`, `tags.1`, not
as compact JSON. `log.file.name` comes from `filelog`.

### What the run turned up

- **filelog trims leading and trailing whitespace by default**;
  Elasticsearch's `message` keeps it. The generated receiver sets
  `preserve_leading_whitespaces` and `preserve_trailing_whitespaces`.
- **filelog recognises a file by its first bytes**, so a new file with the same
  content as one it already read is taken for the old one and never read.
  `check.py` therefore:
  - clears the input directory before each run;
  - refuses two identical lines in one pipeline;
  - refuses include globs that overlap (`access-log-*` also matches
    `access-log-ecs-…`).
- **A file created after the receiver's first poll is read from its start**,
  even with `start_at: end`. That is how the check reads files without
  changing the generated config.
- **An OTTL float literal with an exponent (`1e21`) also crashes the collector
  at load** — the lexer cannot read it. Floats are printed without one.
- **`ParseJSON` makes numbers float64**: `9007199254740993` arrives as
  `9007199254740992`. Elasticsearch fails a document with an integer past
  `Long`.
- **ClickStack's `transform` re-parses a JSON body after the fragment** and
  upserts its keys, so a key the fragment changed after parsing it is
  overwritten. The `json-reparse` fixture shows it: Elasticsearch keeps
  `user: overridden`, ClickStack has `alice`. The converter therefore marks
  every step that writes a field after a `json` step on `message` as needs
  review, which moves steps in `app-json-root` and `app-json-nested` into
  that class.
- **`IsInCIDR` returns false for a string that is not an IP**, with no error.
  `source.ip=not-an-ip` gives `network.direction: external`, where
  Elasticsearch fails the document. A missing IP with `ignore_missing: false`
  leaves the field absent, where Elasticsearch fails; the step is then needs
  review. `::ffff:10.0.0.1` is IPv4 to Java, and the collector agrees.
- **`UserAgent()`** returns `user_agent.name`, `version`, `original` and
  `os.name` / `os.version` only — no `os.full`, no `device.name` — and its
  parser tables are not Elasticsearch's.
- **`geoip` without a database does not fail in Elasticsearch**; it tags the
  document `_geoip_database_unavailable_GeoLite2-City.mmdb`.
- **`otelcontribcol validate` on a fragment alone fails** (`no exporter
  configuration specified`), because the base components live in the image's
  config and in what the HyperDX API sends over OpAMP. `validate.sh`
  validates the fragment merged over the image's own config plus a stub for
  the OpAMP part. It compiles every OTTL statement and grok pattern; it does
  not run them. `check.py` runs them.

### Verified on

Elasticsearch **8.17.0** (Lucene 9.12.0), ClickStack/HyperDX
**2.39.1** (`clickhouse/clickstack-all-in-one:2.39.1`), collector
**otelcol-hyperdx 0.155.0** (contrib v0.155.0), ClickHouse **26.8.7.19** (the
one bundled in ClickStack, which `otel_logs` lives in), Python 3.9.6.

- **2026-10-06** (#64), same versions: 21 fixture pipelines, 20 checked over
  75 sample lines: **51 PASS, 17 REVIEW, 7 UNSUPPORTED, 0 MISMATCH**. The 50
  earlier lines kept their counts. ClickStack's ClickHouse ports were moved
  to 18123 / 19000 through a temporary compose override, because another
  local stack held 8123 and 9000; nothing else differed. `test_*.py`: 116
  tests, all passing. `lint.sh` and `validate.sh` pass on all 21.
- 2026-10-02: 16 fixture pipelines in `GET _ingest/pipeline` shape, 15
  checked over 50 sample lines (`nested-child` runs inside `nested-parent`):
  **29 PASS, 14 REVIEW, 7 UNSUPPORTED, 0 MISMATCH**, re-run by the lead with
  the same result. `test_*.py`: 91 tests. `lint.sh` passes on all 16
  generated directories, and its no-argument output is identical before and
  after the path change. `validate.sh` passes on all 16.
- Faults, each caught:

| Fault | Caught by |
|---|---|
| a rename target edited in a generated fragment | `check.py`: `MISMATCH` on 4 lines, missing field plus unexplained extra |
| a set value edited in the `uri` fragment (lead) | `check.py`: `MISMATCH` on 3 lines |
| a converted processor patched to emit nothing | a unit test |
| a bare `logs:` pipeline | `lint.sh` |
| a misspelled OTTL function | `validate.sh` |
| `outbound` and `inbound` swapped in the `netdir` fragment (#64) | `check.py`: `MISMATCH` on 5 lines |
| an `on_failure` value edited, `grok-onfail` and `dissect-onfail` (#64) | `check.py`: `MISMATCH` on the no-match line of each |
| the re-parse note disabled in `convert.py` (#64) | `check.py`: `MISMATCH` on `json-reparse` |

Not run: an authenticated cluster (the same `es_client` as `data/`, which is
verified there, but not on this path); a host in another time zone (the
container is UTC — the zone argument itself was shown to be honoured);
multiline events; Python 3.8 (syntax only).

### Not covered

- Filebeat and Logstash ([#59](https://github.com/litkhai/clickstack-hyperdx-hols/issues/59)).
- Elastic Agent integrations, and Elasticsearch's built-in pipelines (`logs@json-pipeline` …), which were not tried.
- Index templates and component templates: the schema half is [`../data/`](../data/).

---

## 한국어

Elasticsearch ingest pipeline을 ClickStack용 collector 조각으로 바꾸고, 한 줄씩
확인합니다. 같은 입력을 Elasticsearch 자신의 `_simulate`와 ClickStack 자신의
collector에 통과시켜 `otel_logs`에 넣은 뒤, SQL로 필드마다 비교합니다.

이 부분은 처리 로직이 Elasticsearch **안에**(`GET _ingest/pipeline`) 있는 이전에만
해당합니다. 자체 파이프라인으로 Elasticsearch에 넣던 팀은 여기서 바꿀 것이
없습니다. Filebeat processor와 Logstash filter는
[#59](https://github.com/litkhai/clickstack-hyperdx-hols/issues/59)이고, 같은 단계
목록으로 파싱됩니다.

| 파일 | 하는 일 |
|---|---|
| [`convert.py`](convert.py) | ingest pipeline 하나 → 프로파일 모양의 디렉터리(`custom.config.yaml`과 [`otel-profiles/CONVENTIONS.md`](../../../otel-profiles/CONVENTIONS.md) 규칙 9가 요구하는 파일), stderr에 분류 보고서 |
| [`steps.py`](steps.py) | 출처와 무관한 단계 목록: processor마다 분류, `if`, 실패 처리. #59가 여기로 파싱합니다 |
| [`grok.py`](grok.py) | 클러스터에서 grok 정의를 재귀적으로 풀고, RE2용으로 고쳐 쓰거나 거부합니다 |
| [`check.py`](check.py) | `_simulate`와 collector를 한 줄씩 비교하는 검사 |
| [`validate.sh`](validate.sh) | 생성한 조각을 모두 ClickStack 이미지의 collector로 컴파일합니다 |

### 실행해 보기

```bash
cd _base && docker compose --profile elastic up -d     # Elasticsearch 8.17.0 + ClickStack

cd ../labs/elastic-migration/ingest
./convert.py --pipelines fixtures/pipelines.json --id access-log --name access-log \
    --include '/ingest-verify/in/access-log.*.log' --out-dir out/access-log
../../../otel-profiles/bin/lint.sh out/access-log
./validate.sh out/access-log
./check.py --install-nested --restore     # 모든 fixture pipeline. ClickStack을 두 번 재생성합니다
```

실제 클러스터에서는 `--pipelines` 대신 `--url`을 씁니다(`ES_USER` /
`ES_PASSWORD` 또는 `ES_API_KEY`, `ES_CA_CERT` — [`../data/`](../data/)와 같은
클라이언트). 중첩된 `pipeline` processor도 함께 가져옵니다. grok 정의는 클러스터의
`GET _ingest/processor/grok`에서 읽습니다. 오프라인이면 그 응답을 한 번 저장해
`--grok-patterns`로 넘깁니다.

```bash
curl -s "$ES_URL/_ingest/processor/grok" > grok-patterns.json
```

`check.py`는 생성한 조각을 `_base/docker-compose.ingest-verify.yml`로 ClickStack에
올리고 컨테이너를 재생성합니다. bind mount한 파일이 바뀌어도 알아채지 못하기
때문입니다. `--restore`는 끝나면 override 없이 되돌립니다. `--install-nested`는
`pipeline` processor가 이름으로 부르는 파이프라인을 클러스터에 올립니다.
`_simulate`가 그것을 클러스터에서 찾기 때문입니다.

### 조각의 모양

`--include` glob을 읽는 `filelog/<name>` 수신기가 이름 붙은 파이프라인으로 들어갑니다.

```
logs/<name>: [memory_limiter, transform/<name>, transform, batch] -> [clickhouse]
```

- 규칙 3대로 `memory_limiter`가 처음, `batch`가 마지막입니다.
- 규칙 3이 요구하는 대로 ClickStack 자신의 `transform`을 포함합니다. 조각 **뒤에**
  돌기 때문에, 조각이 파싱한 `level`을 `severity_text`로 올립니다. `drop`은
  `transform/<name>` 뒤에 `filter/<name>`을 추가합니다.
- 생성한 모든 디렉터리에서 `otel-profiles/bin/lint.sh`가 통과합니다. 이제 프로파일
  이름뿐 아니라 경로도 받습니다. `/`가 들어간 인자는 디렉터리입니다.

**필드 대응** — `convert.py` docstring에 한 번 정의합니다.

| Elasticsearch | Collector |
|---|---|
| `message` | 로그 본문 |
| `@timestamp` | 레코드 시각 |
| 나머지 필드 | `log.attributes["<점.경로>"]`, 평평하게 |

### 분류

모든 processor는 정확히 하나의 분류입니다. 보고서가 단계마다 이유를 말합니다.

| 분류 | 출력 | Processor |
|---|---|---|
| converted | OTTL 문장 | set, remove, rename, append, lowercase, uppercase, drop, uri_parts, network_direction |
| needs review | 문장, 위에 이유를 주석으로 | grok, dissect, date, json, kv, convert, csv, gsub, split, trim, sort, user_agent, html_strip, pipeline, dot_expander |
| unsupported | **실행 가능한 것은 없음** — `# UNSUPPORTED <processor>: <이유>`만 | script, enrich, geoip, foreach, bytes, urldecode, registered_domain, join, fail, terminate, inference, set_security_user, date_index_name, reroute, circle, geo_grid, community_id, redact, fingerprint |

설계의 needs review 목록 중 셋은 unsupported로 남습니다. 각각 2026-10-06에 확인한
이유입니다(#64):
- `community_id`: 번들된 collector에 `CommunityID()`가 있지만, 지원하는 프로토콜과
  인자가 Elasticsearch와 다릅니다.
- `redact`: platinum이나 enterprise 라이선스가 필요합니다. basic 라이선스 클러스터는
  `_simulate`에서도 이를 실패시키므로 비교할 대상이 없었습니다.
- `fingerprint`: Elasticsearch는 해시 원래 바이트의 base64를 냅니다. OTTL 해시는 hex를
  돌려주고, hex를 바이트로 되돌리는 함수가 없습니다.

- **`if` 조건**(Painless)은 정해진 모양만 OTTL `where`로 바꿉니다.
  - 허용 모양: `ctx.a.b == 'x'`, `ctx.a?.b != null`, `.contains('x')`, RE2가 컴파일할
    수 있는 `=~ /re/`, 그리고 이들의 `&&` / `||` / `!`
  - 나머지는 추측하지 않습니다. 단계는 needs review가 되고, 문장은 `where false`로
    나와서 실행되지 않습니다. 이유에는 Painless 원문이 들어갑니다.
- **`ignore_failure`**는 `error_mode: ignore`와 보고서 메모가 됩니다.
- **`on_failure`**는 실패를 볼 수 있는 `grok`과 `dissect`에서만 변환합니다. 추출이
  `log.cache`를 비워 두기 때문입니다. 그 handler(`set`, `remove`, `rename`, `append`)는
  `Len(log.cache) == 0`, 즉 아무것도 맞지 않았을 때 실행됩니다. Elasticsearch는 다른
  실패에서도 실행합니다. 그 밖의 `on_failure`는 변환하지 않고, 그 단계는 needs review입니다.
- **`network_direction`**은 Elasticsearch의 이름 붙은 범위(`private`, `public`,
  `loopback` …)를 8.17.0 소스에 따라 CIDR로 펼쳐 `IsInCIDR`에 넘깁니다. `public`과
  `unicast`는 여집합이라 `not IsInCIDR(…)`가 됩니다. `internal_networks_field`는
  unsupported입니다.
- **시각**은 processor가 시간대를 지정하지 않으면 UTC로 파싱합니다. collector의
  `time_parser` 기본값은 `Local`이고 Elasticsearch는 UTC입니다. Java 날짜 패턴은
  정확히 옮길 수 있을 때만 옮깁니다. `UNIX`, `UNIX_MS`, `TAI64N`은 변환하지 않았다고
  보고합니다.

### grok — RE2가 컴파일하지 못하는 것

Elasticsearch 8.17.0의 기본 패턴 묶음(`ecs_compatibility: disabled`, legacy 이름:
`clientip`, `bytes` …)은 RE2가 아닙니다.

- 318개 중 129개가 lookbehind·lookahead·atomic group을 씁니다. `IPV4`, `BASE10NUM`,
  `TIME`, `YEAR`, `QUOTEDSTRING`이 그렇습니다. 그래서 `%{COMBINEDAPACHELOG}`는
  그대로는 컴파일되지 않습니다.
- **RE2가 컴파일하지 못하는 정의가 하나라도 있으면 collector 전체가 시작하지
  않습니다.** 그 정의를 쓰는 문장 하나만 실패하는 것이 아닙니다.

그래서 `grok.py`는 딱 두 가지만 고쳐 씁니다. `(?>X)`를 `(?:X)`로 바꾸고, lookaround를
지웁니다. 둘 다 매칭을 **넓힙니다**. 손댄 정의는 모두 단계의 이유에 이름을 적고,
그 단계는 needs review가 됩니다. 아래는 아예 거부해서 unsupported가 됩니다.

- backreference
- possessive quantifier
- Oniguruma 전용 escape(`\h` `\k` `\G` `\Z` `\R` `\X` `\K`) — legacy 묶음에서는
  `RUUID` 하나
- RE2 한계 1000을 넘는 중첩 반복 횟수

마지막 것 때문에 **ECS-v1** `%{COMBINEDAPACHELOG}`는 unsupported입니다.
`EMAILLOCALPART`가 `{0,63}` 안에 `{1,62}`를 중첩해 3,906이 됩니다.

`namedCapturesOnly`는 늘 `true`입니다. 정의는 `ExtractGrokPatterns`에
`"NAME=definition"` 문자열 목록으로 넘깁니다. 실행해 보니 map과 `"NAME definition"`은
둘 다 거부됐습니다. 여러 패턴 `patterns: […]`는 Elasticsearch처럼 처음 맞는 것이
이기도록 조건 붙은 문장으로 나옵니다. 맞지 않으면 빈 map이 나오고 레코드는 그대로
진행합니다. Elasticsearch는 문서를 실패시킵니다. 그래서 needs review이고,
`check.py`가 그 차이를 보여 줍니다.

grok 패턴 파일은 이 저장소에 넣지 않았습니다. Elasticsearch 8.17.0은 Apache-2.0이
아닙니다. 배포본은 Elastic License 2.0이고 grok jar는 AGPL v3입니다. 그래서 변환기는
실행할 때 클러스터에서 정의를 읽습니다. 테스트는 테스트용으로 만든 작은 패턴
묶음을 씁니다.

### `check.py`

파이프라인과 그 입력 줄마다 이렇게 합니다.

1. **Elasticsearch.** 파이프라인을 인라인으로 넣어
   `POST _ingest/pipeline/_simulate?verbose=true`를 부릅니다. 줄마다 `_source`,
   실패, 또는 drop과 processor별 상태가 나옵니다.
2. **Collector.** 줄을 마운트한 입력 디렉터리의 고유한 이름의 파일에 쓰고, 조각들을
   한 설정으로 합칩니다(파이프라인 15개에 약 25초). ClickStack을 재생성한 뒤
   `otel_logs`에서 **SQL로** 행을 읽습니다. 행은 `LogAttributes['log.file.name']`으로
   고릅니다.
3. **비교.** Elasticsearch `_source`를 점 경로로 펼치고 exporter가 attribute를 쓰는
   방식으로 문자열화한 뒤, `Body`, `LogAttributes`, `SeverityText`, `Timestamp`와
   비교합니다.

| 결과 | 뜻 |
|---|---|
| `PASS` | 모든 필드가 같음. 더 있는 필드는 ClickStack `transform`이나 `filelog`로 설명되는 것만 |
| `REVIEW` | 차이가 있지만, 그 줄에 이름이 적힌 needs review 단계가 이유와 함께 설명함 |
| `UNSUPPORTED` | unsupported 단계로 설명되는 차이 |
| `MISMATCH` | 나머지 전부 — converted 단계의 필드가 틀리거나 없음, 아무것도 설명하지 못하는 추가 필드, 도착하지 않은 행, Elasticsearch는 버렸는데 collector는 남긴 줄 |

추가 필드는 실행 중인 이미지(`/etc/otelcol-contrib/config.yaml`)에서 읽은 ClickStack
`transform`이 실제로 하는 일로만 설명합니다. 그 `transform`은 `{…}` 본문을 JSON으로
파싱해 attribute에 넣고, `level`/`severity`를 올리고, 본문에서 severity를 추정해
소문자로 바꾸고, **map과 배열을 펼칩니다**. 그래서 Elasticsearch 배열은 압축 JSON이
아니라 `tags.0`, `tags.1`로 도착합니다. `log.file.name`은 `filelog`가 붙입니다.

### 실행에서 드러난 것

- **filelog는 기본으로 줄 앞뒤 공백을 지웁니다.** Elasticsearch의 `message`는
  남깁니다. 생성하는 수신기는 `preserve_leading_whitespaces`와
  `preserve_trailing_whitespaces`를 켭니다.
- **filelog는 파일을 앞 바이트로 알아봅니다.** 이미 읽은 파일과 내용이 같은 새
  파일은 옛 파일로 여겨 읽지 않습니다. 그래서 `check.py`는 아래 셋을 합니다.
  - 실행마다 입력 디렉터리를 비웁니다.
  - 한 파이프라인에 같은 줄이 두 번 있으면 거부합니다.
  - 겹치는 include glob을 거부합니다(`access-log-*`는 `access-log-ecs-…`도 잡습니다).
- **수신기의 첫 poll 뒤에 생긴 파일은 `start_at: end`여도 처음부터 읽습니다.** 그래서
  검사는 생성된 설정을 바꾸지 않고 파일을 읽습니다.
- **지수가 있는 OTTL float 리터럴(`1e21`)도 collector가 시작할 때 죽입니다.**
  lexer가 읽지 못합니다. float는 지수 없이 씁니다.
- **`ParseJSON`은 숫자를 float64로 바꿉니다.** `9007199254740993`이
  `9007199254740992`로 도착합니다. Elasticsearch는 `Long`을 넘는 정수가 있는 문서를
  실패시킵니다.
- **ClickStack `transform`은 조각 다음에 JSON 본문을 다시 파싱해 키를 upsert합니다.**
  그래서 조각이 파싱한 뒤 바꾼 키는 덮어써집니다. `json-reparse` fixture가 보여 줍니다:
  Elasticsearch는 `user: overridden`을 갖고, ClickStack에는 `alice`가 남습니다. 그래서
  변환기는 `message`에 대한 `json` 단계 뒤에 필드를 쓰는 단계를 모두 needs review로
  표시합니다. `app-json-root`와 `app-json-nested`의 단계 일부가 그쪽으로 옮겨 갔습니다.
- **`IsInCIDR`는 IP가 아닌 문자열에 오류 없이 false를 돌려줍니다.**
  `source.ip=not-an-ip`이면 `network.direction: external`이 되지만, Elasticsearch는
  문서를 실패시킵니다. `ignore_missing: false`에서 IP가 없으면 필드가 빠지고
  Elasticsearch는 실패시킵니다. 이때 단계는 needs review입니다. `::ffff:10.0.0.1`은
  Java에서 IPv4이고, collector도 같게 봅니다.
- **`UserAgent()`**는 `user_agent.name`, `version`, `original`과 `os.name` /
  `os.version`만 돌려줍니다. `os.full`, `device.name`은 없고, 파서 표도
  Elasticsearch와 다릅니다.
- **데이터베이스가 없는 `geoip`는 Elasticsearch에서 실패하지 않고**, 문서에
  `_geoip_database_unavailable_GeoLite2-City.mmdb` 태그를 붙입니다.
- **조각만으로 `otelcontribcol validate`를 돌리면 실패합니다**(`no exporter
  configuration specified`). 기본 컴포넌트가 이미지의 설정과 HyperDX API가 OpAMP로
  보내는 것에 있기 때문입니다. `validate.sh`는 조각을 이미지 설정 위에 합치고
  OpAMP 부분을 대신하는 stub를 더해 검증합니다. OTTL 문장과 grok 패턴을 모두
  컴파일하지만 실행하지는 않습니다. 실행은 `check.py`가 합니다.

### 검증 환경

Elasticsearch **8.17.0** (Lucene 9.12.0), ClickStack/HyperDX
**2.39.1** (`clickhouse/clickstack-all-in-one:2.39.1`), collector
**otelcol-hyperdx 0.155.0** (contrib v0.155.0), ClickHouse **26.8.7.19**(ClickStack에
번들된 것, `otel_logs`가 있는 곳), Python 3.9.6.

- **2026-10-06**(#64), 같은 버전: fixture pipeline 21개 중 20개를 입력 75줄로 검사.
  **PASS 51, REVIEW 17, UNSUPPORTED 7, MISMATCH 0.** 기존 50줄의 결과는 그대로입니다.
  다른 로컬 스택이 8123과 9000을 쓰고 있어서, 임시 compose override로 ClickStack의
  ClickHouse 포트를 18123 / 19000으로 옮겼습니다. 그 밖에는 같습니다. `test_*.py`: 테스트
  116개 모두 통과. `lint.sh`와 `validate.sh`는 21개 모두 통과.
- 2026-10-02: `GET _ingest/pipeline` 모양의 fixture pipeline 16개 중 15개를 입력 50줄로
  검사(`nested-child`는 `nested-parent` 안에서 실행): **PASS 29, REVIEW 14,
  UNSUPPORTED 7, MISMATCH 0.** 리드가 다시 돌려 같은 결과. `test_*.py`: 테스트 91개.
  생성한 디렉터리 16개 모두 `lint.sh` 통과, 인자 없는 실행 결과는 경로 지원 추가 전후가
  같음. `validate.sh` 16개 모두 통과.
- 고장 주입. 모두 잡혔습니다.

| 고장 | 잡은 것 |
|---|---|
| 생성된 조각의 rename 대상 변경 | `check.py`: 4줄 `MISMATCH` — 빠진 필드와 설명되지 않는 추가 필드 |
| `uri` 조각의 set 값 변경(리드) | `check.py`: 3줄 `MISMATCH` |
| converted processor가 아무것도 내지 않도록 패치 | 단위 테스트 |
| 이름 없는 `logs:` 파이프라인 | `lint.sh` |
| OTTL 함수 이름 오타 | `validate.sh` |
| `netdir` 조각에서 `outbound`와 `inbound`를 맞바꿈(#64) | `check.py`: 5줄 `MISMATCH` |
| `grok-onfail`, `dissect-onfail`의 `on_failure` 값 변경(#64) | `check.py`: 각각 매칭 실패 줄에서 `MISMATCH` |
| `convert.py`에서 재파싱 메모를 끔(#64) | `check.py`: `json-reparse`에서 `MISMATCH` |

실행하지 않은 것:
- 인증이 켜진 클러스터. `data/`와 같은 `es_client`이고 거기서는 검증됐지만, 이 경로에서는 돌리지 않았습니다.
- 다른 시간대의 호스트. 컨테이너가 UTC라서, 시간대 인자가 지켜지는 것만 확인했습니다.
- multiline 이벤트
- Python 3.8. 구문만 확인했습니다.

### 다루지 않는 것

- Filebeat와 Logstash ([#59](https://github.com/litkhai/clickstack-hyperdx-hols/issues/59)).
- Elastic Agent integration, Elasticsearch 내장 파이프라인(`logs@json-pipeline` …) — 시도하지 않았습니다.
- index template과 component template: 스키마 쪽은 [`../data/`](../data/)입니다.
