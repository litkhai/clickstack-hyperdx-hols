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
`otel-profiles/` fragments are written to work in both. What was read from the
pinned image: the all-in-one container always runs the collector under the
OpAMP supervisor, which loads `CUSTOM_OTELCOL_CONFIG_FILE` after
`/etc/otelcol-contrib/config.yaml`. `/otel-entrypoint.sh` also has a standalone
branch that does the same without OpAMP, which the all-in-one never takes
(`clickstack-all-in-one:2.39.1`, `otelcol-hyperdx` 0.155.0; see rule 1 in
[`otel-profiles/CONVENTIONS.md`](../otel-profiles/CONVENTIONS.md)). The
collector you run next to a Cloud service was not read.

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
| 8124 / 9001 | the pinned migration-target ClickHouse, `migration` profile only |

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

### Verifying the `otel-profiles` in Docker

`docker-compose.otel-verify.yml` is an override that exists only to verify the
`linux-host` and `mysql` profiles against this stack. A plain `docker compose
up -d` does not use it. It is needed because on Docker Desktop (4.93.0) there is
no host to read logs from:

- `/hostfs` is Docker Desktop's own Linux VM (kernel `7.0.14-linuxkit`), so
  `hostmetrics` reads real kernel counters, but the VM's `/var/log` holds only
  `lastlog`: no `syslog`, no `messages`.
- A named volume **can** be mounted at `/hostfs/var/log` (the directory exists in
  the VM). It **cannot** be mounted at `/hostfs/var/log/mysql`: Docker answers
  `read-only file system`.

So the override mounts one volume, `otel-verify-logs`, at `/hostfs/var/log:ro` in
ClickStack, and the things that write logs put their files in it. The profiles'
configs and paths are not changed.

| Service | Compose profile | What it writes |
|---|---|---|
| `syslog-noble` | `ubuntu-noble` | `/var/log/syslog` from rsyslog in `ubuntu:noble-20260911` (ISO 8601 lines) |
| `syslog-jammy` | `ubuntu-jammy` | `/var/log/syslog` from rsyslog in `ubuntu:jammy-20260901.2` (RFC 3164 lines) |
| `mysql` | `mysql` | `/var/log/mysql/error.log` and `mysql-slow.log` from `mysql:8.4.11`, plus the `otel_monitor` user |

Both rsyslogs run the package's default configuration with only `imklog` off (a
container has no kernel log). Run one at a time: the second one moves the first
one's `syslog` aside, as logrotate would, because the two releases' `syslog`
users have different uids. The `mysql` password is a published local default
(`otel-local-only`), overridable with `MYSQL_MONITOR_PASSWORD` in `.env` --
throwaway, localhost only, like `ES_SECURE_PASSWORD` above.

```bash
cd _base
F="-f docker-compose.yml -f docker-compose.otel-verify.yml"

# linux-host alone
../otel-profiles/bin/build-config.sh linux-host > ../otel-profiles/custom.config.yaml
docker compose $F --profile ubuntu-noble up -d --build
EXPECT_RECEIVER=hostmetrics/linux-host ./bin/check.sh
docker compose $F --profile ubuntu-noble rm -sf syslog-noble      # then the other release
docker compose $F --profile ubuntu-jammy up -d --build
CH_URL=http://localhost:8123 CH_USER=api CH_PASSWORD=api ../otel-profiles/bin/verify.sh linux-host

# mysql: logs through ClickStack, metrics through the sidecar
../otel-profiles/bin/build-config.sh mysql linux-host > ../otel-profiles/custom.config.yaml
../otel-profiles/bin/build-config.sh --tier b mysql > ../otel-profiles/sidecar/sidecar.config.yaml
docker compose $F up -d --force-recreate clickstack              # the config is a bind mount
docker compose $F --profile mysql up -d mysql
```

