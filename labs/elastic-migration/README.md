# Migrating from Elastic

[English](#english) | [한국어](#한국어)

## English

An Elastic migration splits into three parts, and which one dominates varies
enormously between deployments. A team that ran its own pipeline into
Elasticsearch has no ingest work at all; a team on Elastic Agent has a great
deal. So these are three independent parts, each usable on its own, and the
first useful thing this lab does is let you establish which ones you can skip.

| Part | Elastic input | ClickStack output | Status |
|---|---|---|---|
| ingest | `GET _ingest/pipeline`, Beats or Logstash config, index templates | a collector config fragment | planned, `ingest/` |
| data | `GET _mapping` plus the index contents | ClickHouse DDL and a load | written, [`data/`](data/) |
| identity | ids that differ between the two systems | a mapping table, a dictionary and a quarantine | written, [`data/idmap/`](data/idmap/) |
| dashboards, alerts | Kibana saved objects, alerting rules | HyperDX dashboards and alerts | separate, under `skills/` |

Two of the three already have a destination in this repository:
[otel-profiles](../../otel-profiles/) for collector configuration and
[clickstack-config](../../clickstack-config/) for sources, dashboards and
alerts. So the new surface is smaller than "three parts" suggests — the data
path, plus converters that feed assets that already exist.

### Driving this with an agent

Most people running this will be driving it with a coding agent, the same way
it was built. [`AGENTS.md`](AGENTS.md) in this directory is written for that
reader rather than for a contributor: where to look for each question, the
order the steps go in, which two steps are human decisions, the invariants
whose failure mode is a migration that *looks* finished, and the questions an
agent should put to a person instead of answering by default.

Read it before the sections below if you are running a real migration. Read
the repository root's [`AGENTS.md`](../../AGENTS.md) instead if you are
changing this lab.

### Start with the official documentation

ClickHouse documents this migration already, and this lab does not restate it:

- [Migrating to ClickStack from Elastic](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/intro)
- [Equivalent concepts](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/concepts)
- [Mapping types](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/types)
- [Migrating data](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)
- [Migrating agents](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-agents)

Read those first. What is left here is what they do not cover, and the largest
gap is one the data page states about itself: export over JSON and HTTP is only
viable below roughly ten million rows, because that is what the scroll API and
`elasticdump` comfortably sustain. Real observability datasets start above that
ceiling, so the documented path does not apply to the cases that most need help.

### All three parts have the same shape

**Parse an Elastic artifact, emit a ClickStack artifact, then compare the two
sides.** The third step is the one that matters, and it is the same idea every
time — a pair of queries, one per system, that must agree.

| Part | Conversion | Check |
|---|---|---|
| ingest | ingest processors to OTTL or `filelog` operators | push one identical input line through both, compare the resulting fields |
| data | `_mapping` to DDL | document counts per time bucket, and field-level sampling |
| dashboards | panel query to SQL | the same time window must produce the same number |

A conversion without its pair is a claim, not a result. This is also what lets a
tool grade its own output rather than asking someone to eyeball a dashboard.

### Conversion is not one to one, and saying so is the feature

No part of this maps cleanly, and the failure mode to design against is emitting
something plausible for the cases that did not convert. Every converter here
classifies each item it handles:

- **converted** — a direct equivalent exists
- **needs review** — an equivalent exists but behaviour differs at the edges,
  such as `grok`, which has no direct OTTL counterpart and has to be expressed
  as a regex or an operator
- **unsupported** — no mechanical conversion, such as a `script` processor

A silent guess in the second or third category is worse than a refusal, because
it is discovered in production rather than during the migration.

### Questions that change the size of the work

Worth settling before starting. The answers can shrink this considerably:

- total volume: index count, document count, size on disk
- whether everything moves, or only the retention window, leaving the old
  cluster read-only for the rest
- whether both systems run in parallel for a period, which adds dual-write

### Order

`data/` first — it is the only part with no official coverage. The other two
attach to assets that are better left to settle, and are simply absent for some
migrations.

Tracked as issues so a part a given migration does not need can be skipped
rather than worked around.

---

## 한국어

Elastic 마이그레이션은 세 부분으로 나뉘고, 어느 쪽이 대부분을 차지하는지는 환경마다
크게 다릅니다. 자체 파이프라인으로 Elasticsearch에 넣던 팀은 입수 작업이 아예 없고,
Elastic Agent를 쓰던 팀은 그쪽이 대부분입니다. 그래서 세 부분은 서로 독립적이며 각각
따로 쓸 수 있고, 이 랩이 가장 먼저 해주는 일은 **어느 부분을 건너뛸 수 있는지 판단**
하게 해주는 것입니다.

| 부분 | Elastic 입력 | ClickStack 출력 | 상태 |
|---|---|---|---|
| 입수 | `GET _ingest/pipeline`, Beats·Logstash 설정, index template | 컬렉터 설정 조각 | 예정, `ingest/` |
| 데이터 | `GET _mapping`과 인덱스 내용 | ClickHouse DDL과 적재 | 작성됨, [`data/`](data/) |
| 동일성 | 두 시스템에서 값이 다른 id | 매핑 테이블, 딕셔너리, 격리 | 작성됨, [`data/idmap/`](data/idmap/) |
| 대시보드·알림 | Kibana saved objects, alerting rules | HyperDX 대시보드·알림 | 별도, `skills/` 아래 |

세 부분 중 둘은 이미 이 저장소에 도착지가 있습니다 — 컬렉터 설정은
[otel-profiles](../../otel-profiles/), source·대시보드·알림은
[clickstack-config](../../clickstack-config/). 그래서 "세 부분"이라는 말보다 실제로
새로 만들 면적은 작습니다. 데이터 경로, 그리고 이미 있는 자산에 입력을 공급하는
변환기들입니다.

### 에이전트로 실행하는 경우

이것을 실행하는 대부분은 이 랩이 만들어진 방식과 마찬가지로 코딩 에이전트로
진행할 것입니다. 이 디렉터리의 [`AGENTS.md`](AGENTS.md)는 기여자가 아니라 **그
독자**를 위해 쓰였습니다: 질문별로 어디를 볼지, 단계의 순서, 그중 사람이 판단해야
하는 두 단계, 어겼을 때 *완료된 것처럼 보이는* 마이그레이션이 나오는 불변식, 그리고
에이전트가 기본값으로 답하지 말고 사람에게 물어야 하는 질문들.

실제 마이그레이션을 실행한다면 아래 절들보다 먼저 읽으세요. 이 랩 자체를
변경한다면 저장소 루트의 [`AGENTS.md`](../../AGENTS.md)를 읽으세요.

### 공식 문서부터

ClickHouse가 이 마이그레이션을 이미 문서화해 두었고, 이 랩은 그것을 다시 쓰지 않습니다.

- [Migrating to ClickStack from Elastic](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/intro)
- [Equivalent concepts](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/concepts)
- [Mapping types](https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/types)
- [Migrating data](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)
- [Migrating agents](https://clickhouse.com/docs/clickstack/migration/elastic/migrating-agents)

먼저 읽으세요. 여기 남는 것은 문서가 다루지 않는 부분이고, 가장 큰 공백은 데이터
문서가 스스로 밝힌 것입니다 — JSON·HTTP 기반 내보내기는 **약 1천만 행 미만에서만
실용적**입니다. scroll API와 `elasticdump`가 무리 없이 감당하는 한계가 그 정도이기
때문입니다. 실제 관측성 데이터는 그 천장 위에서 시작하므로, 도움이 가장 필요한 경우에
문서의 경로가 적용되지 않습니다.

### 세 부분이 같은 모양입니다

**Elastic 산출물을 파싱하고, ClickStack 산출물을 생성하고, 양쪽을 대조합니다.**
중요한 것은 세 번째이고, 방식은 매번 같습니다 — 시스템별로 하나씩 만든 쿼리 쌍이
같은 답을 내야 합니다.

| 부분 | 변환 | 검증 |
|---|---|---|
| 입수 | ingest processor → OTTL 또는 `filelog` operator | 동일한 입력 한 줄을 양쪽에 통과시켜 결과 필드 비교 |
| 데이터 | `_mapping` → DDL | 시간 버킷별 문서 수, 필드 단위 표본 비교 |
| 대시보드 | 패널 쿼리 → SQL | 같은 시간창에서 같은 숫자가 나와야 함 |

쌍이 없는 변환은 결과가 아니라 주장입니다. 이것이 도구가 사람에게 대시보드를 눈으로
확인해 달라고 하는 대신 **자기 출력을 스스로 채점**할 수 있게 하는 부분입니다.

### 변환은 1:1이 아니고, 그걸 밝히는 것이 기능입니다

어느 부분도 깔끔하게 대응되지 않으며, 설계로 막아야 할 실패는 **변환되지 않은 것에
대해 그럴듯한 결과를 내놓는 것**입니다. 여기의 모든 변환기는 처리한 항목을 분류합니다.

- **변환됨** — 직접 대응이 존재
- **수동 검토** — 대응은 있지만 경계에서 동작이 다름. 예: `grok`은 OTTL에 직접 대응이
  없어 정규식이나 operator로 표현해야 합니다
- **불가** — 기계적 변환이 불가능. 예: `script` processor

두 번째·세 번째 범주에서 조용히 추측하는 것은 거부하는 것보다 나쁩니다. 마이그레이션
중이 아니라 운영 중에 발견되기 때문입니다.

### 작업 규모를 바꾸는 질문들

시작 전에 확정하는 게 좋습니다. 답에 따라 작업이 크게 줄어들 수 있습니다.

- 총량: 인덱스 수, 문서 수, 디스크 크기
- 전체를 옮기는지, 보존 기간만 옮기고 나머지는 기존 클러스터를 읽기 전용으로 남기는지
- 두 시스템을 일정 기간 병행 운영하는지 (그렇다면 이중 기록이 추가됩니다)

### 순서

`data/`부터입니다 — 공식 문서 공백이 여기뿐입니다. 나머지 둘은 아직 안정화 중인 자산에
붙고, 일부 마이그레이션에서는 아예 필요하지 않습니다.

이슈로 나눠 추적하므로, 해당 마이그레이션에 필요 없는 부분은 우회하지 않고 그냥
건너뛸 수 있습니다.
