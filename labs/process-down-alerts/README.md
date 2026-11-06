# Process-down alerts in ClickStack: does an alert notice a series that stopped?

[English](#english) | [한국어](#한국어)

## English

The usual OpenTelemetry way to watch "is the server process up" is the hostmetrics `process`
scraper (or `windowsperfcounters`) filtered to one executable. When the process stops, its
series **stops** — nothing is sent for a process that is not there. This lab checks what five
ClickStack alerts do about that, on a local ClickStack all-in-one `2.42.0` (the HyperDX version
Managed ClickStack runs), with two hosts and one of them losing its process.

> **Verified on** ClickStack all-in-one `2.42.0` (ClickHouse 26.8.34.13), 2026-11-06, one run of
> the scenario below. Notifications counted at the lab's webhook receiver. Full report:
> [`results/2026-11-06-run.md`](results/2026-11-06-run.md).

### Result

| Alert (all `below 1`, every 1 min) | One host's process stops | Every host down | Back up |
|---|---|---|---|
| T1 builder line, count, **group by `host.name`** | **silent** | fires, as one ungrouped alert | state OK, **no resolve notification** |
| T2 SQL line, points per host | **silent** | fires, ungrouped | state OK, no resolve notification |
| T3 SQL line against the **hosts seen in the last hour** (0 where a host sent nothing) | **fires for `host:host-b`** after 76 s | fires per host | resolve per host |
| T4 builder **number** tile filtered to one host | fires after 76 s | fires | resolve |
| T5 builder line, count, no group | silent | fires | resolve |

What this means:

1. **A grouped alert does not notice one missing group.** HyperDX evaluates the rows a bucket
   returns; a host with no rows is not a row. Read in `packages/api/src/tasks/checkAlerts/index.ts`
   at `@hyperdx/api@2.42.0` (time-series path) and now seen in a run. A bucket with no rows at
   all is evaluated as 0, which is why every alert fires when *every* host stops.
2. **The collection method decides it.** A method that stops sending (hostmetrics `process`,
   `windowsperfcounters`) needs an alert that supplies the expected list itself (T3). A method
   that sends an explicit "stopped" value (`windows_service`, a check script that sends 0) works
   with the plain grouped alert.
3. **T3 is a SQL tile alert, and Managed ClickStack accepts alerts on SQL tiles** (used in
   [`labs/apm-workflows`](../apm-workflows/README.md)). Here the expected list is "hosts seen in
   the hour before the window"; an inventory table or EC2 tags would be the production version
   (a host retired on purpose then stops alerting after an hour).
4. **A breaching alert notifies every minute** (5 notifications in a 5-minute stop). With
   thousands of hosts that needs a longer interval, `numConsecutiveWindows`, or routing that
   de-duplicates.
5. Time to first notification: **76 s** from the stop with 20 s sends and a 1-minute alert.
   With the scraper's 60 s interval, add up to one more interval.

### The same test on Managed ClickStack

> **Verified on** Managed ClickStack, our ClickHouse Cloud Seoul service (ClickHouse 26.8.1.608),
> 2026-11-06 06:49–07:08Z, one run. [`managed/managed_alerts.py`](managed/managed_alerts.py) creates
> the dashboard, a generic webhook and the alerts through the ClickHouse Cloud API (SQL tiles only),
> inserts the two hosts' rows over HTTPS every 20 s, reads each alert's `state` every 30–40 s, and
> deletes everything at the end. The webhook pointed at example.com, so nothing was delivered;
> `executionErrors` stayed empty. Report: [`results/2026-11-06-managed.md`](results/2026-11-06-managed.md).

| Alert (SQL tile, `below 1`, 1 m) | host-b stopped | every host down | back up |
|---|---|---|---|
| M2 points per host, group by host | **OK throughout (silent)** | ALERT | OK |
| M3 against the hosts seen in the last hour | **ALERT** within about 2 min | ALERT | OK |
| M4 number tile, host-b | **ALERT** within about 2 min | ALERT | OK |
| M5 all points, no group | OK | ALERT | OK |

Same as the local run: a group-by alert does not fire for a host whose series stopped; the
expected-list SQL tile and a per-host number tile do. The 2 minutes are bounded by the 30–40 s
state polling, not measured to the second (locally the first notification came at 76 s).

### Run it

```bash
cp .env.example .env               # ports 29xxx, clear of _base/ and labs/promql
docker compose up -d               # ClickStack 2.42.0 + webhook receiver
bin/setup.py                       # local test user (.env), webhook, dashboard, 5 alerts
bin/run.py                         # 23 minutes: P0 both up 5 · P1 host-b stopped 6 · P2 both up 4 · P3 all down 5 · P4 both up 3
bin/summarize.py > results/$(date -u +%F)-run.md
docker compose down
```

| File | What |
|---|---|
| `bin/sender.py` | stand-in for the process scraper: `process.memory.usage` for `GameServer.exe`, per host in `out/hosts`, every 20 s (so a 1-minute bucket is never empty by jitter) |
| `sql/t2_grouped.sql`, `sql/t3_expected.sql` | the two SQL tiles; T3 crosses the window's buckets with the expected hosts and left-joins the counts |
| `bin/webhook.py` | the receiver: every POST to `out/webhook.jsonl` with its arrival time |
| `bin/run.py`, `bin/summarize.py` | the phases, alert state every 30 s from the API, the report |

Not covered: Slack as the channel (the generic webhook carries the same text), silences or
maintenance windows, and the Cloud API path for creating the same alerts (that is
[`labs/apm-workflows`](../apm-workflows/README.md)). The "end" rows in the report are the
sender stopping at teardown, not part of the scenario.

---

## 한국어

OpenTelemetry로 "서버 프로세스가 떠 있는가"를 볼 때 보통 hostmetrics `process` scraper(또는
`windowsperfcounters`)를 실행 파일 하나로 거릅니다. 프로세스가 멈추면 그 시계열은 **끊깁니다** —
없는 프로세스에 대해서는 아무것도 보내지 않습니다. 이 실습은 로컬 ClickStack all-in-one `2.42.0`
(Managed ClickStack이 쓰는 HyperDX 버전)에서 호스트 둘 중 하나의 프로세스를 멈췄을 때 ClickStack
알림 다섯 가지가 어떻게 반응하는지 확인합니다.

> **검증:** ClickStack all-in-one `2.42.0`(ClickHouse 26.8.34.13), 2026-11-06, 아래 시나리오 1회.
> 알림은 실습의 webhook 수신기에서 셌습니다. 전체 보고: [`results/2026-11-06-run.md`](results/2026-11-06-run.md).

### 결과

| 알림(모두 `below 1`, 1분마다) | 호스트 하나의 프로세스 중지 | 전체 호스트 중지 | 복구 |
|---|---|---|---|
| T1 빌더 line, count, **`host.name`으로 group by** | **울리지 않음** | 그룹 없는 알림 하나로 울림 | 상태는 OK, **해소 알림 없음** |
| T2 SQL line, 호스트별 point 수 | **울리지 않음** | 그룹 없이 울림 | 상태는 OK, 해소 알림 없음 |
| T3 SQL line, **지난 1시간에 보인 호스트** 대비(보내지 않은 호스트는 0) | 76초 뒤 **`host:host-b`로 울림** | 호스트별로 울림 | 호스트별 해소 |
| T4 빌더 **number** 타일, 호스트 하나로 필터 | 76초 뒤 울림 | 울림 | 해소 |
| T5 빌더 line, count, 그룹 없음 | 울리지 않음 | 울림 | 해소 |

뜻하는 것:

1. **group by 알림은 그룹 하나가 사라진 것을 알아채지 못합니다.** HyperDX는 각 구간이 돌려준 행을
   평가하고, 행이 없는 호스트는 평가 대상이 아닙니다. `@hyperdx/api@2.42.0`의
   `packages/api/src/tasks/checkAlerts/index.ts`(time-series 경로)에서 읽은 내용을 이번 실행으로
   확인했습니다. 구간에 행이 하나도 없으면 0으로 평가하므로, **모든** 호스트가 멈추면 모든 알림이 울립니다.
2. **수집 방법이 결과를 정합니다.** 보내기를 멈추는 방법(hostmetrics `process`, `windowsperfcounters`)은
   기대 목록을 알림이 스스로 만드는 방식(T3)이 필요합니다. "멈춤" 값을 명시적으로 보내는 방법
   (`windows_service`, 0을 보내는 확인 스크립트)은 일반 group by 알림으로 됩니다.
3. **T3는 SQL 타일 알림이고, Managed ClickStack은 SQL 타일에 알림을 걸 수 있습니다**
   ([`labs/apm-workflows`](../apm-workflows/README.md)에서 사용). 여기서 기대 목록은 "구간 전 1시간에
   보인 호스트"입니다. 운영에서는 인벤토리 테이블이나 EC2 태그가 맞습니다(의도적으로 내린 호스트는
   1시간 뒤부터 울리지 않습니다).