`--force-recreate` is needed because Compose does not notice that the content of a
bind-mounted file changed. The sidecar then runs from `otel-profiles/sidecar/`
with `CLICKSTACK_OTLP_ENDPOINT=host.docker.internal:4317`,
`CLICKSTACK_OTLP_INSECURE=true`, `MYSQL_ENDPOINT=host.docker.internal:3306`,
`MYSQL_USERNAME=otel_monitor`, `MYSQL_PASSWORD=otel-local-only`,
`MYSQL_TLS_INSECURE=true` and `SIDECAR_HEALTH_PORT=13134` in an env file, plus
`--env-file ../../_base/.env`, so the ingestion key is read from `HYPERDX_INGESTION_KEY`
there and not copied (see the [mysql profile](../otel-profiles/profiles/mysql/README.md)).
If something else holds 8123 or 9000 on your machine, publish ClickHouse on other
ports with a further `-f` override and point `CH_URL` there.

### Optional: a migration source and a migration target

For `labs/elastic-migration/` only. Both are off by default, behind compose
profiles, so a plain `docker compose up -d` is unaffected:

```bash
docker compose --profile elastic up -d     # Elasticsearch 8.17.0 + the target
docker compose --profile migration up -d   # the target on its own
./bin/seed_elasticsearch.py                # 300,000 synthetic log documents
```

| Service | Port | Profile | What it is |
|---|---|---|---|
| `elasticsearch` | 9200 | `elastic` | the cluster to migrate **from** |
| `clickhouse-target` | 8124 / 9001 | `elastic`, `migration` | the ClickHouse to migrate **to** |
| `elasticsearch-secure` | 9201 | `elastic-secure` | the same Elasticsearch with **security on**, which is the 8.x default |
| `grafana` | 3000 | `grafana` | Grafana 13.2.3 with both of the above as data sources, for `labs/elastic-migration/dashboards/` |

**`grafana` is started together with `elastic`** (`docker compose --profile
elastic --profile grafana up -d`): it has no `depends_on`, because a
dependency on a service whose profile is off is a Compose error. Both plugins
are installed at startup by `GF_PLUGINS_PREINSTALL_SYNC`, so the first start
needs internet access, and both are pinned -- `elasticsearch` 12.9.1 and
`grafana-clickhouse-datasource` 4.22.0. Since Grafana 13 the Elasticsearch data
source is a separate plugin that floats to the latest release when unpinned.
The data sources are provisioned from `grafana/provisioning/` (uids `es` and
`ch`). Log in as `admin` / `GRAFANA_ADMIN_PASSWORD` (a local default in
`.env.example`).

**`docker-compose.ingest-verify.yml`** is a further override, used only by
`labs/elastic-migration/ingest/check.py`. It loads that lab's generated
collector fragments into ClickStack's own collector (`CUSTOM_OTELCOL_CONFIG_FILE`)
and mounts an input directory at `/ingest-verify/in`. `check.py` recreates
`clickstack` with it, and `--restore` recreates it without it. Do not leave it
on: it replaces whatever custom config you had.

**`elastic-secure` is the one that resembles a real source cluster.** The
`elastic` profile has `xpack.security.enabled=false`, which is why every tool
in `labs/elastic-migration/data/` worked for a while without being able to
authenticate at all. Use it to exercise the authenticated path:

```bash
docker compose --profile elastic-secure up -d
ES_URL=http://localhost:9201 ES_USER=elastic ES_PASSWORD=elastic-local-only \
    ./bin/seed_elasticsearch.py --recreate --docs 20000
```

HTTP TLS is off on that profile on purpose: authentication is what the tools
needed, and a self-signed CA on top would mean copying a certificate out of a
container before anything works. The CA path is covered by `ES_CA_CERT` and
verified against a default-configuration container instead -- see
`labs/elastic-migration/data/README.md`.

**Elasticsearch 8.17.0**, single node, security disabled
(`xpack.security.enabled=false`). That is only acceptable because it holds
nothing but seeded synthetic data on localhost -- never do this against a
cluster with anything real in it. See
`labs/elastic-migration/data/README.md` for what the seeded mapping is
designed to exercise.

