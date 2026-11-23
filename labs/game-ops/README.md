# labs/game-ops

[English](#english) | [한국어](#한국어)

## English

A game backend and a game server fleet that live entirely inside one ClickHouse Cloud service, for showing **Managed
ClickStack** end to end on game telemetry: live tail, search and Event Patterns, a trace from a failed purchase to the
slow call behind it, dashboards with release markers, alerts, and AI Notebooks. Everything is generated in SQL by
refreshable materialized views; nothing is sent in from outside.

The engine is [`labs/apm-workflows`](../apm-workflows/) with the shop renamed into a game backend, plus a new generator for
the game servers. How spans, logs and metrics are modelled, the switches, the backfill and the live views are described
there and work the same way here.

> **Status (2026-11-20).** Installed on a Managed ClickStack service, backfilled and live; the patch-day scenario was played
> once and checked by SQL (below). The S1 replay check of apm-workflows (`bin/s1_check.py`) was copied with the renames
> and **not re-run** here.

### What is in it

```
ClickHouse Cloud service with Managed ClickStack
├── database game_ops
│   ├── game backend   game-gateway → catalog · cart · player · purchase → pricing · inventory · billing (→ pg.example.com)
│   │                  · order → Kafka → notification · reward            traces + logs + metrics (from apm-workflows)
│   ├── game servers   gs-kr-01 .. gs-kr-12 on ch-1 .. ch-4               event logs + 2 gauges (sql/14_gen_game.sql)
│   ├── infrastructure the game servers' hosts, the Kubernetes cluster      hostmetrics + kubeletstats / k8s_cluster metrics (sql/15_gen_infra.sql)
│   ├── switches       fault_events · deploy_events · lab_settings        rows you INSERT (bin/fault.py, bin/deploy.py, bin/scenario.py)
│   ├── live           rmv_traces → rmv_logs · rmv_metrics_* · rmv_game_logs · rmv_game_gauge   every minute
│   └── telemetry      otel_traces · otel_logs · otel_metrics_*           OTel exporter schema, TTL 30 days
└── ClickStack         sources Game Traces ⇄ Game Logs ⇄ Game Metrics, dashboards "Game ops" (28 tiles) and
                       "Game ops · backend" (21 tiles), seven saved searches "Demo N · ..." (clickstack/searches.json)
```

| Service | What it does |
|---|---|
| `game-gateway` | entry point for every client request (item pages, search, cart, purchase, purchase history, profile) |
| `catalog` · `cart` · `pricing` · `inventory` | item catalog (MySQL, Redis cache), cart (Redis), prices and promotions, limited stock (MySQL) |
| `purchase` | orchestrates a purchase: cart → quote → stock → billing → order |
| `billing` | charges at an external payment gateway (`pg.example.com`) |
| `order` | writes the purchase (MySQL), publishes `order.created` to Kafka |
| `notification` · `reward` | consume `order.created`: a receipt, then the items granted to the player |
| `player` | accounts |
| `game-server` | twelve dedicated servers: logins, logouts, match start and end, item drops, anti-cheat flags, chat reports, tick overruns, a heartbeat per minute; gauges `game.server.ccu` and `game.server.tick_duration` |

Game server log lines carry `game.event`, `player.id`, `match.id`, `game.mode`, `item.sku`, `item.rarity` and more as log
attributes, and `host.name` / `game.server.id`, `game.channel`, `service.version` as resource attributes. About 200 lines a
minute come from the fleet at the daily peak (12:00 UTC = 21:00 KST), about 30 from the backend.

### The patch-day scenario

`python3 bin/scenario.py` writes the whole incident up front, timestamped from one minute from now (or `--at`):

| Minute | Switch | What it looks like |
|---|---|---|
| +0 | `purchase` next minor version with a regression (4.2.1 → 4.3.0), `game-server` next minor (1.9.0 → 1.10.0) | release markers; purchase calls pricing once per cart item, ~3% errors; the fleet restarts one server every 15 s |
| +3 | `downstream-latency` on billing | purchase time moves into the call to `pg.example.com` |
| +6 | `server-crash` on gs-kr-07 | one ERROR (process exited, 0xC0000005), one WARN (players dropped), then no logs and no gauges |
| +10 | gs-kr-07 restarts | startup lines, users come back at 30% |
| +12 | billing recovers | |
| +14 | `purchase` hotfix (4.3.1) | the hotfix: errors back to the background level |

Versions move forward from what each service runs at the start (latest deploy row, else the base version), so every run
shows new release markers; the examples are the second run on a service that had the first one.

Single switches: `python3 bin/fault.py on|off <fault> [--target <pod or server>]`, `python3 bin/deploy.py <service> <version>
[--regression]`. The faults of apm-workflows are all here (`slow-query`, `n-plus-one`, `pool-exhaustion`,
`downstream-latency`, `kafka-consumer-lag`, `exception-storm`, …) plus `server-crash`.

### Dashboards, saved searches, store revenue, infrastructure

- **"Game ops"** is the game-first screen, in five sections with a header tile each:
  - *Live game*: players online, servers reporting, purchases, store revenue (KRW), purchase failures, game server errors.
  - *Patch day*: purchase failures and purchase / payment-gateway p95 with release and fault markers, game servers by
    version (the rolling update), and a server status table (version, players, host CPU, seconds since the last log line,
    `NO LOGS` for a server whose process is down).
  - *Players*: logins by platform, login latency, matches by mode.
  - *Infrastructure*: game host CPU and network, pod CPU by deployment, nodes.
  - *What changed*: the switch and deploy table, warnings and errors by message, purchase steps, error and warning logs.
- **Markers.** A marker is a series of its own (`deploy purchase 4.3.0`, `downstream-latency on`, …). It carries the
  chart's maximum in the interval of the event and 0 everywhere else, so it shows as a spike. SQL tiles have no annotation
  layer.
- **"Game ops · backend"** holds the 21 tiles of apm-workflows: S1, Kafka, Hikari, the MySQL slow log, alerts' sources.
- **Saved searches.** One per demo step, named `Demo N · …`, from `clickstack/searches.json`. `setup.py --apply` creates
  them with the dashboards and matches them by name.
- **Store revenue.** The root span of `POST /purchase` carries `game.item` (the first package in the cart), `game.cart.items`
  (2–6) and `game.order.amount_krw`. Packages run from gem-100 at 1,200 KRW to legend-skin at 29,000 KRW. A failed purchase
  keeps its amount, which is what the "failed (lost)" series adds up. Only spans written after the change have these
  attributes; the backfill was not rewritten.
- **Infrastructure** (`sql/15_gen_infra.sql`):
  - *Kubernetes.* Metrics named as the collector's kubeletstats and k8s_cluster receivers name them: pod CPU and memory
    (with limit utilisation), phase, restarts, filesystem and uptime; node CPU, memory, readiness, filesystem and uptime;
    namespace phase, two samples a minute (the receivers' 30-second interval). These
    are what ClickStack's Kubernetes page and the Infrastructure tab of a log or span read. The pods are those of the
    spans (same names, renamed by a deploy). A pod's CPU grows with the spans it served, so the purchase regression shows
    on pricing.
  - *Game server hosts.* Hostmetrics for each game server, also every 30 seconds: CPU by state, load, memory, filesystem and network. A crashed
    game process leaves its host up but idle: CPU near 2%, network down to a trickle. The process's own logs and gauges
    stop.
  - *How it is written.* `gen_metrics_gauge` and `gen_metrics_sum` end with these rows, so the live views and the backfill
    write them along with the rest. A lab installed before them gets their history from `bin/backfill_infra.sh`.

### Running it

You need a ClickHouse Cloud service with Managed ClickStack, a credentials file with `CH_HOST`, `CH_USER`, `CH_PASSWORD`,
and for the ClickStack step a Cloud API key (in the same file as `CHC_ORG_ID`, `CHC_KEY_ID`, `CHC_KEY_SECRET`, or in a second
file named by `CHC_ENV_FILE` as `CHC_KEY`, `CHC_SECRET`; the organisation is then looked up). The lab writes only into
database `game_ops`.

```bash
cd labs/game-ops
cp .env.example .env            # set CH_ENV_FILE (and CHC_ENV_FILE) to the credentials files
bin/install.sh                  # database, tables, topology, switches, generator views
bin/backfill.sh                 # 8 days of history; prints the plan first
bin/install.sh rmvs             # live generation, every minute
python3 clickstack/setup.py --test && python3 clickstack/setup.py --apply   # sources, both dashboards, saved searches
bin/backfill_infra.sh           # only on a lab installed before sql/15_gen_infra.sql: infra history after bin/install.sh
python3 bin/scenario.py         # the patch-day incident, starting in one minute
bin/stop.sh                     # stop live generation on every replica (data stays); --resume starts it again
bin/uninstall.sh                # DROP DATABASE game_ops (asks first)
```

`python3 clickstack/setup.py --alerts` creates the alert tiles (A1–A9, A9 = a game server that stopped logging) and needs a
Slack webhook URL in `APM_SLACK_WEBHOOK_URL`; it was not run here.

### Verification

**Verified on ClickHouse 26.8.1.780** (ClickHouse Cloud, ap-northeast-2) **with Managed ClickStack**, 2026-11-20. The
ClickStack build was not read: the UI was not opened in this run.

- `bin/install.sh`, `bin/backfill.sh` (8 days, 190 s: per day about 606,000 spans, 259,000 log lines of which about
  230,000 from the game servers, 278,000 metric points), `bin/install.sh rmvs`. `sql/verify_continuity.sql`: no minute
  without requests, 60 s between the last backfill minute and the first live one.
- `clickstack/setup.py --test`: the SQL of all 27 dashboard tiles and 9 alert tiles ran on the service; `--apply` created the
  three sources and the "Game ops" dashboard, and the dashboard was found again through the API.
- Scenario `scenario-20261120T125340Z`, checked with `sql/verify_scenario.sql`:

  | Phase | Purchases | Errors | Pricing calls per purchase | Billing external call p95 |
  |---|---|---|---|---|
  | 20 min before | 167 | 1.2% | 1.00 | 18 ms |
  | +0 to +3, regression | 28 | 7.1% | 4.29 | 21 ms |
  | +3 to +12, regression and slow gateway | 80 | 3.8% | 4.11 | 1,070 ms |
  | +12 to +14, regression | 10 | 0% | 4.40 | 14 ms |

  gs-kr-07: the crash line at +6:00 and the dropped-players line one and a half seconds later, no other line and no gauge
  point until the restart at +10:00 (gauges back from that minute), startup lines with version 1.9.0. All twelve servers
  restarted between +0:15 and +3:00 and report 1.9.0 afterwards. The three deploy rows are the release markers.
- After the hotfix the run is **not a clean negative control**: an automatic `pricing-timeouts` incident (rmv_incidents,
  13:05–13:11 UTC) overlapped it. `bin/scenario.py` now refuses an overlapping incident, and `incidents` is 0 on this service.
- Unit tests: 34 pass (`python3 -m unittest discover -s tests`).
- **Not run:** `setup.py --alerts`, `bin/s1_check.py`, `bin/uninstall.sh`. The change that keeps a server's events from before
  the crash in its crash minute was made after the run and checked in `clickhouse local` 26.9.6.6 only.

### What it costs on the service

| | Measured 2026-11-20 |
|---|---|
| Backfill | 8 days in 190 s |
| Storage | about 347 MiB for the lab: `otel_traces` 262 MiB (16.3×), `otel_logs` 59 MiB (9.9×), metrics 26 MiB |
| Live generation | eight refreshes a minute: about 2.0 s of CPU and 4.6 s of wall time per minute in total (the two game server views 0.4 s of CPU), over ten minutes |

### Layout

```
labs/game-ops/
├── bin/          install.sh · backfill.sh · backfill_infra.sh · scenario.py · fault.py · deploy.py · stop.sh · uninstall.sh · s1_check.py
├── lib/ch.py     HTTPS client; refuses writes outside game_ops
├── sql/          00–05 tables, switches, topology · 10–13 backend generators and watermark · 14 game servers ·
│                 15 infrastructure · 30 live views · backfill_*.sql · verify_*.sql · s1_*.sql
├── clickstack/   setup.py · tiles/*.sql ("Game ops") · backend/*.sql ("Game ops · backend") · searches.json · alerts/*.sql
└── tests/        unit tests (python3 -m unittest discover -s tests)
```

---

## 한국어

ClickHouse Cloud 서비스 하나 안에서 도는 게임 백엔드와 게임 서버 묶음입니다. 게임 텔레메트리로 **Managed ClickStack**을
처음부터 끝까지 보여주기 위한 실습입니다. live tail, 검색과 Event Patterns, 실패한 구매 trace에서 그 뒤의 느린 호출까지,
배포 마커가 있는 대시보드, 알림, AI Notebooks를 다룹니다. 모든 데이터는 리프레시 머티리얼라이즈드 뷰가 SQL로 만들고,
밖에서 보내는 것은 없습니다.

엔진은 [`labs/apm-workflows`](../apm-workflows/)입니다. 쇼핑몰을 게임 백엔드로 이름만 바꾸고, 게임 서버 생성기를 새로
더했습니다. span·로그·메트릭을 본뜨는 방식, 스위치, 백필, 실시간 뷰는 그쪽 설명과 같습니다.

> **상태 (2026-11-20).** Managed ClickStack 서비스에 설치하고 백필한 뒤 실시간으로 돌고 있습니다. 패치 날 시나리오를 한 번
> 돌리고 SQL로 확인했습니다(아래). apm-workflows의 S1 재생 검사(`bin/s1_check.py`)는 이름만 바꿔 가져왔고 여기서
> **다시 돌리지 않았습니다**.

### 들어 있는 것

| 서비스 | 하는 일 |
|---|---|
| `game-gateway` | 클라이언트 요청의 입구(아이템 화면, 검색, 장바구니, 구매, 구매 내역, 프로필) |
| `catalog` · `cart` · `pricing` · `inventory` | 아이템 목록(MySQL, Redis 캐시), 장바구니(Redis), 가격·프로모션, 한정 재고(MySQL) |
| `purchase` | 구매 흐름: 장바구니 → 견적 → 재고 → 결제 → 주문 |
| `billing` | 외부 결제 대행(`pg.example.com`) 승인 |
| `order` | 구매 기록(MySQL), Kafka에 `order.created` 발행 |
| `notification` · `reward` | `order.created`를 받아 영수증 발송, 플레이어에게 아이템 지급 |
| `player` | 계정 |
| `game-server` | 전용 게임 서버 12대: 로그인·로그아웃, 매치 시작·종료, 아이템 드롭, 안티치트, 채팅 신고, 틱 지연, 분당 heartbeat. 게이지 `game.server.ccu`, `game.server.tick_duration` |

게임 서버 로그에는 `game.event`, `player.id`, `match.id`, `game.mode`, `item.sku`, `item.rarity` 등이 log attribute로,
`host.name` / `game.server.id`, `game.channel`, `service.version`이 resource attribute로 붙습니다. 하루 중 가장 붐빌 때
(12:00 UTC = 21:00 KST) 게임 서버에서 분당 약 200줄, 백엔드에서 약 30줄이 나옵니다.

### 패치 날 시나리오

`python3 bin/scenario.py`는 사고 전체를 미리 기록합니다. 시작은 지금부터 1분 뒤(또는 `--at`)입니다.

| 분 | 스위치 | 보이는 것 |
|---|---|---|
| +0 | `purchase` 다음 minor 버전(회귀 있음, 4.2.1 → 4.3.0), `game-server` 다음 minor(1.9.0 → 1.10.0) | 배포 마커. purchase가 장바구니 아이템마다 pricing을 부르고 오류 약 3%. 게임 서버는 15초마다 한 대씩 재시작 |
| +3 | billing에 `downstream-latency` | 구매 시간이 `pg.example.com` 호출로 옮겨감 |
| +6 | gs-kr-07에 `server-crash` | ERROR 하나(프로세스 종료, 0xC0000005), WARN 하나(플레이어 끊김), 그 뒤 로그도 게이지도 없음 |
| +10 | gs-kr-07 재시작 | 시작 로그, 사용자 30%로 복귀 |
| +12 | billing 회복 | |
| +14 | `purchase` 핫픽스(4.3.1) | 핫픽스: 오류가 평소 수준으로 |

버전은 시작 시점에 각 서비스가 쓰는 버전(마지막 배포 행, 없으면 기본 버전)에서 올라가므로 실행할 때마다 새 배포 마커가
생깁니다. 예시는 첫 실행을 한 서비스에서 두 번째로 실행한 경우입니다.

스위치 하나씩: `python3 bin/fault.py on|off <fault> [--target <pod 또는 서버>]`, `python3 bin/deploy.py <서비스> <버전>
[--regression]`. apm-workflows의 결함이 모두 있고, `server-crash`가 더해졌습니다.

### 대시보드, 저장된 검색, 상점 매출, 인프라

- **"Game ops"**는 게임 중심 화면입니다. 다섯 구역으로 나뉘고, 구역마다 머리 타일이 있습니다.
  - *Live game*: 동시 접속, 보고 중인 서버 수, 구매 건수, 상점 매출(KRW), 구매 실패율, 게임 서버 에러.
  - *Patch day*: 구매 실패율, 구매와 결제 대행 호출의 p95(배포·장애 마커 포함), 버전별 게임 서버 수(롤링 업데이트),
    서버 상태 표. 상태 표에는 버전, 접속자, 호스트 CPU, 마지막 로그 뒤 지난 초가 나오고, 프로세스가 죽은 서버는
    `NO LOGS`로 표시됩니다.
  - *Players*: 플랫폼별 로그인, 로그인 지연, 모드별 매치.
  - *Infrastructure*: 게임 호스트 CPU와 네트워크, 디플로이먼트별 파드 CPU, 노드.
  - *What changed*: 스위치·배포 표, 메시지별 경고·에러, 구매 단계별 시간, 서비스별 에러·경고 로그.
- **마커.** 마커는 따로 하나의 시리즈입니다(`deploy purchase 4.3.0`, `downstream-latency on` 등). 사건이 있던 구간에서는
  차트 최댓값, 나머지 구간에서는 0이어서 뾰족하게 솟아 보입니다. SQL 타일에는 주석 레이어가 없습니다.
- **"Game ops · backend"**에는 apm-workflows의 타일 21개(S1, Kafka, Hikari, MySQL slow log, 알림의 원본)가 있습니다.
- **저장된 검색.** 데모 단계마다 하나씩, `Demo N · …`이라는 이름으로 `clickstack/searches.json`에 있습니다.
  `setup.py --apply`가 대시보드와 함께 만들고, 이름으로 맞춥니다.
- **상점 매출.** `POST /purchase`의 루트 span에 `game.item`(장바구니의 첫 패키지), `game.cart.items`(2–6개),
  `game.order.amount_krw`가 붙습니다. 패키지는 gem-100 1,200원부터 legend-skin 29,000원까지입니다. 실패한 구매도 금액을
  가지고 있어서, "failed (lost)" 시리즈가 그 합입니다. 이 속성은 변경 뒤에 쓴 span에만 있고, 백필은 다시 쓰지 않았습니다.
- **인프라** (`sql/15_gen_infra.sql`):
  - *Kubernetes.* 컬렉터의 kubeletstats와 k8s_cluster 리시버가 쓰는 이름 그대로입니다. 파드는 CPU·메모리(limit 대비
    사용률 포함), phase, 재시작, 파일시스템, uptime이 있고, 노드는 CPU·메모리·Ready·파일시스템·uptime, 네임스페이스는
    phase가 있습니다. 리시버의 30초 간격처럼 1분에 두 번 씁니다.
    ClickStack의 Kubernetes 화면과 로그·span의 Infrastructure 탭이 이 값을 읽습니다. 파드는 span의 파드와 같고(이름도
    같고, 배포하면 이름이 바뀝니다), 처리한 span이 많을수록 CPU가 올라서 purchase 회귀가 pricing CPU에 보입니다.
  - *게임 서버 호스트.* 게임 서버마다 hostmetrics가 있습니다(상태별 CPU, load, 메모리, 파일시스템, 네트워크). 게임
    프로세스가 죽어도 호스트는 살아 있습니다. CPU는 2% 안팎으로 떨어지고 네트워크는 거의 끊기며, 그 프로세스의 로그와
    게이지만 멈춥니다.
  - *쓰는 방식.* `gen_metrics_gauge`와 `gen_metrics_sum` 끝에 이 행들이 붙어서, 실시간 뷰와 백필이 나머지 메트릭과 함께
    씁니다. 이 변경 전에 설치한 실습은 `bin/backfill_infra.sh`로 과거분을 채웁니다.

### 실행

Managed ClickStack이 켜진 ClickHouse Cloud 서비스와 `CH_HOST`, `CH_USER`, `CH_PASSWORD`가 든 접속 정보 파일이 필요합니다.
ClickStack 단계에는 Cloud API 키도 필요합니다(같은 파일의 `CHC_ORG_ID`, `CHC_KEY_ID`, `CHC_KEY_SECRET`, 또는
`CHC_ENV_FILE`이 가리키는 두 번째 파일의 `CHC_KEY`, `CHC_SECRET` — 이때 조직은 조회해서 찾습니다). 이 실습은 `game_ops`
데이터베이스에만 씁니다. 명령은 영어 절과 같습니다.

`python3 clickstack/setup.py --alerts`는 알림 타일(A1–A9, A9 = 로그가 끊긴 게임 서버)을 만들고 `APM_SLACK_WEBHOOK_URL`의
Slack webhook이 필요합니다. 여기서는 돌리지 않았습니다.

### 검증

**ClickHouse 26.8.1.780**(ClickHouse Cloud, ap-northeast-2)**과 Managed ClickStack에서 확인**, 2026-11-20. ClickStack 빌드는
이번 실행에서 화면을 열지 않아 읽지 못했습니다.

- `bin/install.sh`, `bin/backfill.sh`(8일, 190초. 하루에 span 약 60만 6천, 로그 약 25만 9천 줄 중 게임 서버 약 23만, 메트릭 약
  27만 8천 점), `bin/install.sh rmvs`. `sql/verify_continuity.sql`: 요청 없는 분 없음, 백필 마지막 분과 첫 실시간 분 사이 60초.
- `clickstack/setup.py --test`: 대시보드 타일 27개와 알림 타일 9개의 SQL이 서비스에서 실행됨. `--apply`로 소스 세 개와
  "Game ops" 대시보드를 만들었고, API로 대시보드를 다시 찾았습니다.
- 시나리오 `scenario-20261120T125340Z`, `sql/verify_scenario.sql`로 확인:

  | 구간 | 구매 | 오류 | 구매당 pricing 호출 | billing 외부 호출 p95 |
  |---|---|---|---|---|
  | 시작 전 20분 | 167 | 1.2% | 1.00 | 18 ms |
  | +0 ~ +3, 회귀 | 28 | 7.1% | 4.29 | 21 ms |
  | +3 ~ +12, 회귀 + 느린 결제 대행 | 80 | 3.8% | 4.11 | 1,070 ms |
  | +12 ~ +14, 회귀 | 10 | 0% | 4.40 | 14 ms |

  gs-kr-07: +6:00에 크래시 줄, 1.5초 뒤 플레이어 끊김 줄. +10:00 재시작까지 다른 줄도 게이지 점도 없고(그 분부터 게이지 복귀),
  시작 줄은 버전 1.9.0. 서버 12대 모두 +0:15 ~ +3:00 사이에 재시작했고 그 뒤 1.9.0으로 보고합니다. 배포 행 세 개가 배포
  마커입니다.
- 핫픽스 뒤 구간은 **깨끗한 음성 대조가 아닙니다**: 자동 `pricing-timeouts` 사고(rmv_incidents, 13:05–13:11 UTC)가 겹쳤습니다.
  이제 `bin/scenario.py`는 겹치는 자동 사고가 있으면 거부하고, 이 서비스의 `incidents`는 0입니다.
- 단위 테스트 34개 통과(`python3 -m unittest discover -s tests`).
- **돌리지 않은 것:** `setup.py --alerts`, `bin/s1_check.py`, `bin/uninstall.sh`. 크래시가 난 분에 크래시 전 이벤트를 남기는
  수정은 이 실행 뒤에 했고 `clickhouse local` 26.9.6.6에서만 확인했습니다.

### 서비스에 드는 비용

| | 2026-11-20 측정 |
|---|---|
| 백필 | 8일치 190초 |
| 저장 | 실습 전체 약 347 MiB: `otel_traces` 262 MiB(16.3배), `otel_logs` 59 MiB(9.9배), 메트릭 26 MiB |
| 실시간 생성 | 분당 리프레시 8개: 합쳐서 분당 CPU 약 2.0초, 실제 시간 4.6초(게임 서버 뷰 두 개는 CPU 0.4초), 10분 동안 측정 |
