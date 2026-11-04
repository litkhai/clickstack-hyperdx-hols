# Filtering out log patterns in ClickStack

[English](#english) | [한국어](#한국어)

## English

Teams moving from Kibana often work like this: open Discover → **Patterns**, then click
**Filter out pattern** on the 10–30 noise patterns they already know, until only the unusual
logs are left. ClickStack has **Event Patterns** but no such button. This lab shows how far you
get today, and where a Lucene exclusion quietly removes logs you still needed.

> Verified on ClickStack all-in-one `2.39.1` (HyperDX 2.39.1, ClickHouse 26.8.7.19), local
> `_base` stack, 2026-11-02: `bin/check.sh` 11/11. HyperDX behaviour below is read from the
> `@hyperdx/app@2.39.1` source; the UI itself was not driven in this run, and every claim is
> checked with SQL.
>
> UI check, 2026-11-04 (same ClickStack all-in-one `2.39.1` image, ClickHouse 26.8.7.19, after
> `bin/check.sh` 11/11, headless Chromium): Event Patterns on the lab rows showed the nine templates
> with `~` counts and no include or exclude action; the five stacked Lucene phrases gave 10,000
> results with the `… order batch …` pattern gone; the five `NOT match(Body, patternToRegex(…))`
> conditions in SQL mode gave 20,000 results with the three patterns to keep and the signal left.

### What HyperDX 2.39.1 does under Event Patterns

Read from `packages/app/src/hooks/usePatterns.tsx` and `components/PatternTable.tsx`:

- It takes **10,000 rows** of the current search (`ORDER BY rand() LIMIT 10000`) and mines them
  **in the browser** with drain3 0.9.11 (Pyodide), default config plus every ASCII punctuation
  character as a delimiter.
- A variable token shows as `<*>`. The template is rebuilt over the last row's original text, so
  separators like `["`, `=` and `.` stay as they were.
- Counts are the sample count scaled to the total, hence `~72`. A pattern rarer than about one
  row in 10,000 may not show at all.
- The pattern table has no include or exclude action.

`bin/mine_patterns.py` repeats those steps outside the UI, so the lab can check them with SQL.

### The data

`sql/01-seed.sql` writes 200,000 rows into ClickStack's own `default.otel_logs` (tagged
`ResourceAttributes['lab']`, spread over the last 10 minutes), nine patterns in fixed shares:

| | Pattern (as Event Patterns shows it) | Rows |
|---|---|---|
| noise N1 | `payment capture skipped for order["<*>"] token="<*>.<*>"` | 40,000 |
| noise N2 | `cache miss for key user:<*>` | 40,000 |
| noise N3 | `GET /api/cart/<*> completed in <*> ms` | 50,000 |
| noise N4 | `health check ok for upstream 10.0.<*>.<*> in <*> ms` | 30,000 |
| noise N5 | `retrying publish to topic orders-<*> attempt <*>` | 20,000 |
| keep K1 | `payment capture skipped for order batch <*> after <*> retries` — same opening words as N1 | 10,000 |
| keep K2 | `payment capture failed for order["<*>"] code=<*>` | 8,000 |
| keep K3 | `cache miss for key config:<*>.<*> fallback to defaults` — same opening words as N2 | 1,800 |
| signal S1 | `inventory reservation timed out for sku <*> after <*> ms` | 200 |

Removing the five noise patterns should leave exactly 20,000 rows: K1–K3 and S1.

### Run it

```bash
cd _base && docker compose up -d && cd ..
cd labs/log-pattern-exclusion
bin/check.sh              # seed, mine, run every scenario; leaves the rows for the UI
bin/check.sh --cleanup    # same, then removes the lab rows, lab_patterns and patternToRegex
```

Needs `curl`, `python3` and `uv` (for `drain3==0.9.11`). With `CH_URL` unset the scripts use the
published local defaults in `_base/.env.example`. To look at the same rows in HyperDX: open
http://localhost:8080, the Logs source, *Last 15 minutes*, then **Event Patterns**.

### Step 1 — Lucene exclusions and the SQL they become

`-` goes directly in front of the word or phrase, with no space; a lone `-` is a syntax error.
Terms separated by spaces are ANDed. For the implicit field (Body), with ClickStack's default
`INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha')`, the 2.39.1 parser
emits (`sql/03-lucene.sql` runs each one):