**Why a second ClickHouse rather than the one inside the all-in-one image.**
A migration lands in ClickHouse Cloud, whose regular release channel is on
the 26.6 line; the all-in-one 2.39.1 bundles 26.8.7.19. Verifying a
migration against a *newer* ClickHouse than the destination can prove a
feature the destination does not have yet, which is the one mistake a
migration lab cannot afford. So the target is pinned to `26.6.8.7` -- the
newest public patch of the line Cloud runs. Same minor, not the same build:
Cloud's own builds are not published.

The target accepts `default` with no password from the host
(`CLICKHOUSE_SKIP_USER_SETUP`), unlike the all-in-one image. Recent server
images otherwise mint a random password at first start, which no documented
command could then use. Throwaway and localhost only, as above.

Point the lab somewhere else -- your own Cloud service, for instance -- with
the `CH_TARGET_*` variables in `.env.example`; they override `CH_*` for the
migration path only.

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

### End-to-end check

`bin/check.sh` only says the target is reachable. `bin/verify.sh` sends known
telemetry and follows it through:

```bash
./bin/verify.sh
```

It tags 200 log records with a unique `verify.run_id`, waits for them, then
counts them in `otel_logs` and searches for them through the HyperDX API. The
two are checked separately on purpose: **SQL passing while search fails means
the log source definition is wrong, not the ingestion** — which is the failure
that just looks like an empty UI.

Needs `HYPERDX_INGESTION_KEY` as well as `HYPERDX_API_KEY`; they are different
keys and ClickStack's OTLP receiver rejects unauthenticated data.

**Verified on:** ClickStack 2.39.1 (ClickHouse 26.8.7.19), telemetrygen v0.155.0,
2026-10-01. `bin/verify.sh` ran against the local stack and all three layers
passed (200 logs emitted, 200 rows in `otel_logs`, 200 rows through the HyperDX
search); a negative control with a wrong `authorization` header was refused by
the receiver (`Unauthenticated`) and left 0 rows under its `verify.run_id`.
telemetrygen emits about one log per second, so a run takes around four minutes.

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

저장소의 나머지는 어느 쪽인지 거의 신경 쓰지 않아도 됩니다. `otel-profiles/`
조각은 양쪽에서 동작하도록 작성했습니다. 고정한 이미지에서 확인한 것: all-in-one
컨테이너는 항상 OpAMP supervisor 아래에서 컬렉터를 실행하고, supervisor가
`/etc/otelcol-contrib/config.yaml` 다음에 `CUSTOM_OTELCOL_CONFIG_FILE`을
로드합니다. `/otel-entrypoint.sh`에는 OpAMP 없이 같은 일을 하는 standalone 분기도
있지만 all-in-one은 이 분기를 타지 않습니다(`clickstack-all-in-one:2.39.1`,
`otelcol-hyperdx` 0.155.0, [`otel-profiles/CONVENTIONS.md`](../otel-profiles/CONVENTIONS.md)
1번 규칙 참고). Cloud 서비스 옆에서 실행하는 컬렉터는 확인하지 않았습니다.

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

### `otel-profiles`를 Docker에서 검증하기

`docker-compose.otel-verify.yml`은 이 스택에 대해 `linux-host`와 `mysql` 프로파일을
검증하기 위해서만 존재하는 오버라이드입니다. 평범한 `docker compose up -d`는 이
파일을 쓰지 않습니다. Docker Desktop(4.93.0)에는 로그를 읽을 호스트가 없어서 필요합니다.

- `/hostfs`는 Docker Desktop 자체의 Linux VM(kernel `7.0.14-linuxkit`)이라
  `hostmetrics`는 실제 커널 카운터를 읽지만, VM의 `/var/log`에는 `lastlog`뿐입니다.
  `syslog`도 `messages`도 없습니다.
