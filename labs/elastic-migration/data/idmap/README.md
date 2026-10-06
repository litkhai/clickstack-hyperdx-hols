# Elastic migration: ID translation

[English](#english) | [한국어](#한국어)

## English

The two systems disagree about identity. The mapping is too large to hold in
the exporter's memory. And the conversion is one that no type check can
police, so the interesting part of this directory is not the SQL -- it is the
[case matrix](#the-case-matrix) and the checks that make a wrong translation
loud.

### The shape of the problem

| Field | Elasticsearch | ClickHouse | The disagreement |
|---|---|---|---|
| `ItemSN` | `long` | `Int64` | **same type, different value** -- the same item has a different serial number on each side |
| `user_id` | UUID string, but only in transaction and delivery logs | numeric `User_Id` | different type *and* different value, and only on some log types |

`ItemSN` is also not reliably a column: depending on the log type it sits at
the top level, inside a JSON `payload`, or is absent.

### Why this is harder than it looks

**`long` → `Int64` defeats every type-based safety net.** It is a direct
conversion, so a row where the mapping was never applied is
*indistinguishable* from one where it was: same column, same type, a
plausible integer. No cast fails. Nothing is null. It surfaces months later
as a join that quietly returns the wrong item.

Three consequences, and they drive everything here:

1. **Correctness cannot be carried by the value.** Every translated field
   carries an explicit status -- `translated`, `not_applicable`, `unmapped`,
   `malformed`, `native` -- and the counts must satisfy
   `rows_in = translated + not_applicable + quarantined`. A conservation law
   is the only thing that catches silent loss when the output *looks* right.
2. **Translation is not idempotent unless it is made so.** Source and target
   are the same type in the same numeric domain, so a second pass over an
   already-translated row can find the *target* SN in the map and translate it
   again. This matters precisely because everything else in this lab is built
   to be re-run after a failure.
3. **The mapping belongs in ClickHouse, not in the exporter.** Holding it in
   the export process is the thing to rule out -- and once it is in
   ClickHouse, a mapping larger than RAM is a configuration choice rather
   than a problem.

### Three rules the SQL encodes

**Load raw, then translate.** `translate.sql` reads `user_logs_raw` and writes
`user_logs`. It never updates a translated value in place. That is what makes
consequence 2 structurally impossible rather than carefully avoided -- and
`test_cases.py` demonstrates the alternative failing: an `ALTER TABLE ...
UPDATE` run twice turned `1001` into `77001` and then into `66001`, with no
error anywhere.

**Quarantine, never guess.** A row with any `unmapped` or `malformed` field
goes to `user_logs_quarantine` whole, with its reason and its raw values --
never half into each table. Half a translated row is what gets found in
production instead of during the migration. A mapping row that arrives later
rescues it: re-running the translation moves it out of quarantine, and only
it.

**Say which of the five things happened.** `not_applicable` (this log type
has no ItemSN) is a different fact from `unmapped` (it has one and the map
does not), which is different from `native` (`user_id` was already numeric,
no lookup needed). Collapsing them into `NULL` or `0` throws away the only
evidence there is.

### The case matrix

Four independent axes. The matrix, not any single example, is the deliverable
-- a different migration will differ in the particulars and not in the axes.

| Axis | Values |
|---|---|
| **locus** -- where the id is | top-level column · inside `payload` JSON · absent (by log type) |
| **map outcome** | hit · miss · ambiguous (two targets for one source) · identity (target == source) |
| **value shape** | right type · JSON string-encoded number (`"1005"`) · `null` · sentinel (`0`, `-1`) · malformed UUID · UUID case/hyphen variant |
| **run count** | once · twice |

| # | Case | Expected |
|---|---|---|
| T1 | `ItemSN` top-level, in the map | translated |
| T2 | `ItemSN` inside `payload`, in the map | translated -- extraction is part of the unit |
| T3 | log type that never carries `ItemSN` | `NULL` + `not_applicable`, **never `0`** |
| T4 | `payload` present, no `ItemSN` key | `NULL` + `not_applicable` |
| T5 | `ItemSN` present, no mapping row | quarantined `unmapped` -- **not** passed through unchanged |
| T6 | mapping row is identity (`1003 → 1003`) | `translated`; a map hit, not a skip |
| T7 | two mapping rows for one source SN | refused by preflight, no deterministic pick |
| T7-engine | the same ambiguity in a `ReplacingMergeTree` map | silently collapsed on INSERT -- why the mapping tables are plain `MergeTree` |
| T8 | `payload` holds `"1005"` as a JSON string | coerced under a declared rule, flagged in `item_sn_coerced` |
| T8b | `payload` holds `"abc"` | `malformed` → quarantined, never a silent cast to `0` |
| T9 | `payload` holds `ItemSN: null` | `not_applicable` |
| T10 / T10b | `ItemSN` is `0` / `-1` | `not_applicable`; the id is never used as a lookup key |
| T11 | `user_id` UUID in a transaction log, in the map | translated to the numeric id |
| T12 | `user_id` UUID not in the map | quarantined `unmapped` |
| T13 | `user_id` is not a UUID | quarantined `malformed`, **distinct from** `unmapped` |
| T14 | the same UUID uppercase and unhyphenated | normalised first, same target as T11 |
| T15 | log type where `user_id` is already numeric | `native`, no lookup |
| T16 | one row, `ItemSN` hits and `user_id` misses | quarantined whole, both statuses kept |
| T17 | mapping row arrives after the row was quarantined | re-running rescues it, and only it |
| T17-reload | that mapping row added without `SYSTEM RELOAD DICTIONARY` | stays invisible -- indistinguishable from unmapped, which is why the reload is a documented step and not a footnote |
| T18 | translation run twice | identical output; a target SN is never re-translated |
| T18-inplace | the same thing done with `ALTER ... UPDATE` | demonstrably double-translates |
| T19 | the map held on disk instead of in RAM | identical rows, digest for digest |
| T19-join | no dictionary at all, a spilling `JOIN` | identical rows again |
| T20 | conservation | `rows_in = translated + not_applicable + quarantined`, per field |
| T21 | `user_id` absent entirely | `not_applicable` |

T18 and T20 are the two that fail silently in a hand-written translation, and
the reason this is a harness rather than a code review.

### Run it

```bash
cd labs/elastic-migration/data/idmap
./test_cases.py                      # the matrix, against _base/'s target ClickHouse
./test_cases.py --keep               # leave the database behind to poke at
./bench.py --map-rows 5000000 --fact-rows 2000000
```

`PASS` / `FAIL` per case, in the convention of `_base/bin/check.sh`. The whole
matrix runs three times -- against an in-memory dictionary, a disk-backed one,
and a dictionary-free `JOIN` -- because a layout is supposed to change what it
costs and not what it answers.

| File | What it is |
|---|---|
| `schema.sql` | raw, mapping, output, quarantine and staging tables, plus four dictionaries |
| `preflight.sql` | mapping-table health as one query: `(check, severity, n, detail)` |
| `translate.sql` | the translation, via `dictGet` |
| `translate_join.sql` | the same translation, via a spilling `JOIN`, no dictionary |
| `test_cases.py` | the case matrix above, executable |
| `bench.py` | the same work four ways, with memory and wall clock |

### Check the mapping table before translating

`preflight.sql` is one query returning `(check, severity, n, detail)`.
Everything in it produces a *plausible wrong answer* rather than an error,
which is why it is a check and not a line in a runbook:

| Severity | Check | Why it is not an error you would notice |
|---|---|---|
| `FAIL` | two different targets for one source id | whichever row merged last wins; the result is stable, wrong, and looks correct |
| `FAIL` | `user_id` keys not in canonical form | every lookup normalises first, so a map full of non-canonical keys matches **nothing** while looking full |
| `FAIL` | target ids that are also source ids | the double-translation hazard, quantified: this is the room for the mistake T18-inplace demonstrates |
| `WARN` | several source ids sharing one target | merges those items -- sometimes a deliberate deduplication, usually a mapping export that lost a join condition |
| `INFO` | identity rows, and row counts | a map that is *entirely* identity usually means the export joined a table to itself, and the migration then looks completely successful |

**The mapping tables are plain `MergeTree`, and that is a decision.**
`ReplacingMergeTree` is the obvious engine for "one row per source id" and it
destroys the evidence the first check depends on. Measured on 26.6.8.7: two
conflicting rows for one key in a single `INSERT` come back as **one** row
immediately; in separate inserts both are visible until a merge collapses
them. So on a `ReplacingMergeTree`, whether the ambiguity check can see
anything is a race against the merge scheduler. On a `MergeTree`, a re-export
of the same pair leaves harmless identical duplicates -- the check only fires
on two *different* targets -- and deduplication becomes a deliberate step
after the check has passed.

**`LIFETIME(0)` on every dictionary**, so the map is loaded once and every
chunk of a long migration is translated against one version of it. The cost is
that a mapping row added later is invisible until `SYSTEM RELOAD DICTIONARY`,
and an unreloaded dictionary looks exactly like an unmapped id -- so T17-reload
asserts that too.

### Choosing where the mapping lives

Four strategies, the same data, the same answer. Measured on the pinned
target -- ClickHouse **26.6.8.7**, 7.75 GiB of server memory, which leaves a
~4.5 GiB ceiling for queries and dictionaries together.

**A mapping that fits: 5,000,000 `ItemSN` + 5,000,000 `user_id` rows**
(38 MiB + 171 MiB on disk), translating 2,000,000 raw rows:

| Strategy | translate | rows/s | peak query memory | dictionary resident | keys resident |
|---|---|---|---|---|---|
| `HASHED` / `COMPLEX_KEY_HASHED` | 1.6s | 1,261,181 | 322.6 MiB | 1024.0 MiB | 10,000,000 |
| `SSD_CACHE` / `COMPLEX_KEY_SSD_CACHE` | 12.8s | 152,801 | 297.5 MiB | 962.0 MiB | 1,600,000 |
| `JOIN`, `grace_hash` | 4.4s | 449,773 | 738.5 MiB | -- | -- |

**A mapping that does not fit: 20,000,000 + 20,000,000 rows**
(154 MiB + 681 MiB on disk), translating 500,000 raw rows:

| Strategy | translate | rows/s | peak query memory | dictionary resident | keys resident |
|---|---|---|---|---|---|
| `HASHED` | **did not fit** | -- | -- | -- | -- |
| `SSD_CACHE` | 4.6s | 107,450 | 98.9 MiB | 626.0 MiB | 400,000 of 40,000,000 |
| `JOIN`, `grace_hash`, 32 initial buckets | 5.5s | 89,892 | 656.9 MiB | -- | -- |
| `JOIN`, `grace_hash`, default buckets | **did not fit** | -- | -- | -- | -- |

`HASHED` did not fail while translating -- it failed at
`SYSTEM RELOAD DICTIONARY`, before a single row was translated:
`would use 5.40 GiB ... maximum: 4.54 GiB`. That is the honest form of the
answer to "the mapping table will not fit in memory": the in-memory layout
does not degrade, it declines.

How to read the rest:

- **`SSD_CACHE`'s footprint is what you configure, not what the map weighs.**
  The same dictionaries allocate 257 MiB each against the 19-row fixture in
  `test_cases.py` and 626 MiB against 40,000,000 keys. `FILE_SIZE`,
  `BLOCK_SIZE`, `MAX_PARTITIONS_COUNT` and `WRITE_BUFFER_SIZE` set that
  bound; the mapping table's size does not. It held 400,000 keys because
  that is what the data touched -- and that gap between working set and map
  is the entire reason this layout exists.
- **It costs 5-8x in throughput**, which is the trade being made. Over a
  migration measured in hours that is usually the cheaper half of the
  bargain than not running at all.
- **The `JOIN` holds nothing resident**, needs no dictionary defined,
  reloaded or kept in step, and pays per query instead. At the larger map it
  was also the strategy whose *setting* decided whether it ran:
  `grace_hash_join_initial_buckets = 32` finished in 5.5s at 657 MiB, while
  the default asked for 7.36 GiB and was killed. grace_hash does grow its
  buckets by itself, but it grows them after trying, and the first try is
  what gets killed.

| Situation | Use |
|---|---|
| map fits in RAM comfortably, many lookups over a long run | `HASHED` / `COMPLEX_KEY_HASHED` |
| map does not fit, and the working set is much smaller | `SSD_CACHE` / `COMPLEX_KEY_SSD_CACHE` |
| one batch translation of everything, nothing to maintain afterwards | `JOIN` with `grace_hash` (raise the initial buckets) |
| the mapping lives in its own database (MySQL, Postgres) and lookups are few | `DIRECT` / `COMPLEX_KEY_DIRECT` -- listed for completeness, not measured here |

**A resident dictionary comes out of the same budget as everything else.**
Measured while getting these numbers: with all four dictionaries attached
(1.7 GiB between them), the `JOIN` run was killed at the server ceiling --
which reads as "the JOIN does not fit" when what did not fit was the
leftovers of two earlier runs. `bench.py` detaches the dictionaries a
strategy does not use, and a real migration should think the same way: a
`LIFETIME(0)` dictionary stays resident until something detaches it.

`./bench.py --only ssd` measures one strategy per process, which is the only
honest way to measure at a size where a strategy runs out of memory -- what a
failed attempt leaves behind affects the next one.

### Verified on

**ClickHouse 26.6.8.7** (the pinned migration target in `_base/`, matching the
26.6 line ClickHouse Cloud's regular release channel runs), ClickStack/HyperDX
2.39.1 running but not on this path. **Elasticsearch is not involved at all**,
and that is the design: translation happens in ClickHouse after the load, not
in flight.

`./test_cases.py`: **70 assertions, 0 failures.** The whole matrix runs three
times -- in-memory dictionary, disk-backed dictionary, and a dictionary-free
`JOIN` -- and all three produced byte-identical output (digest for digest). The
cases that assert a hazard exists rather than a feature works were checked to
still fail the way they document:

- an `ALTER TABLE ... UPDATE` run twice took `1001` to `77001` and then to
  `66001`, with no error anywhere (T18-inplace)
- a mapping row added without `SYSTEM RELOAD DICTIONARY` stayed invisible,
  indistinguishable from an unmapped id (T17-reload)
- two conflicting mapping rows in one `INSERT` into a `ReplacingMergeTree`
  came back as one row, `[88002]`, immediately (T7-engine)

`./bench.py`: the numbers in the section above, from two map sizes.

### On ClickHouse Cloud

Run on 2026-10-06 against a ClickHouse Cloud service on **26.6.1.2292**: two
replicas of 8 GiB (autoscaling 8–120 GiB), shared with other demo
databases. A scratch database held a 200,000-row map, then 100,000 more rows,
and was dropped afterwards (#41). `test_cases.py` and `bench.py` were not run
there.

- **`SSD_CACHE` works with `schema.sql`'s `PATH`.** 300,000 entries, well past
  the 1 MiB write buffer, translated correctly on both replicas at 305 MiB per
  replica, the "what you configure" footprint above. A `PATH` outside
  `/var/lib/clickhouse/user_files/` is refused (`PATH_ACCESS_DENIED`), so keep
  it there. The table above stands.
- **Each replica holds its own copy of a dictionary, and
  `SYSTEM RELOAD DICTIONARY` reloads only one.** After 100,000 mapping rows were added,
  the plain reload that `test_cases.py` and `bench.py` run fixed one replica.
  The other kept the 200,000-row copy and left the new keys untranslated,
  with no error. `ON CLUSTER 'default'` reloaded both. In an earlier attempt, a
  `HASHED` dictionary created right after the map's `INSERT` loaded 0 rows on
  one replica, and `LIFETIME(0)` kept it that way. So on Cloud, reload with
  `ON CLUSTER 'default'`, then check every replica before translating:

  ```sql
  SELECT hostName(), name, status, element_count
  FROM clusterAllReplicas('default', system.dictionaries)
  WHERE database = '<db>' AND name IN ('item_sn_dict_hashed', 'user_id_dict_hashed')
  ```

  On every row, `element_count` must equal the map's `count()`. (For
  `SSD_CACHE`, the count is what has been looked up, not the map.)
- **Memory: the ceiling is per replica, and close to the local one.**
  `max_server_memory_usage` was 7.05 / 7.13 GiB at 8 GiB per replica. The
  service's other work already used about 1.8 GiB, which left about 5.3 GiB
  free, against 4.54 GiB locally. `HASHED` at 20M + 20M asked for 5.40 GiB
  locally, and each replica loads its own copy. This was not measured on
  Cloud: at the service's minimum size it is on the edge, and whether
  autoscaling raises the ceiling in time for a dictionary load was not tested.

### What needs a decision, not a default

The matrix is fixed; these are not. Worth settling before a real run:

- **Is an unmapped id a stopping error or an expected long tail?** This
  quarantines and continues, and `parity_checks.py`'s counts will show the
  gap. If an unmapped id means the mapping export is wrong, stop on the first
  one instead.
- **Is the mapping stable during the migration?** `LIFETIME(0)` assumes yes.
  If ids are still being minted, chunks translated at different times are
  translated against different maps.
- **Is the reverse direction ever needed?** Nothing here supports
  ClickHouse → Elasticsearch. It is a second dictionary keyed the other way,
  which is cheap to add and not free to keep correct.
- **The declared rule for a JSON string-encoded number.** This coerces and
  flags (`item_sn_coerced`). Quarantining instead is a one-line change, and is
  the right call if a string `ItemSN` means the producer was broken rather
  than merely loose.
- **What `0` and `-1` mean.** Treated here as "no item". If they are real ids
  in your data, that assumption is wrong and T10 should fail.

---

## 한국어

두 시스템이 동일성에 대해 서로 다른 값을 갖고 있습니다. 매핑 테이블은 익스포터
메모리에 올리기엔 너무 큽니다. 그리고 이 변환은 **어떤 타입 검사로도 잡을 수 없는**
종류입니다. 그래서 이 디렉터리에서 중요한 것은 SQL이 아니라
[케이스 매트릭스](#케이스-매트릭스)와 잘못된 변환을 시끄럽게 만드는 검사들입니다.

### 문제의 모양

| 필드 | Elasticsearch | ClickHouse | 불일치 |
|---|---|---|---|
| `ItemSN` | `long` | `Int64` | **타입은 같고 값이 다름** -- 같은 아이템의 일련번호가 양쪽에서 다름 |
| `user_id` | UUID 문자열, 단 거래·택배 로그에만 | 숫자 `User_Id` | 타입도 값도 다르고, 일부 로그 타입에만 있음 |

`ItemSN`은 컬럼으로 항상 있지도 않습니다. 로그 타입에 따라 최상위에 있거나, JSON
`payload` 안에 있거나, 아예 없습니다.

### 왜 보기보다 어려운가

**`long` → `Int64`는 모든 타입 기반 안전장치를 무력화합니다.** 직접 변환이므로
매핑이 적용되지 않은 행과 적용된 행을 **구별할 수 없습니다**. 같은 컬럼, 같은 타입,
그럴듯한 정수. 캐스팅 오류도 없고 null도 없습니다. 몇 달 뒤에 엉뚱한 아이템을 조용히
반환하는 조인으로 드러납니다.

여기 모든 설계를 끌고 가는 세 가지 결론입니다.

1. **정확성을 값이 담을 수 없습니다.** 변환되는 모든 필드는 명시적 상태를 갖습니다 --
   `translated`, `not_applicable`, `unmapped`, `malformed`, `native` -- 그리고 개수가
   `rows_in = translated + not_applicable + quarantined`를 만족해야 합니다. 출력이
   *맞아 보일 때* 조용한 손실을 잡아내는 것은 보존 법칙뿐입니다.
2. **일부러 만들지 않으면 멱등하지 않습니다.** 소스와 타깃이 같은 타입, 같은 숫자
   영역이라서, 이미 변환된 행을 한 번 더 통과시키면 **타깃** SN을 매핑에서 찾아 다시
   변환할 수 있습니다. 이 실습의 나머지 전부가 실패 후 재실행을 전제로 만들어졌기
   때문에 더욱 중요합니다.
3. **매핑은 익스포터가 아니라 ClickHouse에 있어야 합니다.** 내보내기 프로세스에
   들고 있는 것이 배제해야 할 방식이고, ClickHouse 안에 있으면 RAM보다 큰 매핑은
   문제가 아니라 설정 선택이 됩니다.

### SQL이 담고 있는 세 규칙

**원본을 적재하고, 그다음 변환합니다.** `translate.sql`은 `user_logs_raw`를 읽고
`user_logs`에 씁니다. 변환된 값을 제자리에서 갱신하는 일은 절대 없습니다. 결론 2를
조심해서 피하는 것이 아니라 **구조적으로 불가능**하게 만드는 부분입니다.
`test_cases.py`는 그 대안이 실패하는 모습도 보여줍니다: `ALTER TABLE ... UPDATE`를
두 번 실행하면 `1001`이 `77001`을 거쳐 `66001`이 되고, 어디에도 오류는 없습니다.

**추측하지 않고 격리합니다.** `unmapped`나 `malformed` 필드가 하나라도 있는 행은
이유와 원본 값을 달고 `user_logs_quarantine`으로 통째로 갑니다 -- 두 테이블에 절반씩
들어가는 일은 없습니다. 절반만 변환된 행이 바로 마이그레이션 중이 아니라 운영 중에
발견되는 것입니다. 나중에 도착한 매핑 행이 구제합니다: 변환을 다시 돌리면 격리에서
나오고, 그 행만 나옵니다.

**다섯 가지 중 무엇이 일어났는지 말합니다.** `not_applicable`(이 로그 타입에는
ItemSN이 없음)은 `unmapped`(있는데 매핑에 없음)와 다른 사실이고, `native`(`user_id`가
이미 숫자여서 조회가 필요 없음)와도 다릅니다. 이것들을 `NULL`이나 `0`으로 합치면
남아 있는 유일한 증거를 버리는 것입니다.

### 케이스 매트릭스

독립적인 네 개의 축입니다. 개별 예시가 아니라 **매트릭스**가 산출물입니다. 다른
마이그레이션은 세부가 다를 뿐 축은 같습니다.

| 축 | 값 |
|---|---|
| **위치** -- id가 어디 있나 | 최상위 컬럼 · `payload` JSON 안 · 없음(로그 타입별) |
| **매핑 결과** | 히트 · 미스 · 모호(한 소스에 타깃 둘) · 항등(타깃 == 소스) |
| **값의 형태** | 올바른 타입 · JSON 문자열로 온 숫자(`"1005"`) · `null` · 센티널(`0`, `-1`) · 잘못된 UUID · UUID 대소문자·하이픈 변형 |
| **실행 횟수** | 한 번 · 두 번 |

| # | 케이스 | 기대 결과 |
|---|---|---|
| T1 | `ItemSN` 최상위, 매핑에 있음 | 변환됨 |
| T2 | `ItemSN`이 `payload` 안, 매핑에 있음 | 변환됨 -- JSON 추출도 이 단위의 일부 |
| T3 | `ItemSN`을 아예 갖지 않는 로그 타입 | `NULL` + `not_applicable`, **절대 `0` 아님** |
| T4 | `payload`는 있고 `ItemSN` 키가 없음 | `NULL` + `not_applicable` |
| T5 | `ItemSN`은 있고 매핑 행이 없음 | `unmapped`로 격리 -- 그대로 통과시키지 **않음** |
| T6 | 매핑 행이 항등(`1003 → 1003`) | `translated`. 건너뛴 것이 아니라 매핑 히트 |
| T7 | 한 소스 SN에 매핑 행이 둘 | preflight가 거부. 임의 선택 없음 |
| T7-engine | 같은 모호성을 `ReplacingMergeTree` 매핑에서 | INSERT 시점에 조용히 하나로 접힘 -- 매핑 테이블이 평범한 `MergeTree`인 이유 |
| T8 | `payload`에 `"1005"`가 JSON 문자열로 | 선언된 규칙에 따라 강제 변환하고 `item_sn_coerced`로 표시 |
| T8b | `payload`에 `"abc"` | `malformed` → 격리. `0`으로 조용히 캐스팅하지 않음 |
| T9 | `payload`에 `ItemSN: null` | `not_applicable` |
| T10 / T10b | `ItemSN`이 `0` / `-1` | `not_applicable`. 그 id는 조회 키로 쓰이지 않음 |
| T11 | 거래 로그의 `user_id` UUID, 매핑에 있음 | 숫자 id로 변환 |
| T12 | `user_id` UUID가 매핑에 없음 | `unmapped`로 격리 |
| T13 | `user_id`가 UUID가 아님 | `malformed`로 격리. `unmapped`와 **구분** |
| T14 | 같은 UUID를 대문자·하이픈 없이 | 먼저 정규화, T11과 같은 타깃 |
| T15 | `user_id`가 이미 숫자인 로그 타입 | `native`, 조회 없음 |
| T16 | 한 행에서 `ItemSN`은 히트, `user_id`는 미스 | 통째로 격리, 두 상태 모두 보존 |
| T17 | 격리된 뒤에 매핑 행이 도착 | 재실행이 그 행을 구제, 그 행만 |
| T17-reload | 그 매핑 행을 `SYSTEM RELOAD DICTIONARY` 없이 추가 | 보이지 않음 -- unmapped와 구별 불가. 그래서 reload가 각주가 아니라 문서화된 절차 |
| T18 | 변환을 두 번 실행 | 동일한 출력. 타깃 SN이 다시 변환되지 않음 |
| T18-inplace | 같은 일을 `ALTER ... UPDATE`로 | 이중 변환이 실제로 발생 |
| T19 | 매핑을 RAM이 아니라 디스크에 | 동일한 행, 다이제스트까지 일치 |
| T19-join | 딕셔너리 없이 스필하는 `JOIN` | 역시 동일한 행 |
| T20 | 보존 | 필드별 `rows_in = translated + not_applicable + quarantined` |
| T21 | `user_id`가 아예 없음 | `not_applicable` |

T18과 T20이 직접 작성한 변환에서 조용히 실패하는 두 케이스이고, 이것이 코드 리뷰가
아니라 테스트 하네스가 필요한 이유입니다.

### 실행

```bash
cd labs/elastic-migration/data/idmap
./test_cases.py                      # _base/의 목적지 ClickHouse에 대해 매트릭스 실행
./test_cases.py --keep               # 들여다볼 수 있게 데이터베이스를 남김
./bench.py --map-rows 5000000 --fact-rows 2000000
```

케이스별 `PASS`/`FAIL`, `_base/bin/check.sh`와 같은 규칙입니다. 매트릭스 전체를 세 번
실행합니다 -- 메모리 딕셔너리, 디스크 기반 딕셔너리, 딕셔너리 없는 `JOIN`. 레이아웃은
비용을 바꿔야 하고 답을 바꾸면 안 되기 때문입니다.

| 파일 | 내용 |
|---|---|
| `schema.sql` | 원본·매핑·출력·격리·스테이징 테이블과 네 개의 딕셔너리 |
| `preflight.sql` | 매핑 테이블 건강 검사 한 쿼리: `(check, severity, n, detail)` |
| `translate.sql` | `dictGet`을 쓰는 변환 |
| `translate_join.sql` | 같은 변환을 스필하는 `JOIN`으로, 딕셔너리 없이 |
| `test_cases.py` | 위 매트릭스, 실행 가능한 형태 |
| `bench.py` | 같은 작업을 네 방식으로, 메모리와 소요 시간 측정 |

### 변환 전에 매핑 테이블을 검사하세요

`preflight.sql`은 `(check, severity, n, detail)`을 돌려주는 한 개의 쿼리입니다. 여기
있는 모든 항목은 오류가 아니라 **그럴듯한 오답**을 만들어냅니다. 그래서 런북의 한 줄이
아니라 검사입니다.

| 심각도 | 검사 | 왜 알아채기 어려운가 |
|---|---|---|
| `FAIL` | 한 소스 id에 서로 다른 타깃 둘 | 마지막에 병합된 행이 이깁니다. 결과는 안정적이고, 틀렸고, 맞은 것처럼 보입니다 |
| `FAIL` | 정규형이 아닌 `user_id` 키 | 모든 조회가 먼저 정규화하므로, 키가 정규형이 아닌 매핑은 가득 차 보이면서 **아무것도** 맞지 않습니다 |
| `FAIL` | 소스 id이기도 한 타깃 id | 이중 변환 위험의 정량화. T18-inplace가 보여주는 실수의 여지입니다 |
| `WARN` | 여러 소스 id가 한 타깃을 공유 | 그 아이템들을 합칩니다 -- 의도한 중복 제거일 수도, 조인 조건을 잃은 매핑 추출일 수도 |
| `INFO` | 항등 행 수, 전체 행 수 | **전부** 항등인 매핑은 보통 추출 시 같은 테이블을 자기 자신과 조인한 경우이고, 그러면 마이그레이션이 완벽히 성공한 것처럼 보입니다 |

**매핑 테이블이 평범한 `MergeTree`인 것은 결정입니다.** "소스 id당 한 행"에 당연해
보이는 `ReplacingMergeTree`는 첫 번째 검사가 의지하는 증거를 없애버립니다. 26.6.8.7에서
측정: 한 `INSERT`에 들어간 충돌하는 두 행은 즉시 **한 행**으로 돌아오고, 별도
INSERT라면 병합이 접기 전까지만 둘 다 보입니다. 즉 `ReplacingMergeTree`에서는 모호성
검사가 무엇이라도 볼 수 있는지가 병합 스케줄러와의 경주입니다. `MergeTree`에서는 같은
쌍을 다시 추출해도 해롭지 않은 동일 중복만 남고(검사는 **서로 다른** 타깃에만 반응),
중복 제거는 검사를 통과한 뒤의 의도적인 단계가 됩니다.

**모든 딕셔너리에 `LIFETIME(0)`** -- 매핑을 한 번 적재하고, 긴 마이그레이션의 모든
청크가 같은 버전의 매핑으로 변환되게 합니다. 대가는 나중에 추가된 매핑 행이
`SYSTEM RELOAD DICTIONARY` 전까지 보이지 않는다는 것이고, 리로드되지 않은 딕셔너리는
unmapped와 똑같아 보입니다. 그래서 T17-reload가 그것까지 검증합니다.

### 매핑을 어디에 둘지 고르기

네 가지 전략, 같은 데이터, 같은 답. 고정된 목적지에서 측정했습니다 --
ClickHouse **26.6.8.7**, 서버 메모리 7.75 GiB, 쿼리와 딕셔너리가 함께 쓸 수 있는
상한은 약 4.5 GiB입니다.

**들어가는 매핑: `ItemSN` 500만 + `user_id` 500만 행**
(디스크 38 MiB + 171 MiB), 원본 200만 행 변환:

| 전략 | 변환 시간 | rows/s | 쿼리 최대 메모리 | 딕셔너리 상주 | 상주 키 수 |
|---|---|---|---|---|---|
| `HASHED` / `COMPLEX_KEY_HASHED` | 1.6s | 1,261,181 | 322.6 MiB | 1024.0 MiB | 10,000,000 |
| `SSD_CACHE` / `COMPLEX_KEY_SSD_CACHE` | 12.8s | 152,801 | 297.5 MiB | 962.0 MiB | 1,600,000 |
| `JOIN`, `grace_hash` | 4.4s | 449,773 | 738.5 MiB | -- | -- |

**들어가지 않는 매핑: 2000만 + 2000만 행**
(디스크 154 MiB + 681 MiB), 원본 50만 행 변환:

| 전략 | 변환 시간 | rows/s | 쿼리 최대 메모리 | 딕셔너리 상주 | 상주 키 수 |
|---|---|---|---|---|---|
| `HASHED` | **들어가지 않음** | -- | -- | -- | -- |
| `SSD_CACHE` | 4.6s | 107,450 | 98.9 MiB | 626.0 MiB | 40,000,000 중 400,000 |
| `JOIN`, `grace_hash`, 초기 버킷 32 | 5.5s | 89,892 | 656.9 MiB | -- | -- |
| `JOIN`, `grace_hash`, 기본 버킷 | **들어가지 않음** | -- | -- | -- | -- |

`HASHED`는 변환 중에 실패한 것이 아닙니다. 한 행도 변환하기 전에
`SYSTEM RELOAD DICTIONARY`에서 실패했습니다:
`would use 5.40 GiB ... maximum: 4.54 GiB`. "매핑 테이블이 메모리에 안 들어간다"에
대한 정직한 형태의 답입니다 -- 메모리 레이아웃은 느려지지 않고 그냥 거부합니다.

나머지를 읽는 법:

- **`SSD_CACHE`의 사용량은 매핑 크기가 아니라 설정값입니다.** 같은 딕셔너리가
  `test_cases.py`의 19행 픽스처에서 각 257 MiB, 4000만 키에서 626 MiB를
  할당합니다. 그 한도는 `FILE_SIZE`, `BLOCK_SIZE`, `MAX_PARTITIONS_COUNT`,
  `WRITE_BUFFER_SIZE`가 정하고 매핑 테이블 크기는 정하지 않습니다. 키를 40만 개만
  들고 있는 것은 데이터가 그만큼만 건드렸기 때문이고, 작업 집합과 매핑 크기의 이
  차이가 이 레이아웃이 존재하는 이유 전부입니다.
- **처리량은 5~8배 느립니다.** 그게 지불하는 대가입니다. 시간 단위로 도는
  마이그레이션에서 "아예 못 돌리는 것"보다는 대개 싼 쪽입니다.
- **`JOIN`은 아무것도 상주시키지 않고**, 정의·리로드·동기화할 딕셔너리가 없으며,
  대신 쿼리마다 비용을 냅니다. 큰 매핑에서는 **설정 하나가** 돌아가는지 여부를
  결정했습니다: `grace_hash_join_initial_buckets = 32`는 657 MiB로 5.5초에
  끝냈고, 기본값은 7.36 GiB를 요구하다 종료됐습니다. grace_hash는 버킷을 스스로
  늘리지만, 늘리는 것은 시도한 **뒤**이고 종료되는 것은 그 첫 시도입니다.

| 상황 | 선택 |
|---|---|
| 매핑이 RAM에 여유롭게 들어가고, 긴 실행 동안 조회가 많음 | `HASHED` / `COMPLEX_KEY_HASHED` |
| 매핑이 들어가지 않고, 작업 집합이 훨씬 작음 | `SSD_CACHE` / `COMPLEX_KEY_SSD_CACHE` |
| 전체를 한 번에 일괄 변환하고 이후 유지할 것이 없음 | `grace_hash` `JOIN` (초기 버킷을 올릴 것) |
| 매핑이 자체 데이터베이스(MySQL, Postgres)에 있고 조회가 적음 | `DIRECT` / `COMPLEX_KEY_DIRECT` -- 완결성을 위해 적었고, 여기서 측정하지는 않았습니다 |

**상주하는 딕셔너리는 다른 모든 것과 같은 예산에서 나갑니다.** 이 숫자들을 얻는
과정에서 측정된 사실: 네 개의 딕셔너리가 모두 붙어 있을 때(합쳐 1.7 GiB)
`JOIN` 실행이 서버 상한에서 종료됐습니다. "JOIN이 안 들어간다"로 읽히지만 실제로
안 들어간 것은 앞선 두 실행이 남긴 잔여물이었습니다. `bench.py`는 해당 전략이 쓰지
않는 딕셔너리를 detach하고, 실제 마이그레이션도 같은 방식으로 생각해야 합니다.
`LIFETIME(0)` 딕셔너리는 누군가 detach할 때까지 계속 상주합니다.

`./bench.py --only ssd`는 프로세스당 전략 하나를 측정합니다. 어떤 전략이 메모리를
초과하는 크기에서는 그것만이 정직한 측정입니다 -- 실패한 시도가 남긴 것이 다음
시도에 영향을 주기 때문입니다.

### Verified on

**ClickHouse 26.6.8.7**(`_base/`의 고정된 마이그레이션 목적지. ClickHouse Cloud
regular release 채널이 도는 26.6 라인과 같은 minor), ClickStack/HyperDX 2.39.1은
실행 중이지만 이 경로에는 관여하지 않습니다. **Elasticsearch는 전혀 관여하지
않습니다.** 그게 설계입니다 -- 변환은 전송 중이 아니라 적재 후 ClickHouse 안에서
일어납니다.

`./test_cases.py`: **70개 단정, 실패 0.** 매트릭스 전체를 세 번 실행합니다 --
메모리 딕셔너리, 디스크 기반 딕셔너리, 딕셔너리 없는 `JOIN` -- 그리고 세 결과가
다이제스트까지 동일했습니다. 기능이 동작함이 아니라 **위험이 존재함**을 단정하는
케이스들도 문서대로 여전히 실패하는지 확인했습니다.

- `ALTER TABLE ... UPDATE`를 두 번 실행하면 `1001` → `77001` → `66001`이 되고,
  어디에도 오류가 없었습니다(T18-inplace)
- `SYSTEM RELOAD DICTIONARY` 없이 추가한 매핑 행은 보이지 않았고, unmapped와
  구별할 수 없었습니다(T17-reload)
- `ReplacingMergeTree`에 한 `INSERT`로 넣은 충돌하는 두 매핑 행은 즉시 한 행
  `[88002]`로 돌아왔습니다(T7-engine)

`./bench.py`: 위 절의 숫자들, 두 가지 매핑 크기에서 측정.

### ClickHouse Cloud에서

2026-10-06에 ClickHouse Cloud 서비스 **26.6.1.2292**에서 실행했습니다. 8 GiB
replica 2개(자동 확장 8–120 GiB)이고, 다른 데모 데이터베이스와 함께 쓰는
서비스입니다. 임시 데이터베이스에 200,000행 매핑을 넣고 100,000행을 더 넣은 뒤
지웠습니다(#41). `test_cases.py`와 `bench.py`는 거기서 돌리지 않았습니다.

- **`SSD_CACHE`는 `schema.sql`의 `PATH`로 동작합니다.** 1 MiB 쓰기 버퍼를 훨씬
  넘는 300,000개 항목이 두 replica 모두에서 올바르게 변환됐고, replica마다
  305 MiB였습니다. 위에서 말한 "설정한 만큼"의 사용량입니다.
  `/var/lib/clickhouse/user_files/` 밖의 `PATH`는 거부되므로(`PATH_ACCESS_DENIED`)
  그 안에 두세요. 위 표는 그대로입니다.
- **딕셔너리는 replica마다 따로 있고, `SYSTEM RELOAD DICTIONARY`는 하나만 다시
  로드합니다.** 매핑 행 100,000개를 더 넣은 뒤 `test_cases.py`와 `bench.py`가 쓰는
  보통의 리로드를 하자 replica 하나만 고쳐졌습니다. 다른 replica는 200,000행짜리
  사본을 그대로 들고 새 키를 변환하지 못했고, 오류도 없었습니다.
  `ON CLUSTER 'default'`로 리로드하자 둘 다 고쳐졌습니다. 그 전 시도에서는 매핑의
  `INSERT` 직후 만든 `HASHED` 딕셔너리가 한 replica에서 0행으로 로드됐고,
  `LIFETIME(0)` 때문에 그대로 남았습니다. 그러니 Cloud에서는
  `ON CLUSTER 'default'`로 리로드하고, 변환 전에 모든 replica를 확인하세요:

  ```sql
  SELECT hostName(), name, status, element_count
  FROM clusterAllReplicas('default', system.dictionaries)
  WHERE database = '<db>' AND name IN ('item_sn_dict_hashed', 'user_id_dict_hashed')
  ```

  모든 행에서 `element_count`가 매핑의 `count()`와 같아야 합니다. (`SSD_CACHE`의
  개수는 매핑이 아니라 지금까지 조회된 항목 수입니다.)
- **메모리: 상한은 replica마다 있고, 로컬과 비슷합니다.** replica 8 GiB에서
  `max_server_memory_usage`는 7.05 / 7.13 GiB였습니다. 서비스의 다른 작업이 이미
  약 1.8 GiB를 써서 여유는 약 5.3 GiB였고, 로컬은 4.54 GiB였습니다. 로컬에서
  `HASHED`는 20M + 20M에 5.40 GiB를 요구했고, replica마다 자기 사본을 로드합니다.
  Cloud에서는 재지 않았습니다. 서비스의 최소 크기에서는 경계선이고, 자동 확장이
  딕셔너리 로드에 맞춰 상한을 올려 주는지는 시험하지 않았습니다.

### 기본값이 아니라 결정이 필요한 것들

매트릭스는 고정이지만 아래는 아닙니다. 실제 실행 전에 정하는 게 좋습니다.

- **매핑에 없는 id는 중단 사유인가, 예상된 롱테일인가?** 여기서는 격리하고 계속
  진행하며, `parity_checks.py`의 개수 차이로 드러납니다. 매핑에 없는 id가 곧 매핑
  추출이 잘못됐다는 뜻이라면, 첫 건에서 멈추도록 바꾸세요.
- **마이그레이션 중에 매핑이 고정인가?** `LIFETIME(0)`은 그렇다고 가정합니다. id가
  계속 생성된다면, 서로 다른 시각에 변환된 청크는 서로 다른 매핑으로 변환됩니다.
- **역방향이 필요한가?** ClickHouse → Elasticsearch는 지원하지 않습니다. 반대로 키를
  잡은 딕셔너리 하나를 더 두면 되고, 추가는 싸지만 계속 맞게 유지하는 것은 공짜가
  아닙니다.
- **JSON 문자열로 온 숫자에 대한 선언된 규칙.** 여기서는 강제 변환하고
  표시합니다(`item_sn_coerced`). 대신 격리하는 것도 한 줄 변경이고, 문자열 `ItemSN`이
  생산자가 느슨한 게 아니라 고장났다는 뜻이면 그게 맞습니다.
- **`0`과 `-1`의 의미.** 여기서는 "아이템 없음"으로 봅니다. 실제 데이터에서 유효한
  id라면 그 가정이 틀렸고 T10이 실패해야 합니다.