| Lucene | Excludes | SQL (2.39.1) | Rows left |
|---|---|---|---|
| `-payment` | Body has the word | `NOT hasAllTokens(lower(Body), lower('payment'))` | 142,000 |
| `-"payment capture skipped for order"` | Body contains the phrase | `NOT (hasAllTokens(lower(Body), lower('…')) AND lower(Body) LIKE lower('%…%'))` | 150,000 |
| `-payment*` | Body starts with | `lower(Body) NOT LIKE lower('payment%')` | 142,000 |
| `-*skipped*` | Body contains | `lower(Body) NOT LIKE lower('%skipped%')` | 150,000 |

A field term without quotes, `-ServiceName:pay`, is a substring test (`ServiceName NOT ILIKE
'%pay%'`); with quotes, `-ServiceName:"payment"`, it is an exact value. `*` only works on an
unquoted single word. There is no regex in the Lucene bar.

### Step 2 — Why stacked phrases are not enough

The best you can do in Lucene is one fixed phrase per noise pattern:

```
-"payment capture skipped for order" -"cache miss for key user" -"completed in"
-"health check ok for upstream" -"retrying publish to topic"
```

That leaves **10,000** rows, not 20,000. The phrase for N1 also matches K1
(`payment capture skipped for order batch …`), so a pattern you wanted to keep disappears with
the noise and nothing tells you (checks C5, C6). With real logs, where the same words run through
many messages, this is the normal case, not an edge case.

### Step 3 — One regex per pattern

A template is precise enough to become a regex: `<*>` stands for one `[A-Za-z0-9]+` run and
everything else is literal. `sql/02-functions.sql` defines that conversion once:

```sql
SELECT patternToRegex('payment capture skipped for order["<*>"] token="<*>.<*>"');
-- ^payment\s+capture\s+skipped\s+for\s+order\["[A-Za-z0-9]+"\]\s+token="[A-Za-z0-9]+\.[A-Za-z0-9]+"$
```

Anchored at both ends, it matches N1 and not K1. Five of them stacked
(`sql/04-regex-exclusion.sql`) leave exactly **20,000** rows (C7). In HyperDX, switch the search
bar from Lucene to SQL and paste the condition, e.g.
`NOT match(Body, patternToRegex('cache miss for key user:<*>'))`.

### Step 4 — A stored noise list

Typing 10–30 conditions per incident is the actual pain. Keep the templates in a table instead
and test them all in one `multiMatchAny` (Hyperscan) call:

```sql
NOT multiMatchAny(Body, (SELECT groupArray(regex) FROM lab_patterns.noise))
```

Same 20,000 rows (C8). With a second list of patterns that are expected but worth keeping,
`sql/05-new-patterns.sql` counts per five minutes what is in **neither** list — here only the
200 signal rows (C10) — which is the query an alert on "a new pattern appeared" would run.

### Cost

`bin/cost.sh`, 2,000,000 lab rows (10 minutes of data), local all-in-one 2.39.1 on Docker
Desktop (12 CPUs, 16 GiB), 2026-11-02, `count()` over the whole range, best of 5 after one warm-up:

| Exclusions | Lucene phrases | `NOT match()` each | one `multiMatchAny` | Rows left (correct: 200,000) |
|---|---|---|---|---|
| none | 17 ms | | | |
| 5 | 40 ms | 36 ms | 47 ms | Lucene 100,000 · regex 200,000 |
| 30 (25 match nothing) | 71 ms | 62 ms | 54 ms | Lucene 100,000 · regex 200,000 |

At this size the cost does not decide anything; the row count does. Exclusions cannot skip
granules, so time grows with the rows in the chosen time range. Not measured on ClickHouse Cloud.

### What the checks cover

| Check | What |
|---|---|
| C0 | 200,000 rows seeded |
| C1–C2 | the miner finds 9 patterns, and the noise templates in `02-functions.sql` are exactly what it shows |
| C3 | `patternToRegex()` in SQL equals `template_to_regex()` in Python for every mined template — breaking the escaping makes it fail (tried 2026-11-02: 4 of 9 differ) |
| C4 | every row matches exactly one template regex |
| C5–C6 | stacked Lucene phrases leave 10,000 rows; the 10,000 lost rows are K1 |
| C7–C9 | stacked `NOT match()` and `multiMatchAny` both leave 20,000 rows: K1–K3 and S1 |
| C10 | rows in neither list = the 200 signal rows |

### Limits

- **No one-click exclude.** Templates are copied from Event Patterns by hand; this lab only
  makes the condition exact and reusable.
- **ASCII templates only were checked.** `reconstructTemplate` treats anything outside
  `[A-Za-z0-9]` as a separator, so a template over Korean or other non-ASCII text may not line up
  with drain's tokens. Not tested.
- **A template is as wide as the sample.** `10.0.<*>.<*>` came from three upstream hosts; a host
  in `10.1.x.x` would not match and would surface as new.
