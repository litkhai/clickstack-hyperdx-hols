# _base

[English](#english) | [한국어](#한국어)

## English

The two environments every lab in this repository runs against, and one check
that tells you whether the one you picked is ready.

| Target | What it is | Who runs what |
|---|---|---|
| `oss` | the local `docker compose` stack here | you run everything: ClickHouse, HyperDX, MongoDB, collector |
| `cloud` | ClickStack in ClickHouse Cloud | Cloud runs ClickHouse and HyperDX; you run only a collector |

Almost nothing else in the repository needs to care which one is in use.
`otel-profiles/` fragments work in both, because ClickStack honours
`CUSTOM_OTELCOL_CONFIG_FILE` in its standalone mode as well as under the OpAMP
supervisor.

### Local open-source stack

```bash
cd _base
cp .env.example .env
docker compose up -d
./bin/check.sh
```

HyperDX comes up on http://localhost:8080. The other published ports matter for
verification rather than for using the UI:

| Port | What |
|---|---|
| 8080 | HyperDX UI |
| 8000 | HyperDX external API (`/api/v2/...`) |
| 4317 / 4318 | OTLP in |
| 8123 / 9000 | ClickHouse |
| 8888 | collector internal telemetry, Prometheus format |
| 13133 | collector `health_check` |

The image tag is pinned to `2.39.1` rather than `latest`, so a lab's
verification line means something. Its collector is built from components
0.155.0.

**Connect as `api`, not `default`.** The image writes a `users.d` override at
startup that restricts `default` to the container's own localhost, so a query
from the host fails with `Authentication failed: password is incorrect, or
there is no user with such name` — which reads like a wrong password and is
not. `api` and `worker` accept remote connections; their passwords are
published dev defaults in `ClickHouse/ClickStack`'s
`docker/clickhouse/local/users.xml` and exist only in this local image.

**On macOS and Windows**, `/:/hostfs:ro` mounts the root of Docker Desktop's own
Linux VM, not your machine's filesystem. The `linux-host` and `virt-kvm`
profiles will collect the VM's metrics and logs, which is enough to exercise the
config but is not your host. Run those on Linux to see real host data.

### ClickHouse Cloud

Copy `.env.example` to `.env`, set `TARGET=cloud`, and fill in the HTTPS
endpoint on port 8443 with your service credentials.

**A Cloud service is usually shared.** Create a database of your own before
pointing anything at it:

```bash
# edit the name in sql/00-database.sql first if you want a different one,
# then run it against your service and set CH_DATABASE to match
```

`bin/check.sh` fails rather than warns when `CH_DATABASE` does not exist,
because the alternative — a lab quietly writing synthetic telemetry into a
database that already holds real data — is worse than a failed check.

ClickStack's collector runs its own schema migrations, so it creates
`otel_logs`, `otel_traces` and the `otel_metrics_*` tables itself. `sql/` only
creates the database they live in.

### The check

```bash
./bin/check.sh                        # uses ./.env
./bin/check.sh --env-file ../my.env
```

It needs only `curl`. Checks, in order: ClickHouse reachable, `CH_DATABASE`
exists, how many `otel_*` tables are in it, the HyperDX external API answers
with a key, and — on `oss` only — the collector's health endpoint and its
internal telemetry.

Set `EXPECT_RECEIVER` to assert a specific receiver is live, which is how you
tell "my merged profile config loaded" from "the collector started without it":

```bash
EXPECT_RECEIVER=hostmetrics/linux-host ./bin/check.sh
```

**A `SKIP` is not a `PASS`.** The script says so at the end and counts them,
because a check that quietly did not run is how a broken pipeline looks healthy.

### Secrets

`.env` is gitignored; `.env.example` carries placeholders and is not. Nothing
here writes a credential into a tracked file. If a password has ever been pasted
somewhere it might be recorded — a chat log, a terminal transcript, a ticket —
rotate it rather than assuming it stayed private.

---

## 한국어

이 저장소의 모든 실습이 대상으로 삼는 두 환경과, 선택한 환경이 준비됐는지
알려주는 검사 하나입니다.

| 대상 | 내용 | 누가 무엇을 운영하나 |
|---|---|---|
| `oss` | 여기의 로컬 `docker compose` 스택 | 전부 직접: ClickHouse, HyperDX, MongoDB, 컬렉터 |
| `cloud` | ClickHouse Cloud의 ClickStack | Cloud가 ClickHouse·HyperDX, 사용자는 컬렉터만 |

저장소의 나머지는 어느 쪽인지 거의 신경 쓰지 않아도 됩니다. ClickStack이
OpAMP supervisor 모드뿐 아니라 standalone 모드에서도
`CUSTOM_OTELCOL_CONFIG_FILE`을 적용하므로 `otel-profiles/` 조각은 양쪽에서
그대로 동작합니다.

### 로컬 오픈소스 스택

```bash
cd _base
cp .env.example .env
docker compose up -d
./bin/check.sh
```

HyperDX는 http://localhost:8080 에 올라옵니다. 나머지 공개 포트는 UI 사용보다
검증에 필요한 것들입니다.

| 포트 | 용도 |
|---|---|
| 8080 | HyperDX UI |
| 8000 | HyperDX 외부 API (`/api/v2/...`) |
| 4317 / 4318 | OTLP 수신 |
| 8123 / 9000 | ClickHouse |
| 8888 | 컬렉터 내부 텔레메트리, Prometheus 형식 |
| 13133 | 컬렉터 `health_check` |

이미지 태그를 `latest`가 아니라 `2.39.1`로 고정했습니다. 그래야 실습의 검증
기록이 의미를 갖습니다. 이 버전의 컬렉터는 컴포넌트 0.155.0으로 빌드됩니다.

**`default`가 아니라 `api`로 접속하세요.** 이미지가 기동 시 `users.d` 오버라이드를
써서 `default` 유저를 컨테이너 자체 localhost로 제한합니다. 그래서 호스트에서 쿼리하면
`Authentication failed: password is incorrect, or there is no user with such name`이
나오는데, 비밀번호 문제처럼 읽히지만 아닙니다. `api`와 `worker`는 원격 접속을 허용하며,
비밀번호는 `ClickHouse/ClickStack`의 `docker/clickhouse/local/users.xml`에 공개된 개발용
기본값이고 이 로컬 이미지에만 존재합니다.

**macOS와 Windows에서는** `/:/hostfs:ro`가 여러분 머신의 파일시스템이 아니라
Docker Desktop 내부 Linux VM의 루트를 마운트합니다. `linux-host`와 `virt-kvm`
프로파일은 그 VM의 지표와 로그를 수집하므로 설정을 시험하기에는 충분하지만
여러분의 호스트는 아닙니다. 실제 호스트 데이터를 보려면 Linux에서 실행하세요.

### ClickHouse Cloud

`.env.example`을 `.env`로 복사하고 `TARGET=cloud`로 설정한 뒤, 8443 포트의
HTTPS 엔드포인트와 서비스 자격증명을 채웁니다.

**Cloud 서비스는 보통 공유됩니다.** 무엇이든 연결하기 전에 자기 데이터베이스를
먼저 만드세요.

```bash
# 이름을 바꾸려면 sql/00-database.sql을 먼저 수정하고,
# 서비스에 실행한 뒤 CH_DATABASE를 같은 이름으로 맞춥니다
```

`bin/check.sh`는 `CH_DATABASE`가 없을 때 경고가 아니라 **실패**로 처리합니다.
대안 — 실습이 남의 실제 데이터가 든 데이터베이스에 조용히 합성 텔레메트리를
쓰는 것 — 이 실패한 검사보다 나쁘기 때문입니다.

ClickStack 컬렉터가 자체 스키마 마이그레이션을 실행하므로 `otel_logs`,
`otel_traces`, `otel_metrics_*` 테이블은 컬렉터가 직접 만듭니다. `sql/`은 그것들이
들어갈 데이터베이스만 만듭니다.

### 검사

```bash
./bin/check.sh                        # ./.env 사용
./bin/check.sh --env-file ../my.env
```

`curl`만 필요합니다. 순서대로 확인합니다: ClickHouse 접근, `CH_DATABASE` 존재
여부, 그 안의 `otel_*` 테이블 수, 키가 있을 때 HyperDX 외부 API 응답, 그리고
`oss`에서만 컬렉터 health 엔드포인트와 내부 텔레메트리.

`EXPECT_RECEIVER`를 지정하면 특정 리시버가 살아있는지 단언합니다. "병합한
프로파일 설정이 로드됐다"와 "컬렉터가 그것 없이 시작했다"를 구분하는 방법입니다.

```bash
EXPECT_RECEIVER=hostmetrics/linux-host ./bin/check.sh
```

**`SKIP`은 `PASS`가 아닙니다.** 스크립트가 마지막에 개수를 세어 알려줍니다.
조용히 실행되지 않은 검사가 바로 깨진 파이프라인이 건강해 보이는 방식입니다.

### 비밀값

`.env`는 gitignore되고, 자리표시자만 든 `.env.example`은 추적됩니다. 여기의
어떤 것도 자격증명을 추적되는 파일에 쓰지 않습니다. 비밀번호를 기록이 남을 수
있는 곳 — 채팅 로그, 터미널 기록, 티켓 — 에 붙여넣은 적이 있다면, 비공개로
남았다고 가정하지 말고 교체하세요.
