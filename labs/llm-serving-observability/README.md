# Self-hosted LLM serving observed with ClickStack

[English](#english) | [한국어](#한국어)

## English

> **Related notes** (Korean): [자가 호스팅 LLM 서빙 관측: OTel Collector에서 ClickStack까지](https://clickhouse.litkhai.dev/articles/clickstack/clickstack-llm-serving-observability/)

The OTel Collector configuration, the `nvidia-smi` → OTLP pusher and the HyperDX dashboard (v3, 32
tiles) that observed a vLLM coding model behind a LiteLLM gateway on one NVIDIA GB10 box, made runnable
on a laptop with no GPU. vLLM and `nvidia-smi` are replaced by small stand-ins; the Collector
configuration, the pusher and the dashboard are the ones from the article. Six scenarios are checked
with SQL and the HyperDX API, each with a positive and a negative check.

The numbers in the article (GPU fields, data volume, decode impact) were measured on the GB10 box on
2026-09-10/11 and are not reproduced here: a mock has no GPU and no decode speed. This lab checks the
configuration and the behaviour that does not depend on the hardware.

### What it builds

| Piece | Where | What |
|---|---|---|
| ClickStack all-in-one `2.42.0` | `docker-compose.yml` | ClickHouse and HyperDX. The Collector writes to its ClickHouse as the image's published dev user `worker` (the image refuses `default` over the network) |
| OTel Collector contrib `0.160.0` | `otelcol/config.yaml` | the article's configuration: `prometheus` scrape of vLLM with a keep filter, `host_metrics` with the utilization gauges named, snap's loop/squashfs excluded, `otlp` on all three pipelines, the ClickHouse exporter with `create_schema` into database `llm_obs`. Only the exporter's endpoint, user and password come from the environment |
| Mock vLLM | `vllm-mock/mock.py` | Prometheus `/metrics` with vLLM's metric names (two families the filter must drop) and an OpenAI-compatible `/v1/chat/completions` that moves the counters |
| LiteLLM `v1.108.0` | `litellm/config.yaml` | the gateway, OTel callback with traces, events and metrics, and the attribute allowlist. `config-default.yaml` is the same without the allowlist (S4 negative). A random master key is generated into `.run/` by `bin/up.sh` |
| GPU pusher | `gpu-push/gpu_push.py` | the article's pusher, standard library only. Here it reads `gpu-push/fake-nvidia-smi`; on a GPU box drop the `PATH` line and add `gpus: all` |
| Dashboard | `dashboard/llm-serving-v3.json` | the article's dashboard: 8 containers, 32 tiles, sources named `LLM Trace`, `LLM Metrics`, `LLM Log` |
| Setup and checks | `bin/up.sh`, `bin/setup_hyperdx.py`, `bin/check.py` | stack up, a localhost HyperDX test user, the three sources, the dashboard import, and the scenario checks |

### Findings that shaped the lab

- **The image's default sources cannot be reused.** In 2.42.0 its Logs and Traces sources read the
  `default` database and ClickStack's rollup tables (`otel_logs_kv_rollup_15m`, `otel_traces_kv_rollup_15m`).
  The contrib exporter fills `llm_obs` and creates no rollup tables. `setup_hyperdx.py` copies the
  default sources' shape, points it at `llm_obs` and drops the rollup setting. Both databases' `otel_logs`
  have a single `Timestamp` column in this image.
- **Dashboard import matches sources by name, case-insensitively.** HyperDX's import page
  (`DBDashboardImportPage.tsx`, 2.42.0, same logic in 2.38.0) maps each tile's source name to a source of
  the same name and requires a manual mapping for any that does not match. `setup_hyperdx.py` does the
  same mapping through the API and stops on an unmatched name.
- **LiteLLM 1.108.0 refuses to start without a master key** ("no master key is set, so every request would
  be accepted without authentication"). `bin/up.sh` generates one into `.run/`.
- **The OTLP HTTP body limit defaults to 20 MiB** in Collector 0.160.0 (`confighttp`), not 4 MiB.
  The configuration keeps the article's 32 MiB.

### Run it

```bash
cd labs/llm-serving-observability
bin/up.sh            # under a minute once the images are pulled
python3 bin/check.py # about 4 minutes
docker compose down -v && rm -rf .run
```

HyperDX is at `http://localhost:38080`; the test user is in `.run/hyperdx-user`. Ports: 38080 (UI),
38000 (HyperDX API), 38123 (ClickHouse HTTP), 34000 (LiteLLM), 38888 (Collector metrics), all on
127.0.0.1; S2 briefly uses 38318.

### The checks

| | Check | Negative check |
|---|---|---|
| S1 config | `otelcol/config.yaml` passes `validate` on Collector 0.160.0 | `exclude_devices` under `disk` fails with `'diskscraper.Config' has invalid keys: exclude_devices` |
| S2 GPU push | the five `gpu.*` gauges of service `gb10-gpu` are in `llm_obs.otel_metrics_gauge` | a Collector whose metrics pipeline lacks `otlp` answers `/v1/metrics` with 404 (200 with it) |
| S3 vLLM | `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_hits_total`, `vllm:time_to_first_token_seconds` arrive with the colon; the utilization gauges named in `host_metrics` arrive | `vllm:estimated_read_bytes_per_gpu`, `vllm:cache_config_info` and an underscore spelling never arrive; samples scraped > samples after relabel |
| S4 gateway | with the allowlist, 10 requests leave 2 series of `gen_ai.client.token.usage` (input, output) | without it, the same 10 requests leave at least 10 (one attribute set per request and token type) |
| S5 traces | a marker sent through LiteLLM is in `otel_traces` (`gen_ai.input.messages`) and `otel_logs` | a marker sent straight to vLLM is in neither, while `vllm:request_success_total` still moves |
| S6 HyperDX | the dashboard's 32 tiles each map to one of the three `llm_obs` sources; the prefix-cache ratio computes from the same rows | the image's default Logs source reads `default`, which has no LiteLLM spans, and its rollup table does not exist in `llm_obs` |

> Verified on ClickStack all-in-one `2.42.0` (HyperDX 2.42.0, as reported by its `/health`;
> ClickHouse 26.8.34.13), OTel Collector contrib `0.160.0`, LiteLLM `1.108.0`, Python 3.12.16 —
> local Docker Desktop (macOS arm64), 2026-11-05: from an empty volume, `bin/up.sh` then `bin/check.py`,
> 14 of 14 checks passed, the negative checks included (S1 fault: `exclude_devices`; S4: 2 series with
> the allowlist, 20 without, after 10 requests each).

### On a real box

Point `CH_ENDPOINT` / `CH_USER` / `CH_PASSWORD` at your ClickHouse (for ClickHouse Cloud,
`tcp://<service>.clickhouse.cloud:9440?secure=true` and `default`), scrape your vLLM instead of the mock
(`static_configs.targets`), run the pusher with `--gpus all` and without the fake `nvidia-smi`, and pin
LiteLLM to a version you have checked. The GB10 notes behind this lab used tags (`main-stable`,
`latest`), which is why their versions are unknown.

## 한국어

> **관련 글**: [자가 호스팅 LLM 서빙 관측: OTel Collector에서 ClickStack까지](https://clickhouse.litkhai.dev/articles/clickstack/clickstack-llm-serving-observability/)

NVIDIA GB10 장비 한 대에서 LiteLLM 게이트웨이 뒤의 vLLM 코딩 모델을 관측할 때 쓴 OTel Collector 설정,
`nvidia-smi` → OTLP 푸셔, HyperDX 대시보드(v3, 타일 32개)를 GPU 없는 노트북에서 돌릴 수 있게 만든
실습입니다. vLLM과 `nvidia-smi`는 작은 대역으로 바꿨고, Collector 설정, 푸셔, 대시보드는 글의 것을
그대로 씁니다. 시나리오 여섯 개를 SQL과 HyperDX API로 확인하고, 시나리오마다 양성·음성 검사가 하나씩
있습니다.

글의 수치(GPU 필드, 데이터량, 디코드 영향)는 2026-09-10~11 GB10 장비에서 잰 것이고 여기서 재현하지
않습니다. 대역에는 GPU도 디코드 속도도 없습니다. 이 실습은 하드웨어와 무관한 설정과 동작을 확인합니다.

### 구성

| 구성 요소 | 위치 | 내용 |
|---|---|---|
| ClickStack all-in-one `2.42.0` | `docker-compose.yml` | ClickHouse와 HyperDX. Collector는 이미지가 공개한 개발용 사용자 `worker`로 씁니다(이 이미지는 네트워크로 들어온 `default`를 거부합니다) |
| OTel Collector contrib `0.160.0` | `otelcol/config.yaml` | 글의 설정: keep 필터를 단 vLLM `prometheus` 스크레이프, 사용률 게이지를 이름으로 켠 `host_metrics`, snap의 loop/squashfs 제외, 세 파이프라인 모두의 `otlp`, `create_schema`로 `llm_obs`에 쓰는 ClickHouse exporter. exporter의 주소·사용자·비밀번호만 환경 변수에서 받습니다 |
| vLLM 대역 | `vllm-mock/mock.py` | vLLM 메트릭 이름을 쓰는 Prometheus `/metrics`(필터가 버려야 할 계열 둘 포함)와 카운터를 움직이는 OpenAI 호환 `/v1/chat/completions` |
| LiteLLM `v1.108.0` | `litellm/config.yaml` | 게이트웨이. trace·이벤트·메트릭을 보내는 OTel 콜백과 속성 허용 목록. `config-default.yaml`은 허용 목록만 뺀 것(S4 음성 검사). 마스터 키는 `bin/up.sh`가 `.run/`에 무작위로 만듭니다 |
| GPU 푸셔 | `gpu-push/gpu_push.py` | 글의 푸셔, 표준 라이브러리만 씀. 여기서는 `gpu-push/fake-nvidia-smi`를 읽고, GPU 장비에서는 `PATH` 줄을 빼고 `gpus: all`을 더합니다 |
| 대시보드 | `dashboard/llm-serving-v3.json` | 글의 대시보드: 컨테이너 8개, 타일 32개, 소스 이름 `LLM Trace`, `LLM Metrics`, `LLM Log` |
| 준비와 검사 | `bin/up.sh`, `bin/setup_hyperdx.py`, `bin/check.py` | 스택 기동, localhost HyperDX 테스트 사용자, 소스 셋, 대시보드 가져오기, 시나리오 검사 |

### 실습 설계를 정한 발견

- **이미지의 기본 소스는 다시 쓸 수 없습니다.** 2.42.0의 Logs·Traces 기본 소스는 `default` 데이터베이스와
  ClickStack 롤업 테이블(`otel_logs_kv_rollup_15m`, `otel_traces_kv_rollup_15m`)을 읽습니다. contrib
  exporter는 `llm_obs`를 채우고 롤업 테이블을 만들지 않습니다. `setup_hyperdx.py`는 기본 소스의 모양을
  복사해 `llm_obs`를 가리키게 하고 롤업 설정을 뺍니다. 이 이미지에서는 두 데이터베이스의 `otel_logs` 모두
  시간 컬럼이 `Timestamp` 하나입니다.
- **대시보드 가져오기는 소스를 이름으로, 대소문자를 무시하고 짝짓습니다.** HyperDX 가져오기 화면
  (`DBDashboardImportPage.tsx`, 2.42.0, 2.38.0도 같은 논리)은 타일의 소스 이름과 같은 이름의 소스를 고르고,
  맞는 것이 없으면 손으로 고르게 합니다. `setup_hyperdx.py`는 같은 짝짓기를 API로 하고, 맞지 않는 이름이
  있으면 멈춥니다.
- **LiteLLM 1.108.0은 마스터 키 없이는 기동하지 않습니다**("no master key is set, so every request would
  be accepted without authentication"). `bin/up.sh`가 `.run/`에 하나 만듭니다.
- **OTLP HTTP 본문 한도의 기본값은 Collector 0.160.0(`confighttp`)에서 20 MiB입니다.** 4 MiB가
  아닙니다. 설정은 글의 32 MiB를 그대로 둡니다.

### 실행

```bash
cd labs/llm-serving-observability
bin/up.sh            # 이미지를 받아 둔 뒤라면 1분이 안 걸림
python3 bin/check.py # 4분쯤
docker compose down -v && rm -rf .run
```

HyperDX는 `http://localhost:38080`이고 테스트 사용자는 `.run/hyperdx-user`에 있습니다. 포트는 38080(UI),
38000(HyperDX API), 38123(ClickHouse HTTP), 34000(LiteLLM), 38888(Collector 메트릭)이고 모두
127.0.0.1에만 열립니다. S2는 잠깐 38318을 씁니다.

### 검증 항목

| | 검증 | 부정 검증 |
|---|---|---|
| S1 설정 | `otelcol/config.yaml`이 Collector 0.160.0의 `validate`를 통과 | `disk` 아래 `exclude_devices`는 `'diskscraper.Config' has invalid keys: exclude_devices`로 실패 |
| S2 GPU 푸시 | 서비스 `gb10-gpu`의 `gpu.*` 게이지 다섯 개가 `llm_obs.otel_metrics_gauge`에 있음 | metrics 파이프라인에 `otlp`가 없는 Collector는 `/v1/metrics`에 404(있으면 200) |
| S3 vLLM | `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_hits_total`, `vllm:time_to_first_token_seconds`가 콜론 그대로 들어옴. `host_metrics`에서 이름으로 켠 사용률 게이지가 들어옴 | `vllm:estimated_read_bytes_per_gpu`, `vllm:cache_config_info`, 밑줄 표기는 들어오지 않음. 스크레이프한 샘플 > relabel 뒤 샘플 |
| S4 게이트웨이 | 허용 목록이 있으면 요청 10개 뒤 `gen_ai.client.token.usage` 시리즈가 2개(input, output) | 없으면 같은 요청 10개가 10개 이상을 남김(요청과 토큰 종류마다 속성 조합 하나) |
| S5 trace | LiteLLM으로 보낸 표식이 `otel_traces`(`gen_ai.input.messages`)와 `otel_logs`에 있음 | vLLM에 바로 보낸 표식은 둘 다에 없고, `vllm:request_success_total`은 그래도 움직임 |
| S6 HyperDX | 대시보드 타일 32개가 모두 `llm_obs` 소스 셋 중 하나에 연결됨. prefix cache 비율이 같은 행에서 계산됨 | 이미지의 기본 Logs 소스는 `default`를 읽고, 거기에는 LiteLLM 스팬이 없으며, 그 롤업 테이블은 `llm_obs`에 없음 |

> 검증: ClickStack all-in-one `2.42.0`(HyperDX 2.42.0, `/health` 보고값, ClickHouse 26.8.34.13),
> OTel Collector contrib `0.160.0`, LiteLLM `1.108.0`, Python 3.12.16. 로컬 Docker Desktop(macOS arm64), 2026-11-05:
> 빈 볼륨에서 `bin/up.sh`, 이어서 `bin/check.py` 14개 모두 통과(부정 검증 포함. S1 고장: `exclude_devices`, S4: 요청 10개씩
> 보낸 뒤 허용 목록이 있으면 시리즈 2개, 없으면 20개).

### 실제 장비에서

`CH_ENDPOINT` / `CH_USER` / `CH_PASSWORD`를 쓰는 ClickHouse로 바꾸고(ClickHouse Cloud라면
`tcp://<service>.clickhouse.cloud:9440?secure=true`와 `default`), 대역 대신 실제 vLLM을 스크레이프하고
(`static_configs.targets`), 푸셔는 가짜 `nvidia-smi` 없이 `--gpus all`로 돌리고, LiteLLM은 확인한 버전으로
고정합니다. 이 실습의 바탕이 된 GB10 노트는 태그(`main-stable`, `latest`)로 돌려서 버전이 남아 있지 않습니다.