- `multiMatchAny` needs Hyperscan (`allow_hyperscan`, on by default); `match()` does not.
- Cleanup uses a lightweight `DELETE` that does not wait (`sql/99-cleanup.sql` says why).

### Files

| Path | What |
|---|---|
| `sql/01-seed.sql` | the nine patterns, `{rows:UInt64}` |
| `sql/02-functions.sql` | `patternToRegex()`, `lab_patterns.noise`, `lab_patterns.known` |
| `sql/03-lucene.sql` | Lucene exclusions as 2.39.1 SQL |
| `sql/04-regex-exclusion.sql` | per-pattern regex and the stored list |
| `sql/05-new-patterns.sql` | rows in neither list, per five minutes |
| `sql/99-cleanup.sql` | removes the lab rows and objects |
| `bin/mine_patterns.py` | Event Patterns outside the UI, with the regex for each template |
| `bin/check.sh` · `bin/cost.sh` · `bin/sql.sh` | checks, cost, SQL runner |

## 한국어

Kibana에서 옮겨 오는 팀은 보통 이렇게 일합니다. Discover → **Patterns**를 열고, 이미 아는
노이즈 패턴 10~30개에 **Filter out pattern**을 눌러 이상한 로그만 남깁니다. ClickStack에는
**Event Patterns**가 있지만 그런 버튼은 없습니다. 이 실습은 지금 어디까지 되는지, 그리고
Lucene 제외가 아직 필요한 로그까지 조용히 지우는 곳이 어디인지 보여 줍니다.

> 검증: ClickStack all-in-one `2.39.1`(HyperDX 2.39.1, ClickHouse 26.8.7.19), 로컬 `_base`
> 스택, 2026-11-02, `bin/check.sh` 11/11. 아래 HyperDX 동작은 `@hyperdx/app@2.39.1` 소스에서
> 읽었고, 이번 실행에서 UI를 직접 조작하지는 않았습니다. 모든 주장은 SQL로 확인합니다.
>
> UI 확인, 2026-11-04(같은 ClickStack all-in-one `2.39.1` 이미지, ClickHouse 26.8.7.19, `bin/check.sh`
> 11/11 뒤 headless Chromium): 실습 행의 Event Patterns에 템플릿 아홉 개가 `~` 건수와 함께 보였고 포함·제외
> 동작은 없었습니다. Lucene 문구 다섯 개를 겹치면 10,000건이 남고 `… order batch …` 패턴이 사라졌습니다.
> SQL 모드에서 `NOT match(Body, patternToRegex(…))` 다섯 개를 이으면 20,000건이 남고 유지할 패턴 셋과
> 찾을 신호가 남았습니다.

### HyperDX 2.39.1의 Event Patterns가 하는 일

`packages/app/src/hooks/usePatterns.tsx`, `components/PatternTable.tsx`에서 읽은 내용:

- 현재 검색에서 **10,000행**을 뽑아(`ORDER BY rand() LIMIT 10000`) **브라우저에서** drain3
  0.9.11(Pyodide)로 패턴을 찾습니다. 설정은 기본값에 ASCII 구두점 전부를 구분자로 더한 것입니다.
- 변하는 토큰은 `<*>`로 보입니다. 템플릿은 마지막 행의 원문 위에 다시 만들기 때문에 `["`,
  `=`, `.` 같은 구분자는 그대로 남습니다.
- 건수는 표본 건수를 전체로 늘린 값이라 `~72`처럼 보입니다. 1만 행에 한 번보다 드문 패턴은
  아예 안 보일 수 있습니다.
- 패턴 표에는 포함·제외 동작이 없습니다.

`bin/mine_patterns.py`가 같은 단계를 UI 밖에서 되풀이해서, 실습이 SQL로 확인할 수 있게 합니다.

### 데이터

`sql/01-seed.sql`이 ClickStack의 `default.otel_logs`에 200,000행을 넣습니다
(`ResourceAttributes['lab']` 표시, 최근 10분에 분포). 패턴 9개, 비율은 고정입니다.

| | 패턴 (Event Patterns에 보이는 모양) | 행 수 |
|---|---|---|
| 노이즈 N1 | `payment capture skipped for order["<*>"] token="<*>.<*>"` | 40,000 |
| 노이즈 N2 | `cache miss for key user:<*>` | 40,000 |
| 노이즈 N3 | `GET /api/cart/<*> completed in <*> ms` | 50,000 |
| 노이즈 N4 | `health check ok for upstream 10.0.<*>.<*> in <*> ms` | 30,000 |
| 노이즈 N5 | `retrying publish to topic orders-<*> attempt <*>` | 20,000 |
| 유지 K1 | `payment capture skipped for order batch <*> after <*> retries` — N1과 앞부분이 같음 | 10,000 |
| 유지 K2 | `payment capture failed for order["<*>"] code=<*>` | 8,000 |
| 유지 K3 | `cache miss for key config:<*>.<*> fallback to defaults` — N2와 앞부분이 같음 | 1,800 |
| 신호 S1 | `inventory reservation timed out for sku <*> after <*> ms` | 200 |

