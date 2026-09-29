# AGENTS.md — labs/elastic-migration

[English](#english) | [한국어](#한국어)

## English

**Two different jobs. Read the right file.**

| You are… | Read |
|---|---|
| **running a real migration** with this lab | this file, then the sections it points you at |
| changing this lab (adding a tool, fixing a script) | [`/AGENTS.md`](../../AGENTS.md) at the repository root |

If you are running a migration: nothing here is style advice. Every rule below
exists because breaking it produces a migration that **looks finished**. That
is the failure mode this lab is built against — not crashes, which you would
notice.

### What this lab does not do

Read this before deciding it covers your migration. The whole lab is built on
one rule -- never emit something plausible for a case that did not convert --
and this section applies that rule to the documentation itself.

| Not covered | What that means for you |
|---|---|
| **Schema design.** The sort key is now *measured and flagged* -- `mapping_to_ddl.py` ranks the candidate prefix columns by cardinality and coverage and marks the default `NEEDS REVIEW` -- but it is still not **decided**, because which of those your queries filter on is not in the mapping. No TTL and no codecs are emitted at all. | Give the sort key with `--order-by` once you know the query pattern, and decide the TTL from the retention answer. A low-cardinality prefix is not a free win: measured on this repository's seed it cost 3% more disk than the time column alone (see `data/README.md`). |
| **Scale.** Verified against 300,000 documents (2,000,000 rows for `idmap/`). The ceiling this lab exists to get past is ten million. | The mechanisms are built for scale and were measured below it. Expect to find things at your volume that a 300,000-row run cannot show: PIT keep-alive under load, shard-to-slice ratios, insert pressure. |
| **ClickHouse Cloud specifics.** The `s3()` load, `SSD_CACHE(PATH …)` and the memory ceiling were all measured against a local single node. | The three claims you most want to rely on at real scale are the three never executed against the destination. [#41](https://github.com/litkhai/clickstack-hyperdx-hols/issues/41) |
| **An index pattern whose indices have different mappings** -- now *detected*: `plan.py` refuses a field with two types across the pattern and warns about a field only some indices have, and `mapping_to_ddl.py` reads a pattern whose mappings are identical while refusing one whose mappings differ. | What it still does not do is *merge* two shapes into one table. Narrow the pattern and plan each group separately, or decide the column and pass `--allow-mapping-conflicts`. |
| **Ingest.** Nothing converts Beats, Logstash or Elastic Agent configuration. | If your pipeline lives in Elastic rather than in front of it, that side is unbuilt. [#21](https://github.com/litkhai/clickstack-hyperdx-hols/issues/21) |
| **Dashboards and alerts.** Kibana saved objects are a separate problem. | [#5](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5) |
| **ID translation is not a tracked stage.** `run.py` tracks export/load/verify; `idmap/` is SQL you run. | The step most likely to need a re-run is the one with no record of what has been done. [#33](https://github.com/litkhai/clickstack-hyperdx-hols/issues/33) |
| **A live cutover.** The export's resume assumes a static source index. | Dual-write, and reading while writing, are out of scope -- a resumed slice opens a *new* point-in-time, so rows can be skipped or repeated if the index moved underneath. |
| **The reverse direction.** ClickHouse → Elasticsearch is not supported anywhere here. | |
| **Anything that needs the business.** Retention window, whether an unmapped id stops the migration, what `0` means. | See "Ask a human" below. These change the result, not the implementation. |

Hitting one of these is not a bug in your run -- it is the edge of what has
been built and verified. The issue number is where to say that you hit it.

### Where to look

Read the one section you need. Do not read the whole lab first.

| If you need to know… | Read |
|---|---|
| whether this dataset can move in one pass, and how long it takes | [`data/README.md`](data/README.md) § `plan.py` |
| which Elasticsearch types convert, and which need a human | [`data/README.md`](data/README.md) § `mapping_to_ddl.py` |
| how the export survives a network error or an OOM | [`data/README.md`](data/README.md) § `export.py`, § `run.py` |
| where memory goes, and which number to lower | [`data/README.md`](data/README.md) § "Where memory goes, and the one dial" |
| how to tell whether every row arrived | [`data/README.md`](data/README.md) § `parity_checks.py` |
| what to do when a run died halfway | [`data/README.md`](data/README.md) § `run.py`, and `./run.py --status` |
| how to handle ids that differ between the two systems | [`data/idmap/README.md`](data/idmap/README.md) |
| where the mapping table should live when it exceeds RAM | [`data/idmap/README.md`](data/idmap/README.md) § "Choosing where the mapping lives" |
| what could be silently wrong in a mapping table | [`data/idmap/preflight.sql`](data/idmap/preflight.sql) — the comments are the reasons |
| what the translation is asserted to do | [`data/idmap/test_cases.py`](data/idmap/test_cases.py) — the case matrix, executable |
| how to bring up a source and a target locally | [`../../_base/README.md`](../../_base/README.md) |
| what the official docs already cover | [`README.md`](README.md) § "Start with the official documentation" |

**Read the official ClickHouse documentation first.** This lab does not
restate it and is not a substitute for it. It fills one gap: the documented
JSON-over-HTTP path states its own ceiling at roughly ten million rows, and
real datasets start above it.

### The order

Each step's output is the next step's input, and two of them are human
checkpoints rather than commands.

1. **Stand up source and target**, or point at the real ones.
   `cd _base && docker compose --profile elastic up -d && ./bin/check.sh`.
   A `SKIP` in `check.sh` is not a `PASS`.
2. **Size it.** `./plan.py --index '<pattern>' --target-rows 5000000 --out plan.json`.
   Read the warnings. Do not start until the calibrated estimate and the chunk
   count are numbers someone has seen.
3. **Convert the mapping.** `./mapping_to_ddl.py ... --manifest manifest.json > ddl.sql`.
   **Human checkpoint.** The classification report on stderr is a decision, not
   an output: every `needs review` field is a place where ClickHouse behaves
   differently at the edges, and every `unsupported` field is commented out of
   the DDL on purpose. Do not uncomment one to make the DDL run.
4. **Create the table.** Send `ddl.sql` to the target.
5. **Move it.** `./run.py --plan plan.json --table <t> --manifest manifest.json`.
   Re-run the same command to resume. `./run.py --plan plan.json --table <t> --status`
   answers "is it progressing, what is stuck" and needs neither cluster.
6. **Check the whole table.** `./parity_checks.py ...`. Four query pairs, one
   per system.
7. **Translate ids, if they differ.** [`data/idmap/`](data/idmap/): `preflight.sql`
   first, then `translate.sql`, then read the quarantine.
   **Human checkpoint.** A non-empty quarantine is a question for a person, not
   a number to drive to zero.

Steps 2–6 are in `data/`. Step 7 is in `data/idmap/` and happens **after** the
load, in ClickHouse — never in flight, and never as an in-place update.

### Invariants

Each of these has a failure attached. That is why it is a rule.

1. **Never write a `Verified on …` line for a run that did not happen.** Record
   both versions — ClickHouse and ClickStack/HyperDX, plus Elasticsearch here —
   and say what you did not run. A migration is the one place where "it worked
   in the lab" has to mean something.
2. **Never translate an id in place.** Read the raw table, write a different
   table. `ItemSN`-shaped ids have the same type *and* the same domain on both
   sides, so an already-translated value is a valid source id: an
   `ALTER TABLE … UPDATE` run twice takes `1001 → 77001 → 66001` with no error
   anywhere. Everything else here is built to be re-run after a failure, which
   is exactly what makes this dangerous.
3. **Never make a mapping table `ReplacingMergeTree`.** It collapses two
   conflicting rows for one source id on `INSERT`, so the ambiguity check
   cannot see what it exists to refuse. Plain `MergeTree`, then dedupe
   deliberately after `preflight.sql` passes.
4. **`SYSTEM RELOAD DICTIONARY` after changing a mapping table.** Every
   dictionary here is `LIFETIME(0)` on purpose — one version of the map for the
   whole run. The cost is that a new mapping row is invisible until reloaded,
   and invisible is indistinguishable from unmapped.
5. **Never regenerate `plan.json` mid-run.** Chunk ids are positional; a new
   plan renumbers them and the state file would describe ranges that no longer
   exist. `run.py` refuses this rather than mixing two chunk sets — do not work
   around it with a fresh `--state`.
6. **Never mix `CH_TARGET_*` and `CH_*`.** Take a target connection whole or
   not at all. Half from each is how a load lands in the wrong server with a
   plausible-looking log, and a `.env` pointed at a Cloud service is a normal
   state for this repository.
7. **An id with no mapping row is quarantined, never passed through.** And a
   missing id is `NULL` with a status, never `0`. `0` is the value a schema
   check cannot distinguish from a real answer.
8. **A `SKIP` is not a `PASS`,** in `check.sh`, `parity_checks.py` and
   `test_cases.py` alike. If a check skipped, either make it run or say it did
   not run.
9. **Do not lower a guard to make a check pass.** `--force-unlock` on a lock
   held by a live process, deleting an ambiguous mapping row instead of
   deciding about it, removing a `needs review` comment — each turns a caught
   problem into an uncaught one.
10. **Do not size from `_cat/indices`.** `docs.count` counts one Lucene
    document per `nested` element: 750,255 for 300,000 documents on this
    repository's own seed, a 2.5× overestimate. `plan.py` uses `_count`.
11. **One strategy per process when measuring memory.** A `LIFETIME(0)`
    dictionary stays resident, so measuring several layouts in one run measures
    the last one plus the leftovers — which is how "the JOIN does not fit"
    turned out to be 1.7 GiB of dictionaries from earlier runs.
12. **One `run.py` per state file.** Two would double-load parts and interleave
    state writes; the symptom, a row count that is too high, reads like a
    migration bug rather than an operator mistake.

13. **Never make the source cluster less secure to get a tool to connect, and
    never put a credential in a URL or a command line.** Security is on by
    default in 8.x; the tools take `ES_USER`/`ES_PASSWORD` or `ES_API_KEY`
    from the environment and refuse a URL that carries credentials. An API
    key scoped to cluster `[monitor]` and index
    `[read, view_index_metadata, monitor]` is enough to export, so there is
    no reason to use an administrator's password -- and `--es-insecure`
    exists for a self-signed certificate, not as a substitute for
    `ES_CA_CERT`.

### When something fails

| Symptom | What it means | What to do |
|---|---|---|
| OOM during export | `--batch-size` × document size × `--slices` does not fit | lower `--batch-size`. `plan.py` reports bytes per document, so this is arithmetic, not a guess |
| a chunk failed | the cause is in the state file, one line, chosen not truncated | `./run.py --status`, fix the cause, then `--only <ids>` to retry exactly those |
| the whole run died | nothing is lost: export resumes from slice checkpoints, load skips loaded parts | re-run the same command. A lock left by a killed run is reclaimed automatically when its process is gone |
| `WARNING: slice(s) [n] exported 0 rows` | usually fewer live shards than slices; occasionally a real slicing bug | check the shard count. This is the silent-undercount case, so do not ignore it |
| a dictionary will not load (`would use … GiB`) | the in-memory layout does not degrade, it declines | switch that map to `SSD_CACHE` / `COMPLEX_KEY_SSD_CACHE`, or use the `JOIN` strategy |
| the `JOIN` strategy is killed for memory | `grace_hash` grows its buckets *after* trying, and the first try is what dies | raise `grace_hash_join_initial_buckets` (32 turned 7.36 GiB into 657 MiB here) |
| the plan is refused over conflicting field types | the pattern's indices disagree about a field: one ClickHouse column cannot hold both | narrow the pattern and plan each group separately. `--allow-mapping-conflicts` is for after you have decided what that column should be, not for getting past the message |
| `401` from Elasticsearch | no credential, or the wrong one. Security is on by default in 8.x | set `ES_USER`/`ES_PASSWORD` or `ES_API_KEY`. Never disable security on the source to get past this |
| `403` naming an action, not a privilege | the credential is too narrow | cluster `[monitor]` plus index `[read, view_index_metadata, monitor]`. The index-level `monitor` is the one usually missing |
| `CERTIFICATE_VERIFY_FAILED` | 8.x uses its own CA, which your trust store does not have | copy `config/certs/http_ca.crt` out of the cluster and pass it as `ES_CA_CERT` |
| rows in quarantine | ids the map does not cover, or values that are not ids | read `reason`. Fix the mapping table, reload the dictionary, re-run the translation: it rescues those rows and only those |
| `preflight.sql` FAILs | the mapping table will produce plausible wrong answers | fix the map. Do not translate past a FAIL |
| counts do not add up | `rows_in = translated + not_applicable + quarantined` is broken | stop. This is the check that catches silent loss when the output looks right |

### Ask a human

Do not answer these by default. Each changes the result rather than the
implementation, and getting one wrong is not visible in the output.

- **Does everything move, or only the retention window?** The rest can stay on
  a read-only cluster, which can shrink this by an order of magnitude.
- **Do both systems run in parallel for a period?** That adds dual-write, and
  it breaks the static-source assumption the export's resume relies on.
- **Is an unmapped id a stopping error or an expected long tail?**
- **Is the mapping stable during the migration?** `LIFETIME(0)` assumes yes. If
  ids are still being minted, chunks translated at different times are
  translated against different maps.
- **Is the reverse direction ever needed?** Nothing here supports
  ClickHouse → Elasticsearch.
- **What should happen to a JSON string-encoded number** (`"1005"` where a
  number was expected)? This coerces and flags it; quarantining instead is a
  one-line change.
- **Are `0` and `-1` really "no item"?** They are treated that way. If they are
  real ids, that assumption is wrong and the test case that encodes it should
  fail.
- **Which versions?** The source cluster's, and the destination's. Verifying
  against a ClickHouse *newer* than the destination can prove a feature the
  destination does not have — see `_base/README.md` on why the target is pinned
  separately.

### Done looks like this

Not a feeling. A list:

- [ ] `./run.py --status` says every chunk `verified`, and exits 0
- [ ] `./parity_checks.py` is all `PASS`, with no `SKIP` left unexplained
- [ ] `rows_in = translated + not_applicable + quarantined` holds (`test_cases.py`'s T20, on your data)
- [ ] the quarantine is empty, or every row in it has a decision attached
- [ ] `preflight.sql` has no `FAIL`, and its `WARN`s have been read
- [ ] the numbers are written down: rows, chunks, versions of both systems, and what was **not** run

---

## 한국어

**서로 다른 두 가지 일입니다. 맞는 파일을 읽으세요.**

| 당신이 하는 일 | 읽을 것 |
|---|---|
| 이 랩으로 **실제 마이그레이션을 실행** | 이 파일, 그리고 이 파일이 가리키는 절 |
| 이 랩 자체를 변경(도구 추가, 스크립트 수정) | 저장소 루트의 [`/AGENTS.md`](../../AGENTS.md) |

마이그레이션을 실행하는 경우: 여기 있는 것은 스타일 조언이 아닙니다. 아래 모든
규칙은 어겼을 때 **완료된 것처럼 보이는** 마이그레이션이 나오기 때문에 존재합니다.
이 랩이 대비하는 실패는 그것이고, 크래시가 아닙니다. 크래시는 알아챌 수 있습니다.

### 이 랩이 하지 않는 것

여러분의 마이그레이션을 이 랩이 덮는다고 판단하기 전에 읽으세요. 이 랩 전체가 하나의
원칙 위에 있습니다 -- **변환되지 않은 것에 그럴듯한 결과를 내놓지 않는다** -- 그리고
이 절은 그 원칙을 문서 자신에게 적용한 것입니다.

| 다루지 않는 것 | 그것이 의미하는 바 |
|---|---|
| **스키마 설계.** 정렬 키는 이제 **측정되고 표시**됩니다 -- `mapping_to_ddl.py`가 후보 접두 컬럼을 카디널리티·존재 비율로 순위 매기고 기본값에 `NEEDS REVIEW`를 붙입니다 -- 하지만 여전히 **결정되지는** 않습니다. 그중 무엇을 여러분의 쿼리가 필터하는지는 매핑에 없기 때문입니다. TTL과 코덱은 아예 생성하지 않습니다. | 쿼리 패턴을 알게 되면 `--order-by`로 정렬 키를 주고, TTL은 보존 기간 답으로 정하세요. 저카디널리티 접두는 공짜 이득이 아닙니다: 이 저장소 시드에서 측정하니 시간 컬럼만 쓸 때보다 디스크를 3% 더 썼습니다(`data/README.md` 참고). |
| **규모.** 문서 300,000건(`idmap/`은 2,000,000행)으로 검증했습니다. 이 랩이 넘어서려는 천장은 1천만입니다. | 메커니즘은 대규모를 위해 만들었지만 측정은 그 아래에서 했습니다. 30만 행으로는 드러나지 않는 것들 -- 부하 상태의 PIT keep-alive, 샤드 대 슬라이스 비율, INSERT 부하 -- 이 여러분 볼륨에서 나올 수 있습니다. |
| **ClickHouse Cloud 고유 부분.** `s3()` 적재, `SSD_CACHE(PATH …)`, 메모리 상한은 모두 로컬 단일 노드에서 측정했습니다. | 대규모에서 가장 의지하고 싶은 세 주장이 정작 목적지에서 실행되지 않은 셋입니다. [#41](https://github.com/litkhai/clickstack-hyperdx-hols/issues/41) |
| **매핑이 서로 다른 인덱스 패턴** -- 이제 **감지합니다**: `plan.py`는 패턴 안에서 타입이 둘인 필드를 거부하고 일부 인덱스에만 있는 필드를 경고하며, `mapping_to_ddl.py`는 매핑이 동일한 패턴은 읽고 다른 패턴은 거부합니다. | 여전히 하지 않는 것은 두 모양을 한 테이블로 **합치는** 것입니다. 패턴을 좁혀 그룹별로 계획하거나, 컬럼을 정한 뒤 `--allow-mapping-conflicts`를 주세요. |
| **입수(ingest).** Beats·Logstash·Elastic Agent 설정을 변환하는 것은 없습니다. | 파이프라인이 Elastic 앞이 아니라 Elastic 안에 있다면 그쪽은 미작성입니다. [#21](https://github.com/litkhai/clickstack-hyperdx-hols/issues/21) |
| **대시보드와 알림.** Kibana saved object는 별도 문제입니다. | [#5](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5) |
| **ID 변환이 추적되는 단계가 아님.** `run.py`는 export/load/verify를 추적하고, `idmap/`은 실행하는 SQL입니다. | 재실행이 가장 필요한 단계가 무엇을 했는지 기록이 없는 단계입니다. [#33](https://github.com/litkhai/clickstack-hyperdx-hols/issues/33) |
| **무중단 전환.** 내보내기의 재개는 원본 인덱스가 정적임을 가정합니다. | 이중 기록과 "쓰면서 읽기"는 범위 밖입니다. 재개된 슬라이스는 **새** point-in-time을 열기 때문에, 인덱스가 그사이 움직였다면 행이 빠지거나 중복될 수 있습니다. |
| **역방향.** ClickHouse → Elasticsearch는 어디에서도 지원하지 않습니다. | |
| **업무 판단이 필요한 모든 것.** 보존 기간, 매핑에 없는 id가 중단 사유인지, `0`이 무슨 뜻인지. | 아래 "사람에게 물어야 하는 것"을 보세요. 구현이 아니라 결과를 바꾸는 것들입니다. |

이 중 하나에 부딪히는 것은 여러분 실행의 버그가 아니라 **만들고 검증한 범위의 끝**
입니다. 부딪혔다고 말할 곳이 그 이슈 번호입니다.

### 어디를 봐야 하는가

필요한 절만 읽으세요. 랩 전체를 먼저 읽지 마세요.

| 알아야 하는 것 | 읽을 곳 |
|---|---|
| 이 데이터를 한 번에 옮길 수 있는지, 얼마나 걸리는지 | [`data/README.md`](data/README.md) § `plan.py` |
| 어떤 Elasticsearch 타입이 변환되고 어떤 것이 사람을 필요로 하는지 | [`data/README.md`](data/README.md) § `mapping_to_ddl.py` |
| 네트워크 오류나 OOM을 내보내기가 어떻게 견디는지 | [`data/README.md`](data/README.md) § `export.py`, § `run.py` |
| 메모리가 어디로 가고 어떤 숫자를 낮춰야 하는지 | [`data/README.md`](data/README.md) § "메모리는 어디로 가고, 조절 다이얼은 하나" |
| 모든 행이 도착했는지 어떻게 확인하는지 | [`data/README.md`](data/README.md) § `parity_checks.py` |
| 실행이 중간에 죽었을 때 무엇을 할지 | [`data/README.md`](data/README.md) § `run.py`, 그리고 `./run.py --status` |
| 두 시스템에서 id가 다를 때 어떻게 할지 | [`data/idmap/README.md`](data/idmap/README.md) |
| 매핑 테이블이 RAM을 넘을 때 어디에 둘지 | [`data/idmap/README.md`](data/idmap/README.md) § "매핑을 어디에 둘지 고르기" |
| 매핑 테이블에서 조용히 잘못될 수 있는 것 | [`data/idmap/preflight.sql`](data/idmap/preflight.sql) — 주석이 곧 이유입니다 |
| 변환이 무엇을 보장하는지 | [`data/idmap/test_cases.py`](data/idmap/test_cases.py) — 실행 가능한 케이스 매트릭스 |
| 원본과 목적지를 로컬에 올리는 방법 | [`../../_base/README.md`](../../_base/README.md) |
| 공식 문서가 이미 다루는 것 | [`README.md`](README.md) § "공식 문서부터" |

**ClickHouse 공식 문서를 먼저 읽으세요.** 이 랩은 그것을 다시 쓰지 않고, 대체하지도
않습니다. 채우는 공백은 하나입니다: 문서화된 JSON·HTTP 경로가 스스로 밝힌 약 1천만
행의 한계, 그리고 실제 데이터는 그 위에서 시작한다는 사실입니다.

### 순서

각 단계의 출력이 다음 단계의 입력이고, 그중 둘은 명령이 아니라 **사람의 판단
지점**입니다.

1. **원본과 목적지를 올리거나** 실제 대상을 가리킵니다.
   `cd _base && docker compose --profile elastic up -d && ./bin/check.sh`.
   `check.sh`의 `SKIP`은 `PASS`가 아닙니다.
2. **규모를 잽니다.** `./plan.py --index '<패턴>' --target-rows 5000000 --out plan.json`.
   경고를 읽으세요. 캘리브레이션 추정치와 청크 수를 누군가 본 숫자로 만든 뒤에
   시작하세요.
3. **매핑을 변환합니다.** `./mapping_to_ddl.py ... --manifest manifest.json > ddl.sql`.
   **사람의 판단 지점.** stderr의 분류 리포트는 출력이 아니라 결정입니다. `needs
   review` 필드는 ClickHouse가 경계에서 다르게 동작하는 지점이고, `unsupported`
   필드는 의도적으로 DDL에서 주석 처리돼 있습니다. DDL을 돌리려고 주석을 풀지
   마세요.
4. **테이블을 만듭니다.** `ddl.sql`을 목적지에 보냅니다.
5. **옮깁니다.** `./run.py --plan plan.json --table <t> --manifest manifest.json`.
   같은 명령을 다시 실행하면 재개됩니다.
   `./run.py --plan plan.json --table <t> --status`는 "진행되고 있는가, 무엇이
   막혔는가"에 답하고 두 클러스터 모두 필요하지 않습니다.
6. **전체 테이블을 검증합니다.** `./parity_checks.py ...`. 시스템별로 하나씩 만든
   쿼리 쌍 네 개.
7. **id가 다르면 변환합니다.** [`data/idmap/`](data/idmap/): `preflight.sql` 먼저,
   그다음 `translate.sql`, 그다음 격리 테이블을 읽습니다.
   **사람의 판단 지점.** 비어 있지 않은 격리는 0으로 만들어야 할 숫자가 아니라
   사람에게 물어야 할 질문입니다.

2~6단계는 `data/`에, 7단계는 `data/idmap/`에 있고 적재 **후에** ClickHouse 안에서
일어납니다. 전송 중이 아니고, 제자리 갱신도 아닙니다.

### 불변식

각 항목에는 실패가 붙어 있습니다. 그래서 규칙입니다.

1. **실행하지 않은 것에 `Verified on …`을 쓰지 마세요.** 두 버전을 모두
   기록하고(ClickHouse와 ClickStack/HyperDX, 여기서는 Elasticsearch까지), 실행하지
   않은 것을 밝히세요. 마이그레이션은 "랩에서는 됐다"가 의미를 가져야 하는 유일한
   자리입니다.
2. **id를 제자리에서 변환하지 마세요.** 원본 테이블을 읽고 다른 테이블에 쓰세요.
   `ItemSN` 같은 id는 양쪽에서 타입도 값 영역도 같아서, 이미 변환된 값이 유효한
   소스 id입니다. `ALTER TABLE … UPDATE`를 두 번 실행하면 `1001 → 77001 → 66001`이
   되고 어디에도 오류가 없습니다. 나머지 전부가 실패 후 재실행을 전제로 만들어졌기
   때문에 더 위험합니다.
3. **매핑 테이블을 `ReplacingMergeTree`로 만들지 마세요.** 한 소스 id에 대한 충돌하는
   두 행을 `INSERT` 시점에 접어버려서, 모호성 검사가 거부해야 할 대상을 볼 수 없게
   됩니다. 평범한 `MergeTree`를 쓰고, `preflight.sql`이 통과한 뒤에 의도적으로 중복을
   제거하세요.
4. **매핑 테이블을 바꿨으면 `SYSTEM RELOAD DICTIONARY`.** 여기의 모든 딕셔너리는
   의도적으로 `LIFETIME(0)`입니다 -- 실행 전체가 하나의 매핑 버전을 씁니다. 대가는
   새 매핑 행이 리로드 전까지 보이지 않는다는 것이고, 보이지 않는 것은 unmapped와
   구별할 수 없습니다.
5. **실행 중간에 `plan.json`을 다시 만들지 마세요.** 청크 id는 위치 기반이라 새
   계획은 번호를 바꾸고, 상태 파일은 더 이상 없는 범위를 기술하게 됩니다. `run.py`는
   두 청크 집합을 섞지 않고 거부합니다 -- 새 `--state`로 우회하지 마세요.
6. **`CH_TARGET_*`와 `CH_*`를 섞지 마세요.** 목적지 접속 정보는 전부 아니면 전무로
   가져오세요. 절반씩 가져오면 그럴듯한 로그를 남기며 엉뚱한 서버에 적재됩니다.
   이 저장소에서 `.env`가 Cloud 서비스를 가리키는 것은 정상 상태입니다.
7. **매핑 행이 없는 id는 통과시키지 않고 격리합니다.** 그리고 없는 id는 상태를 달고
   `NULL`이며, `0`이 아닙니다. `0`은 스키마 검사가 진짜 답과 구별할 수 없는 값입니다.
8. **`SKIP`은 `PASS`가 아닙니다.** `check.sh`, `parity_checks.py`, `test_cases.py`
   모두 마찬가지입니다. 건너뛰었다면 실행되게 만들거나, 실행되지 않았다고 말하세요.
9. **검사를 통과시키려고 안전장치를 낮추지 마세요.** 살아 있는 프로세스가 쥔 잠금에
   `--force-unlock`, 모호한 매핑 행을 판단하지 않고 삭제, `needs review` 주석 제거 --
   각각 잡힌 문제를 잡히지 않는 문제로 바꿉니다.
10. **`_cat/indices`로 규모를 재지 마세요.** `docs.count`는 `nested` 원소마다 Lucene
    문서를 하나씩 셉니다. 이 저장소 시드에서 문서 300,000건에 750,255 -- 2.5배
    과대추정입니다. `plan.py`는 `_count`를 씁니다.
11. **메모리를 측정할 때는 프로세스당 전략 하나.** `LIFETIME(0)` 딕셔너리는 계속
    상주하므로, 여러 레이아웃을 한 실행에서 재면 마지막 것 + 잔여물을 재게 됩니다.
    "JOIN이 안 들어간다"가 실은 앞선 실행이 남긴 1.7 GiB였던 경로입니다.
12. **상태 파일당 `run.py` 하나.** 둘이면 part를 이중 적재하고 상태 기록이
    교차합니다. 증상인 "너무 많은 행 수"는 운영자 실수가 아니라 마이그레이션
    버그처럼 읽힙니다.

13. **도구가 연결되게 하려고 원본 클러스터의 보안을 낮추지 말고, 자격증명을 URL이나
    명령행에 넣지 마세요.** 8.x는 보안이 기본 활성입니다. 도구들은
    `ES_USER`/`ES_PASSWORD` 또는 `ES_API_KEY`를 환경에서 읽고, 자격증명이 들어 있는
    URL은 거부합니다. 내보내기에는 클러스터 `[monitor]`와 인덱스
    `[read, view_index_metadata, monitor]`로 좁힌 API key면 충분하므로 관리자
    비밀번호를 쓸 이유가 없습니다. `--es-insecure`는 자체 서명 인증서를 위한 것이고
    `ES_CA_CERT`의 대체물이 아닙니다.

### 무언가 실패했을 때

| 증상 | 의미 | 할 일 |
|---|---|---|
| 내보내기 중 OOM | `--batch-size` × 문서 크기 × `--slices`가 안 들어감 | `--batch-size`를 낮추세요. `plan.py`가 문서당 바이트를 보고하므로 추측이 아니라 산수입니다 |
| 청크 하나 실패 | 원인이 상태 파일에 한 줄로, 잘라낸 게 아니라 고른 줄로 있음 | `./run.py --status`, 원인 수정, `--only <ids>`로 그것만 재시도 |
| 실행 전체가 죽음 | 잃은 것 없음: 내보내기는 슬라이스 체크포인트에서, 적재는 적재된 part를 건너뛰고 재개 | 같은 명령 재실행. 죽은 실행이 남긴 잠금은 프로세스가 사라졌으면 자동 회수됩니다 |
| `WARNING: slice(s) [n] exported 0 rows` | 보통 살아 있는 샤드가 슬라이스보다 적음. 드물게 진짜 슬라이싱 버그 | 샤드 수를 확인하세요. 조용한 과소 집계 경로이므로 무시하지 마세요 |
| 딕셔너리가 적재되지 않음(`would use … GiB`) | 메모리 레이아웃은 느려지지 않고 거부합니다 | 그 매핑을 `SSD_CACHE` / `COMPLEX_KEY_SSD_CACHE`로 바꾸거나 `JOIN` 전략을 쓰세요 |
| `JOIN` 전략이 메모리로 종료됨 | `grace_hash`는 시도한 **뒤** 버킷을 늘리고, 죽는 것은 그 첫 시도 | `grace_hash_join_initial_buckets`를 올리세요(여기서 32가 7.36 GiB를 657 MiB로) |
| 타입 충돌로 계획이 거부됨 | 패턴 안의 인덱스들이 한 필드에 대해 서로 다름. ClickHouse 컬럼 하나가 둘을 담을 수 없습니다 | 패턴을 좁혀 그룹별로 계획하세요. `--allow-mapping-conflicts`는 그 컬럼을 무엇으로 할지 **정한 뒤**에 쓰는 것이고, 메시지를 지나치기 위한 것이 아닙니다 |
| Elasticsearch가 `401` | 자격증명이 없거나 틀림. 8.x는 보안이 기본 활성 | `ES_USER`/`ES_PASSWORD` 또는 `ES_API_KEY`를 설정하세요. 이걸 넘기려고 원본 보안을 끄지 마세요 |
| 권한 이름이 아니라 액션 이름을 말하는 `403` | 자격증명 권한이 너무 좁음 | 클러스터 `[monitor]` + 인덱스 `[read, view_index_metadata, monitor]`. 보통 빠지는 것은 인덱스 레벨의 `monitor`입니다 |
| `CERTIFICATE_VERIFY_FAILED` | 8.x가 자체 CA를 쓰고 로컬 트러스트 스토어에는 없음 | 클러스터에서 `config/certs/http_ca.crt`를 꺼내 `ES_CA_CERT`로 넘기세요 |
| 격리에 행이 쌓임 | 매핑이 덮지 않는 id, 또는 id가 아닌 값 | `reason`을 읽으세요. 매핑 수정 → 딕셔너리 리로드 → 변환 재실행. 그 행들만 구제됩니다 |
| `preflight.sql`이 FAIL | 이 매핑 테이블은 그럴듯한 오답을 만들어냅니다 | 매핑을 고치세요. FAIL을 지나쳐 변환하지 마세요 |
| 개수가 맞지 않음 | `rows_in = translated + not_applicable + quarantined`가 깨짐 | 멈추세요. 출력이 맞아 보일 때 조용한 손실을 잡는 검사입니다 |

### 사람에게 물어야 하는 것

기본값으로 답하지 마세요. 각각 구현이 아니라 결과를 바꾸고, 잘못 정해도 출력에
드러나지 않습니다.

- **전체를 옮기는가, 보존 기간만 옮기는가?** 나머지는 읽기 전용 클러스터로 남길 수
  있고, 그러면 작업이 자릿수 단위로 줄어들 수 있습니다.
- **두 시스템을 일정 기간 병행 운영하는가?** 이중 기록이 추가되고, 내보내기 재개가
  의존하는 "원본이 정적" 가정이 깨집니다.
- **매핑에 없는 id는 중단 사유인가, 예상된 롱테일인가?**
- **마이그레이션 중 매핑이 고정인가?** `LIFETIME(0)`은 그렇다고 가정합니다. id가 계속
  생성되면 서로 다른 시각에 변환된 청크는 서로 다른 매핑으로 변환됩니다.
- **역방향이 필요한가?** ClickHouse → Elasticsearch는 지원하지 않습니다.
- **JSON 문자열로 온 숫자**(`"1005"`)를 어떻게 할까요? 여기서는 강제 변환하고
  표시합니다. 대신 격리하는 것은 한 줄 변경입니다.
- **`0`과 `-1`이 정말 "아이템 없음"인가?** 그렇게 취급합니다. 실제 id라면 그 가정이
  틀렸고, 그것을 담은 테스트 케이스가 실패해야 합니다.
- **버전은?** 원본 클러스터와 **목적지**의 버전. 목적지보다 **더 새로운**
  ClickHouse에서 검증하면 목적지에 없는 기능을 증명할 수 있습니다 -- 목적지를 따로
  고정한 이유는 `_base/README.md`를 보세요.

### 완료의 모습

느낌이 아니라 목록입니다.

- [ ] `./run.py --status`가 모든 청크 `verified`, 종료 코드 0
- [ ] `./parity_checks.py`가 전부 `PASS`, 설명되지 않은 `SKIP` 없음
- [ ] `rows_in = translated + not_applicable + quarantined` 성립(`test_cases.py`의 T20을 여러분 데이터에)
- [ ] 격리가 비었거나, 안의 모든 행에 결정이 붙어 있음
- [ ] `preflight.sql`에 `FAIL` 없고, `WARN`은 읽었음
- [ ] 숫자를 적어둠: 행 수, 청크 수, 양쪽 시스템의 버전, 그리고 **실행하지 않은 것**