- 이름 있는 볼륨은 `/hostfs/var/log`에 마운트**할 수 있습니다**(VM에 그 디렉터리가
  있습니다). `/hostfs/var/log/mysql`에는 마운트**할 수 없습니다**. Docker가
  `read-only file system`으로 답합니다.

그래서 오버라이드는 볼륨 하나, `otel-verify-logs`를 ClickStack의 `/hostfs/var/log:ro`에
마운트하고, 로그를 쓰는 쪽이 그 안에 파일을 둡니다. 프로파일의 설정과 경로는 바꾸지
않습니다.

| 서비스 | Compose 프로파일 | 쓰는 것 |
|---|---|---|
| `syslog-noble` | `ubuntu-noble` | `ubuntu:noble-20260911`의 rsyslog가 쓰는 `/var/log/syslog` (ISO 8601 형식) |
| `syslog-jammy` | `ubuntu-jammy` | `ubuntu:jammy-20260901.2`의 rsyslog가 쓰는 `/var/log/syslog` (RFC 3164 형식) |
| `mysql` | `mysql` | `mysql:8.4.11`의 `/var/log/mysql/error.log`, `mysql-slow.log`와 `otel_monitor` 사용자 |

두 rsyslog 모두 패키지 기본 설정에 `imklog`만 끈 상태입니다(컨테이너에는 읽을 커널
로그가 없습니다). 한 번에 하나만 실행하세요. 두 릴리스의 `syslog` 사용자 uid가
달라서, 나중에 뜬 쪽이 logrotate처럼 앞쪽의 `syslog`를 옆으로 옮깁니다. `mysql`
비밀번호는 공개된 로컬 기본값(`otel-local-only`)이고 `.env`의 `MYSQL_MONITOR_PASSWORD`로
바꿀 수 있습니다. 위의 `ES_SECURE_PASSWORD`처럼 임시·localhost 전용입니다.

```bash
cd _base
F="-f docker-compose.yml -f docker-compose.otel-verify.yml"

# linux-host 단독
../otel-profiles/bin/build-config.sh linux-host > ../otel-profiles/custom.config.yaml
docker compose $F --profile ubuntu-noble up -d --build
EXPECT_RECEIVER=hostmetrics/linux-host ./bin/check.sh
docker compose $F --profile ubuntu-noble rm -sf syslog-noble      # 그다음 다른 릴리스
docker compose $F --profile ubuntu-jammy up -d --build
CH_URL=http://localhost:8123 CH_USER=api CH_PASSWORD=api ../otel-profiles/bin/verify.sh linux-host

# mysql: 로그는 ClickStack, 지표는 사이드카
../otel-profiles/bin/build-config.sh mysql linux-host > ../otel-profiles/custom.config.yaml
../otel-profiles/bin/build-config.sh --tier b mysql > ../otel-profiles/sidecar/sidecar.config.yaml
docker compose $F up -d --force-recreate clickstack              # 설정은 bind mount
docker compose $F --profile mysql up -d mysql
```

Compose는 bind mount한 파일의 내용이 바뀐 것을 알아차리지 못하므로
`--force-recreate`가 필요합니다. 사이드카는 `otel-profiles/sidecar/`에서
`CLICKSTACK_OTLP_ENDPOINT=host.docker.internal:4317`, `CLICKSTACK_OTLP_INSECURE=true`,
`MYSQL_ENDPOINT=host.docker.internal:3306`, `MYSQL_USERNAME=otel_monitor`,
`MYSQL_PASSWORD=otel-local-only`, `MYSQL_TLS_INSECURE=true`,
`SIDECAR_HEALTH_PORT=13134`를 담은 env 파일과 `--env-file ../../_base/.env`로
실행합니다. 수집 키는 거기의 `HYPERDX_INGESTION_KEY`에서 읽으며 복사하지 않습니다
([mysql 프로파일](../otel-profiles/profiles/mysql/README.md) 참고). 이 머신에서 다른 것이
8123이나 9000을 쓰고 있다면 `-f` 오버라이드를 하나 더 두어 ClickHouse를 다른
포트로 공개하고 `CH_URL`을 거기에 맞추세요.

