# mysql

[English](#english) | [한국어](#한국어)

**Tier A + Tier B** — ships both `custom.config.yaml` and `sidecar.config.yaml`.
Logs run inside ClickStack; metrics need the sidecar.

## English

Self-managed MySQL: engine metrics via the `mysql` receiver, error and slow
query logs via `filelog`. Two tiers in one profile because the two signals
genuinely come from different places — see
[CONVENTIONS.md](../../CONVENTIONS.md) rules 4 and 9.

| | |
|---|---|
| Receivers | `mysql` (Tier B, metrics), `filelog` (Tier A, logs) |
| Signals | metrics, logs |
| `deploy.platform` | `host` — a MySQL process on a machine, not a managed service. Compose with [linux-host](../linux-host/) for the machine itself |
| Mapping | none needed — the receiver already follows the database semantic conventions; see [metrics.md](metrics.md) |

### Why two tiers in one profile

`mysql` (the receiver, beta for metrics) is not in ClickStack's collector
build, so engine metrics need a Tier B sidecar. The error and slow query logs
are plain files on the same machine ClickStack's own collector already reads
from (via the `/hostfs` mount, same as [linux-host](../linux-host/)), and
`filelog` **is** in ClickStack's build, so there is no reason to route logs
through the sidecar too. Rejected alternatives: two separate profiles
(`mysql-logs` / `mysql-metrics`), which would make watching one MySQL instance
a matter of picking two profiles; and putting logs through the sidecar as
well, which would give up the `filelog` receiver ClickStack already has for no
benefit, and demand filesystem access from a container that has no reason to
see the host's disk otherwise.

### Prerequisites

A MySQL 8.x instance reachable from wherever you run the sidecar, and a
monitoring user:

```sql
CREATE USER 'otel_monitor'@'%' IDENTIFIED BY '<password>';
-- SHOW GLOBAL STATUS needs no special grant. SHOW REPLICA STATUS does: it
-- needs REPLICATION CLIENT (or SUPER). Without it the receiver logs, on every
-- scrape, "Error 1227 (42000): Access denied; you need (at least one of) the
-- SUPER, REPLICATION CLIENT privilege(s)" and the rest of the metrics still
-- arrive. Grant it on a replica you want replication lag from (the line below).
-- performance_schema access is needed for the table IO/lock wait and
-- statement-event metrics -- see metrics.md.
GRANT SELECT ON performance_schema.* TO 'otel_monitor'@'%';
-- GRANT REPLICATION CLIENT ON *.* TO 'otel_monitor'@'%';
FLUSH PRIVILEGES;
```

Observed on MySQL 8.4.11 with exactly the statements above (the `REPLICATION
CLIENT` line left commented out): the sidecar logged `Failed to fetch replica
status stats` with that error at info level on each 30 s scrape, and 20
`mysql.*` metric names still arrived. A replica with the grant was not tested.

For the logs half, the collector runs in a container and needs the host
filesystem, same as [linux-host](../linux-host/):

```
-v /:/hostfs:ro
```

`slow_query_log` must be `ON` for anything to land in the slow log at all —
see [metrics.md](metrics.md) for the log path assumptions and the 5.7-vs-8.x
error log format caveat.

Copy [.env.example](.env.example) and [../../sidecar/.env.example](../../sidecar/.env.example)
into one `.env` in `sidecar/` for the metrics half.

### Use

Logs (Tier A, merges into ClickStack's own config):

```bash
cd otel-profiles
./bin/build-config.sh mysql linux-host > custom.config.yaml
```

Mount it into ClickStack along with `/hostfs`, same as
[linux-host](../linux-host/)'s instructions.

Metrics (Tier B, sidecar):

```bash
./bin/build-config.sh --tier b mysql > sidecar/sidecar.config.yaml
cd sidecar
docker compose --env-file .env up -d
```

The sidecar publishes its `health_check` on host port 13133. If it runs on the
same machine as the local stack in [`_base/`](../../../_base/README.md), which
publishes 13133 for its own collector, set `SIDECAR_HEALTH_PORT` (for example
`SIDECAR_HEALTH_PORT=13134`) first: without it `docker compose up` fails with
`Bind for 0.0.0.0:13133 failed: port is already allocated`.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh mysql
```

[verify.sql](verify.sql) checks, in order: engine metrics arriving through the
sidecar, `db.system.name` populated, both logs arriving, and — query 4 — that
the slow log's multi-line entries actually got split and parsed rather than
arriving as unparsed single lines.

**Verified on:** Docker only — MySQL 8.4.11 (`mysql:8.4.11`), ClickStack 2.39.1
(ClickHouse 26.8.7.19), sidecar otel/opentelemetry-collector-contrib:0.155.0,
Docker Desktop 4.93.0; logs through a volume mounted at /hostfs/var/log;
2026-10-01 (UTC). The replica-status metrics (`mysql.replica.*`) need
`REPLICATION CLIENT`, which this run did not grant (see Prerequisites).

What that run showed: queries 1–4 of [verify.sql](verify.sql) returned rows,
query 2 with `db.system.name = mysql`, query 3 with `error.log` and
`mysql-slow.log`, and query 4 with `parsed_slow_entries = 3` and
`unparsed_slow_lines = 0` after a `mysqld` restart while the collector was
reading (the restart wrote a fourth `Version:` header to the slow log, and none
reached `otel_logs`). Before the `filter` operator was added, the same query
returned `unparsed_slow_lines = 5`, all of them that header. The fixture is in
[`_base/`](../../../_base/README.md).

### Notes

`sql_query` (alpha) is documented in [metrics.md](metrics.md) as the escape
hatch for anything the `mysql` receiver does not expose, but it is not part of
this profile's default config — every query is hand-written per deployment,
so there is nothing generic to ship.

---

## 한국어

**Tier A + Tier B** — `custom.config.yaml`과 `sidecar.config.yaml`을 모두
제공합니다. 로그는 ClickStack 내부에서, 지표는 사이드카가 필요합니다.

자체 운영 MySQL입니다. 엔진 지표는 `mysql` receiver로, 에러 로그와 슬로우 쿼리
로그는 `filelog`로 수집합니다. 두 신호가 정말로 다른 곳에서 오기 때문에 한
프로파일에 두 tier를 담았습니다 — [CONVENTIONS.md](../../CONVENTIONS.md) 4번과
9번 규칙 참고.

| | |
|---|---|
| 리시버 | `mysql` (Tier B, 지표), `filelog` (Tier A, 로그) |
| 신호 | metrics, logs |
| `deploy.platform` | `host` — 관리형 서비스가 아니라 머신 위 MySQL 프로세스입니다. 머신 자체는 [linux-host](../linux-host/)와 조합하세요 |
| 매핑 | 필요 없음 — 리시버가 이미 데이터베이스 semantic convention을 따릅니다. [metrics.md](metrics.md) 참고 |

### 왜 한 프로파일에 두 tier인가

`mysql` receiver(지표, beta)는 ClickStack 컬렉터 빌드에 없으므로 엔진 지표는
Tier B 사이드카가 필요합니다. 에러 로그와 슬로우 쿼리 로그는 ClickStack 자체
컬렉터가 이미 읽는 같은 머신의 평범한 파일이고([linux-host](../linux-host/)와
같은 `/hostfs` 마운트로), `filelog`는 ClickStack 빌드에 **있으므로** 로그까지
사이드카로 보낼 이유가 없습니다. 기각한 대안: `mysql-logs`/`mysql-metrics`로
나누는 것 — MySQL 하나를 보려고 프로파일 두 개를 골라야 하게 됩니다. 로그도
사이드카로 보내는 것 — ClickStack에 이미 있는 `filelog` receiver를 이득 없이
버리고, 원래 호스트 디스크를 볼 이유가 없는 컨테이너에 파일시스템 접근을
요구하게 됩니다.

### 전제조건

사이드카를 실행하는 곳에서 접근 가능한 MySQL 8.x 인스턴스와 모니터링 계정:

```sql
CREATE USER 'otel_monitor'@'%' IDENTIFIED BY '<password>';
-- SHOW GLOBAL STATUS에는 별도 권한이 필요 없습니다. SHOW REPLICA STATUS에는
-- 필요합니다: REPLICATION CLIENT(또는 SUPER). 없으면 리시버가 스크레이프마다
-- "Error 1227 (42000): Access denied; you need (at least one of) the SUPER,
-- REPLICATION CLIENT privilege(s)"를 로그로 남기고 나머지 지표는 계속
-- 들어옵니다. 복제 지연이 필요한 레플리카에서는 아래 줄로 권한을 주세요.
-- performance_schema 접근은 테이블 IO/락 대기, statement-event
-- 지표에 필요합니다 -- metrics.md 참고.
GRANT SELECT ON performance_schema.* TO 'otel_monitor'@'%';
-- GRANT REPLICATION CLIENT ON *.* TO 'otel_monitor'@'%';
FLUSH PRIVILEGES;
```

위 구문 그대로(`REPLICATION CLIENT` 줄은 주석 상태로) MySQL 8.4.11에서 관찰한
결과: 사이드카가 30초 스크레이프마다 info 레벨로 `Failed to fetch replica status
stats`와 위 오류를 남겼고, `mysql.*` 지표 이름 20개는 계속 들어왔습니다. 권한을
준 레플리카에서는 시험하지 않았습니다.

로그 쪽은 컬렉터가 컨테이너에서 돌기 때문에 [linux-host](../linux-host/)와
같은 호스트 파일시스템이 필요합니다.

```
-v /:/hostfs:ro
```

슬로우 로그에 뭐라도 쌓이려면 `slow_query_log`가 `ON`이어야 합니다. 로그 경로
가정과 5.7-대-8.x 에러 로그 형식 차이는 [metrics.md](metrics.md)에 있습니다.

지표 쪽을 위해 [.env.example](.env.example)과
[../../sidecar/.env.example](../../sidecar/.env.example)을 합쳐 `sidecar/`
아래 하나의 `.env`로 만듭니다.

### 사용

로그 (Tier A, ClickStack 설정에 병합):

```bash
cd otel-profiles
./bin/build-config.sh mysql linux-host > custom.config.yaml
```

[linux-host](../linux-host/) 안내와 마찬가지로 `/hostfs`와 함께 ClickStack에
마운트합니다.

지표 (Tier B, 사이드카):

```bash
./bin/build-config.sh --tier b mysql > sidecar/sidecar.config.yaml
cd sidecar
docker compose --env-file .env up -d
```

사이드카는 `health_check`를 호스트 포트 13133으로 공개합니다. [`_base/`](../../../_base/README.md)의
로컬 스택은 자체 컬렉터를 위해 13133을 이미 공개하므로, 같은 머신에서 사이드카를
실행한다면 먼저 `SIDECAR_HEALTH_PORT`(예: `SIDECAR_HEALTH_PORT=13134`)를 설정해야
합니다. 그렇지 않으면 `docker compose up`이
`Bind for 0.0.0.0:13133 failed: port is already allocated`로 실패합니다.

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh mysql
```

[verify.sql](verify.sql)이 순서대로 확인하는 것: 사이드카를 통한 엔진 지표
도착, `db.system.name` 채워짐, 두 로그 모두 도착, 그리고 4번 쿼리로 슬로우
로그의 멀티라인 엔트리가 실제로 분리·파싱됐는지(파싱되지 않은 한 줄짜리로
도착하지 않았는지).

**Verified on:** Docker only — MySQL 8.4.11 (`mysql:8.4.11`), ClickStack 2.39.1
(ClickHouse 26.8.7.19), sidecar otel/opentelemetry-collector-contrib:0.155.0,
Docker Desktop 4.93.0; 로그는 /hostfs/var/log에 마운트한 볼륨을 통해;
2026-10-01 (UTC). 복제 상태 지표(`mysql.replica.*`)에는 `REPLICATION CLIENT`가
필요한데, 이 실행에서는 부여하지 않았습니다(전제조건 참고).

이 실행에서 확인한 것: [verify.sql](verify.sql)의 1–4번 쿼리가 모두 행을
반환했습니다. 2번은 `db.system.name = mysql`, 3번은 `error.log`와
`mysql-slow.log`, 4번은 컬렉터가 읽는 중에 `mysqld`를 재시작한 뒤
`parsed_slow_entries = 3`, `unparsed_slow_lines = 0`이었습니다(재시작으로 슬로우
로그에 네 번째 `Version:` 헤더가 쓰였고 `otel_logs`에는 하나도 도달하지
않았습니다). `filter` 연산자를 넣기 전에는 같은 쿼리가
`unparsed_slow_lines = 5`였고 전부 그 헤더였습니다. fixture는
[`_base/`](../../../_base/README.md)에 있습니다.

### 참고

`sql_query`(alpha)는 `mysql` receiver가 노출하지 않는 것을 위한 우회로로
[metrics.md](metrics.md)에 문서화했지만, 이 프로파일의 기본 설정에는
포함하지 않았습니다 — 쿼리마다 배포 환경에 맞게 손으로 작성해야 해서 범용으로
제공할 것이 없습니다.