노이즈 다섯 개를 빼면 정확히 20,000행(K1~K3, S1)이 남아야 합니다.

### 실행

```bash
cd _base && docker compose up -d && cd ..
cd labs/log-pattern-exclusion
bin/check.sh              # 넣기, 패턴 찾기, 모든 시나리오 실행. UI에서 보도록 행은 남김
bin/check.sh --cleanup    # 같은 실행 뒤 실습 행, lab_patterns, patternToRegex를 지움
```

`curl`, `python3`, `uv`(`drain3==0.9.11`용)가 필요합니다. `CH_URL`이 없으면 `_base/.env.example`의
공개 로컬 기본값을 씁니다. HyperDX에서 같은 행을 보려면 http://localhost:8080 → Logs 소스 →
*Last 15 minutes* → **Event Patterns**.

### 1단계 — Lucene 제외와 그 SQL

`-`는 단어나 문구 바로 앞에 공백 없이 붙입니다. `-`만 쓰면 문법 오류입니다. 공백으로 이은
조건은 AND입니다. 기본 필드(Body)에 ClickStack 기본 색인
`INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha')`가 있을 때 2.39.1
파서가 만드는 SQL(`sql/03-lucene.sql`이 하나씩 실행):

| Lucene | 제외 대상 | SQL (2.39.1) | 남는 행 |
|---|---|---|---|
| `-payment` | 그 단어가 있는 Body | `NOT hasAllTokens(lower(Body), lower('payment'))` | 142,000 |
| `-"payment capture skipped for order"` | 그 문구가 들어 있는 Body | `NOT (hasAllTokens(lower(Body), lower('…')) AND lower(Body) LIKE lower('%…%'))` | 150,000 |
| `-payment*` | 그 단어로 시작하는 Body | `lower(Body) NOT LIKE lower('payment%')` | 142,000 |
| `-*skipped*` | 그 단어가 들어 있는 Body | `lower(Body) NOT LIKE lower('%skipped%')` | 150,000 |

따옴표 없는 필드 조건 `-ServiceName:pay`는 부분 문자열 비교(`ServiceName NOT ILIKE '%pay%'`)이고,
따옴표가 있으면 `-ServiceName:"payment"`처럼 정확한 값입니다. `*`는 따옴표 없는 한 단어에만
쓸 수 있습니다. Lucene 검색창에는 정규식이 없습니다.

### 2단계 — 문구를 겹쳐도 부족한 이유

Lucene으로 할 수 있는 최선은 노이즈 패턴마다 고정 문구 하나입니다.

```
-"payment capture skipped for order" -"cache miss for key user" -"completed in"
-"health check ok for upstream" -"retrying publish to topic"
```

결과는 20,000행이 아니라 **10,000행**입니다. N1용 문구가 K1(`payment capture skipped for order
batch …`)에도 맞아서, 남겨야 할 패턴이 노이즈와 함께 사라지고 아무 표시도 없습니다(C5, C6).
같은 단어가 여러 메시지에 걸쳐 나오는 실제 로그에서는 이것이 드문 경우가 아니라 보통입니다.

### 3단계 — 패턴마다 정규식 하나

템플릿은 정규식으로 바꿀 만큼 정확합니다. `<*>`는 `[A-Za-z0-9]+` 한 덩어리이고 나머지는 그대로입니다.
`sql/02-functions.sql`이 이 변환을 함수로 정의합니다.

```sql
SELECT patternToRegex('payment capture skipped for order["<*>"] token="<*>.<*>"');
-- ^payment\s+capture\s+skipped\s+for\s+order\["[A-Za-z0-9]+"\]\s+token="[A-Za-z0-9]+\.[A-Za-z0-9]+"$
```

앞뒤를 고정해서 N1에는 맞고 K1에는 맞지 않습니다. 다섯 개를 겹치면(`sql/04-regex-exclusion.sql`)
정확히 **20,000행**이 남습니다(C7). HyperDX에서는 검색창을 Lucene에서 SQL로 바꾸고 조건을
붙여 넣습니다. 예: `NOT match(Body, patternToRegex('cache miss for key user:<*>'))`.