### 선택: 마이그레이션 원본과 목적지

`labs/elastic-migration/`에만 필요합니다. 둘 다 compose 프로파일 뒤에 있어
기본적으로 꺼져 있고, 평범한 `docker compose up -d`에는 영향이 없습니다.

```bash
docker compose --profile elastic up -d     # Elasticsearch 8.17.0 + 목적지
docker compose --profile migration up -d   # 목적지만
./bin/seed_elasticsearch.py                # 합성 로그 문서 300,000건
```

| 서비스 | 포트 | 프로파일 | 역할 |
|---|---|---|---|
| `elasticsearch` | 9200 | `elastic` | 마이그레이션 **원본** 클러스터 |
| `clickhouse-target` | 8124 / 9001 | `elastic`, `migration` | 마이그레이션 **목적지** ClickHouse |
| `elasticsearch-secure` | 9201 | `elastic-secure` | 보안을 **켠** 같은 Elasticsearch. 8.x 기본값입니다 |
| `grafana` | 3000 | `grafana` | 위 둘을 데이터 소스로 연결한 Grafana 13.2.3. `labs/elastic-migration/dashboards/`용 |

**`grafana`는 `elastic`과 함께 띄웁니다**(`docker compose --profile elastic
--profile grafana up -d`). `depends_on`이 없는데, 꺼진 프로파일의 서비스에 의존하면
Compose가 오류를 내기 때문입니다. 두 플러그인은 시작할 때
`GF_PLUGINS_PREINSTALL_SYNC`로 설치되므로 첫 시작에는 인터넷이 필요하고, 둘 다
버전을 고정했습니다 -- `elasticsearch` 12.9.1, `grafana-clickhouse-datasource`
4.22.0. Grafana 13부터 Elasticsearch 데이터 소스는 별도 플러그인이라, 고정하지
않으면 최신 릴리스로 바뀝니다. 데이터 소스는 `grafana/provisioning/`에서
프로비저닝됩니다(uid `es`, `ch`). 로그인은 `admin` / `GRAFANA_ADMIN_PASSWORD`
(`.env.example`의 로컬 기본값)입니다.

**`docker-compose.ingest-verify.yml`**은 `labs/elastic-migration/ingest/check.py`만
쓰는 추가 override입니다. 그 실습이 생성한 collector 조각을 ClickStack 자신의
collector에 올리고(`CUSTOM_OTELCOL_CONFIG_FILE`), 입력 디렉터리를 `/ingest-verify/in`에
마운트합니다. `check.py`가 이것으로 `clickstack`을 재생성하고, `--restore`가 이것 없이
다시 재생성합니다. 켜 둔 채로 두지 마세요. 원래 쓰던 커스텀 설정을 대체합니다.

**실제 원본 클러스터에 가까운 것은 `elastic-secure`입니다.** `elastic`
프로파일은 `xpack.security.enabled=false`이고, 그래서
`labs/elastic-migration/data/`의 모든 도구가 한동안 **인증 자체가 없는 상태로**
동작했습니다. 인증 경로를 시험하려면 이쪽을 쓰세요.

```bash
docker compose --profile elastic-secure up -d
ES_URL=http://localhost:9201 ES_USER=elastic ES_PASSWORD=elastic-local-only \
    ./bin/seed_elasticsearch.py --recreate --docs 20000
```

이 프로파일은 HTTP TLS를 의도적으로 끕니다. 도구에 필요했던 것은 인증이고,
자체 서명 CA까지 얹으면 무엇을 하기 전에 컨테이너에서 인증서를 꺼내야 합니다.
CA 경로는 `ES_CA_CERT`가 담당하며 기본 설정 컨테이너로 따로 검증했습니다 --
`labs/elastic-migration/data/README.md`를 보세요.

