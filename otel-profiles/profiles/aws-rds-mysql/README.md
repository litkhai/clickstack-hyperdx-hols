# aws-rds-mysql

[English](#english) | [한국어](#한국어)

**Tier B** — needs a sidecar collector. ClickStack's own build has neither the
`mysql` nor the `awscloudwatch` receiver.

## English

> **Related notes** (Korean): [OTel Collector 하나로 Kafka, MySQL, MSSQL, Prometheus, 클라우드 로그를 ClickHouse Cloud에 통합하기](https://clickhouse.litkhai.dev/articles/case-study/otel-collector-kafka-mysql-mssql-prometheus-clickhouse-cloud/)

MySQL on Amazon RDS: engine metrics straight from the instance, everything
else — instance metrics, Enhanced Monitoring, error and slow query logs —
from CloudWatch, since RDS gives the collector no filesystem to read.

| | |
|---|---|
| Receivers | `mysql` (**beta**, metrics), `awscloudwatch` (**alpha**, logs and metrics) — both `distributions: [contrib]` |
| Signals | metrics, logs |
| `deploy.platform` | `managed` — a managed service, not a machine class. Paired with `cloud.provider=aws`, `cloud.region`, `db.system.name=mysql` |
| Mapping | none needed — see [metrics.md](metrics.md) |

### Why a separate profile, not a variant of `mysql`

[mysql](../mysql/) reads its error and slow query logs from files via
`filelog`, mounting the host's `/hostfs`. RDS is a managed instance: there is
no host filesystem to mount, and the logs only exist because RDS optionally
ships them to CloudWatch Logs. Everything downstream of that — the receiver
(`awscloudwatch` instead of `filelog`), the credentials (AWS IAM instead of a
bind mount), the cost model (CloudWatch API billing instead of free file
reads) — is different enough that a shared profile would need a fork inside
almost every file. See [metrics.md](metrics.md) for the full routing table.

### Prerequisites

- An RDS MySQL instance, its Resource ID and instance identifier (RDS console
  → the instance → Configuration).
- **Log exports enabled** on the instance for the error and slow query logs
  (RDS console → Modify → Log exports), and `slow_query_log=1` in the
  instance's parameter group. Without both, the corresponding CloudWatch Logs
  group never gets created and query 5 in [verify.sql](verify.sql) stays empty
  — that is a configuration gap, not a broken profile.
- A database user with:
  ```sql
  CREATE USER 'otel_monitor'@'%' IDENTIFIED BY '<password>';
  GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'otel_monitor'@'%';
  GRANT SELECT ON performance_schema.* TO 'otel_monitor'@'%';
  ```
  RDS grants no `SUPER`; this is the full set the `mysql` receiver needs.
- An IAM principal with at minimum `cloudwatch:GetMetricData` and
  `logs:FilterLogEvents` — the exact policy is in [metrics.md](metrics.md).

Copy [.env.example](.env.example) and [../../sidecar/.env.example](../../sidecar/.env.example)
into one `.env` in `sidecar/`.

### Use

```bash
cd otel-profiles
./bin/build-config.sh --tier b aws-rds-mysql > sidecar/sidecar.config.yaml
cd sidecar
docker compose --env-file .env up -d
```

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh aws-rds-mysql
```

[verify.sql](verify.sql) checks, in order: engine metrics from the direct
connection, the five CloudWatch instance metrics, that `cloud.*`/`db.system.name`
landed on **both** metric sources (not just the one that sets them natively),
that Enhanced Monitoring's JSON body actually got parsed rather than arriving
as an opaque string, and that the error/slow logs arrived from CloudWatch.

`collection_interval` is 5m for the CloudWatch metrics — allow at least two
intervals before concluding anything from an empty result.

**Not verified yet, and likely to stay that way longer than most profiles
here**: this needs a real RDS instance and an IAM principal, not just a local
container, so it may take longer to get an end-to-end run than
[mysql](../mysql/) or the hardware profiles. No `Verified on …` line — with
the ClickHouse and ClickStack versions plus the sidecar image tag — until one
actually happens.

### Notes

- **CloudWatch cost.** `GetMetricData` is billed per metric per call; this
  profile defaults to a 5-minute interval and explicit single-statistic
  queries rather than the four-statistic Summary default. See
  [metrics.md](metrics.md) for the reasoning and for Metric Streams
  (`awsfirehose`) as a cheaper option at fleet scale — documented there, not
  defaulted to, because its built-in encodings are deprecated upstream.
- **Performance Insights is out of scope.** It has its own API, not an OTel
  receiver; see [metrics.md](metrics.md).
- **`db.system.name`, not `db.system`.** Checked against the `mysql`
  receiver's `metadata.yaml`: semconv renamed the attribute and the receiver
  emits the new name (off by default upstream). This profile sets it itself
  via `resource/aws-rds-mysql` instead of relying on the receiver's own
  toggle, since that toggle would not reach the `awscloudwatch`-sourced
  metrics.

---

## 한국어

> **관련 글**: [OTel Collector 하나로 Kafka, MySQL, MSSQL, Prometheus, 클라우드 로그를 ClickHouse Cloud에 통합하기](https://clickhouse.litkhai.dev/articles/case-study/otel-collector-kafka-mysql-mssql-prometheus-clickhouse-cloud/)

**Tier B** — 사이드카 컬렉터가 필요합니다. ClickStack 자체 빌드에는 `mysql`도
`awscloudwatch`도 없습니다.

Amazon RDS의 MySQL입니다. 엔진 지표는 인스턴스에서 직접, 나머지 — 인스턴스
지표, Enhanced Monitoring, 에러/슬로우 쿼리 로그 — 는 CloudWatch에서
가져옵니다. RDS는 컬렉터에게 파일시스템을 주지 않기 때문입니다.

| | |
|---|---|
| 리시버 | `mysql` (**beta**, 지표), `awscloudwatch` (**alpha**, 로그·지표) — 둘 다 `distributions: [contrib]` |
| 신호 | metrics, logs |
| `deploy.platform` | `managed` — 머신 종류가 아니라 관리형 서비스입니다. `cloud.provider=aws`, `cloud.region`, `db.system.name=mysql`과 함께 씁니다 |
| 매핑 | 필요 없음 — [metrics.md](metrics.md) 참고 |

### 왜 `mysql`의 변형이 아니라 별도 프로파일인가

[mysql](../mysql/)은 `/hostfs`를 마운트해서 `filelog`로 에러·슬로우 로그를
파일에서 읽습니다. RDS는 관리형 인스턴스라 마운트할 호스트 파일시스템이 없고,
로그는 RDS가 선택적으로 CloudWatch Logs로 보내야만 존재합니다. 그 아래
모든 것 — receiver(`filelog` 대신 `awscloudwatch`), 자격증명(bind mount
대신 AWS IAM), 비용 모델(무료 파일 읽기 대신 CloudWatch API 과금) — 이 달라서
공유 프로파일로 만들면 거의 모든 파일에 분기가 필요해집니다. 전체 라우팅
표는 [metrics.md](metrics.md)에 있습니다.

### 전제조건

- RDS MySQL 인스턴스, 그 Resource ID와 인스턴스 식별자 (RDS 콘솔 → 인스턴스 →
  Configuration).
- 인스턴스에서 **로그 내보내기 활성화** (RDS 콘솔 → Modify → Log exports)로
  에러·슬로우 쿼리 로그를 켜고, 파라미터 그룹에서 `slow_query_log=1`을
  설정합니다. 둘 다 안 하면 해당 CloudWatch Logs 그룹이 생성되지 않아
  [verify.sql](verify.sql) 5번 쿼리가 계속 비어 있습니다 — 프로파일이
  고장난 게 아니라 설정이 빠진 것입니다.
- 다음 권한을 가진 데이터베이스 사용자:
  ```sql
  CREATE USER 'otel_monitor'@'%' IDENTIFIED BY '<password>';
  GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'otel_monitor'@'%';
  GRANT SELECT ON performance_schema.* TO 'otel_monitor'@'%';
  ```
  RDS는 `SUPER`를 주지 않습니다. 이것이 `mysql` receiver에 필요한 전체
  권한입니다.
- 최소 `cloudwatch:GetMetricData`와 `logs:FilterLogEvents`를 가진 IAM
  주체. 정확한 정책은 [metrics.md](metrics.md)에 있습니다.

[.env.example](.env.example)과 [../../sidecar/.env.example](../../sidecar/.env.example)을
합쳐 `sidecar/` 아래 하나의 `.env`로 만듭니다.

### 사용

```bash
cd otel-profiles
./bin/build-config.sh --tier b aws-rds-mysql > sidecar/sidecar.config.yaml
cd sidecar
docker compose --env-file .env up -d
```

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh aws-rds-mysql
```

[verify.sql](verify.sql)이 순서대로 확인하는 것: 직접 연결로 얻은 엔진 지표,
CloudWatch 인스턴스 지표 5개, `cloud.*`/`db.system.name`이 (자체적으로 설정하는
쪽뿐 아니라) 두 지표 소스 모두에 붙었는지, Enhanced Monitoring의 JSON
바디가 불투명한 문자열이 아니라 실제로 파싱됐는지, 에러·슬로우 로그가
CloudWatch에서 도착했는지.

CloudWatch 지표의 `collection_interval`은 5분입니다. 결과가 비었다고
판단하기 전에 최소 두 주기를 기다리세요.

**아직 검증하지 않았고, 여기 다른 프로파일보다 더 오래 미검증으로 남을
가능성이 높습니다**: 로컬 컨테이너가 아니라 실제 RDS 인스턴스와 IAM 주체가
필요해서 [mysql](../mysql/)이나 하드웨어 프로파일보다 end-to-end 실행까지
시간이 더 걸릴 수 있습니다. ClickHouse·ClickStack 버전과 사이드카 이미지
태그를 갖춘 실제 실행 전에는 `Verified on …` 줄을 쓰지 않습니다.

### 참고

- **CloudWatch 비용.** `GetMetricData`는 호출당 메트릭 단위로 과금됩니다.
  이 프로파일은 기본값으로 5분 간격과, 4개 통계의 Summary 기본값 대신
  명시적인 단일 통계 쿼리를 씁니다. 근거와, 대규모에서 더 저렴한 대안인
  Metric Streams(`awsfirehose`)는 [metrics.md](metrics.md)에 문서화했지만
  기본값은 아닙니다 — 내장 인코딩이 업스트림에서 deprecated이기 때문입니다.
- **Performance Insights는 범위 밖입니다.** 자체 API만 있고 OTel receiver가
  없습니다. [metrics.md](metrics.md) 참고.
- **`db.system`이 아니라 `db.system.name`.** `mysql` receiver의
  `metadata.yaml`을 직접 확인했습니다: semconv가 속성 이름을 바꿨고 receiver는
  새 이름을 방출합니다(업스트림 기본값은 비활성). 이 프로파일은 receiver
  자체의 토글에 의존하지 않고 `resource/aws-rds-mysql`로 직접 설정합니다 —
  그 토글은 `awscloudwatch` 쪽 지표까지는 닿지 않기 때문입니다.