4. **조건이 계속 충족되면 1분마다 다시 알립니다**(5분 중지에 알림 5개). 수천 대에서는 더 긴 간격,
   `numConsecutiveWindows`, 또는 중복을 거르는 라우팅이 필요합니다.
5. 첫 알림까지 **76초**(20초 간격 전송, 1분 알림). scraper의 60초 간격이면 최대 한 간격이 더 붙습니다.

### 같은 시험을 Managed ClickStack에서

> **검증:** Managed ClickStack, 우리 조직의 ClickHouse Cloud 서울 서비스(ClickHouse 26.8.1.608),
> 2026-11-06 06:49–07:08Z, 1회. [`managed/managed_alerts.py`](managed/managed_alerts.py)가 ClickHouse Cloud
> API로 대시보드, generic webhook, 알림(SQL 타일만)을 만들고, 두 호스트의 행을 HTTPS로 20초마다 넣고,
> 알림의 `state`를 30–40초마다 읽은 뒤 끝나면 모두 지웁니다. webhook은 example.com이라 아무것도 전달되지
> 않았고 `executionErrors`는 비어 있었습니다. 보고서: [`results/2026-11-06-managed.md`](results/2026-11-06-managed.md).

표는 위 English 절과 같습니다. 로컬 실행과 같습니다: group by 알림은 시계열이 끊긴 호스트에 울리지 않고,
기대 목록 SQL 타일과 호스트별 number 타일은 울립니다. "약 2분"은 30–40초 간격의 상태 조회로 잡힌 값이라
초 단위 측정이 아닙니다(로컬에서는 첫 알림이 76초).

### 실행

위 English 절의 명령과 같습니다. 다루지 않은 것: Slack 채널(일반 webhook이 같은 문구를 받음),
알림 끄기(silence)·점검 시간, 같은 알림을 Cloud API로 만드는 경로([`labs/apm-workflows`](../apm-workflows/README.md)).
보고서의 "end" 행은 정리하면서 sender가 멈춘 것이고 시나리오가 아닙니다.