**Elasticsearch 8.17.0**, 단일 노드, 보안 비활성
(`xpack.security.enabled=false`). localhost에 시딩한 합성 데이터만 있기
때문에만 괜찮은 설정입니다 -- 실제 데이터가 있는 클러스터에는 절대 이렇게
하지 마세요. 시딩된 매핑이 무엇을 시험하도록 설계됐는지는
`labs/elastic-migration/data/README.md`를 보세요.

**all-in-one 안의 ClickHouse를 쓰지 않고 두 번째를 두는 이유.**
마이그레이션은 ClickHouse Cloud에 도착하고, Cloud의 regular release 채널은
26.6 라인입니다. all-in-one 2.39.1은 26.8.7.19를 번들합니다. 목적지보다 **더
새로운** ClickHouse에서 검증하면 목적지에 아직 없는 기능을 증명할 수 있고,
그것이 마이그레이션 실습이 해서는 안 되는 유일한 실수입니다. 그래서 목적지를
Cloud와 같은 라인의 최신 공개 패치인 `26.6.8.7`로 고정했습니다. 같은 minor지만
같은 빌드는 아닙니다 -- Cloud 자체 빌드는 공개되지 않습니다.

이 목적지는 all-in-one과 달리 호스트에서 `default`(비밀번호 없음)로 접속을
허용합니다(`CLICKHOUSE_SKIP_USER_SETUP`). 그렇게 하지 않으면 최근 서버 이미지는
첫 기동에 무작위 비밀번호를 만들고, 그러면 문서에 적을 수 있는 명령이 없어집니다.
위와 같이 임시·localhost 전용 전제에서만 괜찮은 설정입니다.

다른 곳 -- 예를 들어 여러분의 Cloud 서비스 -- 로 보내려면 `.env.example`의
`CH_TARGET_*` 변수를 쓰세요. 마이그레이션 경로에 한해서만 `CH_*`를 덮어씁니다.

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

### End-to-end 검사

`bin/check.sh`는 대상이 닿는지만 말합니다. `bin/verify.sh`는 알려진 텔레메트리를
보내고 끝까지 따라갑니다.

```bash
./bin/verify.sh
```

로그 레코드 200개에 고유한 `verify.run_id`를 붙여 보낸 뒤, `otel_logs`에서 개수를
세고 HyperDX API로 검색합니다. 둘을 따로 확인하는 건 의도적입니다 — **SQL은
통과하는데 검색이 실패하면 수집이 아니라 로그 source 정의가 잘못된 것**이고, 이게
UI에서는 그냥 빈 화면으로만 보이는 실패입니다.

`HYPERDX_API_KEY`와 함께 `HYPERDX_INGESTION_KEY`가 필요합니다. 서로 다른 키이고,
ClickStack의 OTLP 리시버는 인증 없는 데이터를 거부합니다.

**Verified on:** ClickStack 2.39.1 (ClickHouse 26.8.7.19), telemetrygen v0.155.0,
2026-10-01. 로컬 스택에서 `bin/verify.sh`를 실행했고 세 단계가 모두 통과했습니다
(로그 200건 전송, `otel_logs` 200행, HyperDX 검색 200행). 잘못된 `authorization`
헤더로 보낸 네거티브 컨트롤은 리시버가 거부(`Unauthenticated`)했고, 해당
`verify.run_id`의 행은 0건이었습니다. telemetrygen은 초당 로그 약 1건을 보내므로
실행에는 4분 안팎이 걸립니다.

### 비밀값

`.env`는 gitignore되고, 자리표시자만 든 `.env.example`은 추적됩니다. 여기의
어떤 것도 자격증명을 추적되는 파일에 쓰지 않습니다. 비밀번호를 기록이 남을 수
있는 곳 — 채팅 로그, 터미널 기록, 티켓 — 에 붙여넣은 적이 있다면, 비공개로
남았다고 가정하지 말고 교체하세요.