### 4단계 — 노이즈 목록을 테이블로

장애 때마다 조건 10~30개를 입력하는 것이 실제 고통입니다. 템플릿을 테이블에 두고
`multiMatchAny`(Hyperscan) 한 번으로 모두 검사합니다.

```sql
NOT multiMatchAny(Body, (SELECT groupArray(regex) FROM lab_patterns.noise))
```

결과는 같은 20,000행입니다(C8). 예상된, 남겨 둘 패턴 목록을 하나 더 두면
`sql/05-new-patterns.sql`이 **두 목록 어디에도 없는** 행을 5분 단위로 셉니다. 여기서는 신호
200행뿐이고(C10), "새 패턴이 나타났다" 알림이 돌릴 쿼리가 이것입니다.

### 비용

`bin/cost.sh`, 실습 행 2,000,000개(10분치), 로컬 all-in-one 2.39.1, Docker Desktop(12 CPU,
16 GiB), 2026-11-02, 전체 범위 `count()`, 예열 1회 뒤 5회 중 최소값:

| 제외 개수 | Lucene 문구 | 패턴마다 `NOT match()` | `multiMatchAny` 한 번 | 남는 행 (정답 200,000) |
|---|---|---|---|---|
| 없음 | 17 ms | | | |
| 5 | 40 ms | 36 ms | 47 ms | Lucene 100,000 · 정규식 200,000 |
| 30 (25개는 맞는 행 없음) | 71 ms | 62 ms | 54 ms | Lucene 100,000 · 정규식 200,000 |

이 규모에서는 비용이 아니라 남는 행 수가 결정합니다. 제외 조건은 granule을 건너뛰지 못하므로
시간은 고른 시간 범위의 행 수에 비례해 늘어납니다. ClickHouse Cloud에서는 재지 않았습니다.

### 검사 항목

| 검사 | 내용 |
|---|---|
| C0 | 200,000행 입력 |
| C1–C2 | 패턴 9개를 찾고, `02-functions.sql`의 노이즈 템플릿이 화면에 보이는 것과 정확히 같음 |
| C3 | SQL `patternToRegex()`와 Python `template_to_regex()`가 모든 템플릿에서 같음 — 이스케이프를 망가뜨리면 실패함(2026-11-02 시도: 9개 중 4개 다름) |
| C4 | 모든 행이 템플릿 정규식 정확히 하나에 맞음 |
| C5–C6 | Lucene 문구를 겹치면 10,000행이 남고, 잃은 10,000행은 K1 |
| C7–C9 | `NOT match()`와 `multiMatchAny` 모두 20,000행(K1~K3, S1)을 남김 |
| C10 | 두 목록 어디에도 없는 행 = 신호 200행 |

### 한계

- **클릭 한 번 제외는 없습니다.** 템플릿은 Event Patterns에서 손으로 옮깁니다. 이 실습은
  조건을 정확하고 다시 쓸 수 있게 만들 뿐입니다.
- **ASCII 템플릿만 확인했습니다.** `reconstructTemplate`은 `[A-Za-z0-9]` 밖의 문자를 구분자로
  보므로, 한국어 같은 비ASCII 문장의 템플릿은 drain 토큰과 어긋날 수 있습니다. 시험하지 않았습니다.
- **템플릿은 표본만큼만 넓습니다.** `10.0.<*>.<*>`는 upstream 호스트 세 개에서 나왔습니다.
  `10.1.x.x` 호스트는 맞지 않아 새 패턴으로 보입니다.
- `multiMatchAny`는 Hyperscan(`allow_hyperscan`, 기본 켜짐)이 필요합니다. `match()`는 필요 없습니다.
- 정리는 기다리지 않는 lightweight `DELETE`를 씁니다(이유는 `sql/99-cleanup.sql`).

### 파일

| 경로 | 내용 |
|---|---|
| `sql/01-seed.sql` | 패턴 9개, `{rows:UInt64}` |
| `sql/02-functions.sql` | `patternToRegex()`, `lab_patterns.noise`, `lab_patterns.known` |
| `sql/03-lucene.sql` | Lucene 제외를 2.39.1 SQL로 |
| `sql/04-regex-exclusion.sql` | 패턴별 정규식과 저장된 목록 |
| `sql/05-new-patterns.sql` | 두 목록 어디에도 없는 행, 5분 단위 |
| `sql/99-cleanup.sql` | 실습 행과 객체 삭제 |
| `bin/mine_patterns.py` | UI 밖의 Event Patterns, 템플릿마다 정규식 |
| `bin/check.sh` · `bin/cost.sh` · `bin/sql.sh` | 검사, 비용, SQL 실행기 |
