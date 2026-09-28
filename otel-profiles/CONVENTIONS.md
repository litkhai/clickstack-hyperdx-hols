# Profile conventions

[English](#english) | [한국어](#한국어)

## English

Rules every profile under `profiles/` follows. They exist because of how ClickStack loads
configuration, not as style preferences — breaking one of the first three silently disables
ClickStack's own ingestion.

### 1. How ClickStack loads a custom config

In standalone mode the container runs the collector with several `--config` flags
(`docker/otel-collector/entrypoint.sh` in `hyperdxio/hyperdx`):

```
/otelcontribcol --config config.yaml --config standalone-config.yaml --config $CUSTOM_OTELCOL_CONFIG_FILE
```

That is the collector's native `confmap` merge: **maps merge deeply, lists are replaced by
the last source.** The `confmap.enableMergeAppendOption` gate makes lists append, but the
upstream README says it "will **not** become the default", so no profile may depend on it.

### 2. Named pipelines only

A profile must never define a bare `metrics:`, `logs:` or `traces:` pipeline. `receivers`,
`processors` and `exporters` inside a pipeline are **lists**, so a bare pipeline key replaces
ClickStack's own and drops OTLP ingestion for that signal.

```yaml
# WRONG — replaces ClickStack's metrics pipeline
service:
  pipelines:
    metrics:
      receivers: [hostmetrics/linux-host]

# RIGHT — a new map key, merges alongside the base pipelines
service:
  pipelines:
    metrics/linux-host:
      receivers: [hostmetrics/linux-host]
```

Pipeline names are `<signal>/<profile-directory-name>`. Two profiles can therefore never
collide, which is what makes them composable.

### 3. Never redefine a base component

Reference these by name; they are already configured by ClickStack:

| Kind | Name | Notes |
|---|---|---|
| exporter | `clickhouse` | the only exporter a Tier A profile should use |
| processor | `memory_limiter` | must be first in every pipeline |
| processor | `batch` | must be last |
| processor | `transform` | HyperDX log shaping — include it in `logs/*` pipelines |
| receiver | `otlp/hyperdx` | base OTLP ingestion; Tier B profiles target it |

Leaf-merging into `memory_limiter` does not work: the processor silently prefers `limit_mib`
over `limit_percentage` when both are set, and the base config sets one of them. To change a
base processor, define a new name (`memory_limiter/custom`) and reference that instead.

Every component a profile does define is suffixed with the profile name —
`hostmetrics/linux-host`, `resource/gpu-nvidia` — for the same reason.

### 4. Tier A and Tier B

ClickStack does not ship `otelcol-contrib`. It is an OCB build (`otelcol-hyperdx`,
`packages/otel-collector/builder-config.yaml`) whose receivers are only:

`nop` `otlp` `datadog` `dockerstats` `filelog` `fluentforward` `hostmetrics` `k8scluster`
`kubeletstats` `prometheus` `statsd`

| Tier | File a profile ships | Runs where |
|---|---|---|
| **A** | `custom.config.yaml` | inside ClickStack, merged into its config |
| **B** | `sidecar.config.yaml` | a separate collector image, exporting OTLP to ClickStack |

A receiver not in the list above forces Tier B. Before adding a Tier B profile, check the
receiver's `metadata.yaml` for `distributions:` — `[contrib]` means the public
`otel/opentelemetry-collector-contrib` image has it, `[]` means it needs a custom OCB build
and the profile README must say so.

The `prometheus` receiver is the reason most hardware profiles stay in Tier A: scraping an
existing exporter needs no new receiver.

### 5. Normalise to `hw.*`, keep the original

Raw exporter names have nothing in common across machine classes, so a dashboard built on
them only ever works for one class. Profiles map hardware metrics onto the
[hardware semantic conventions](https://opentelemetry.io/docs/specs/semconv/hardware/)
(`hw.*`, Development stability as of semconv 1.44.0) using
`metricstransform` with `action: insert`, which keeps the original metric alongside the
normalised copy — no information is lost and existing exporter-specific queries still work.

Units must match the convention, so a mapping usually needs a scale:

| Convention | Instrument | Unit |
|---|---|---|
| `hw.power` | Gauge | `W` |
| `hw.energy` | Counter | `J` |
| `hw.errors` | Counter | `{error}` |
| `hw.status` | UpDownCounter | `1` |
| `hw.temperature` | Gauge | `Cel` |
| `hw.gpu.utilization` | Gauge | `1` (ratio, not percent) |
| `hw.gpu.memory.usage` / `.limit` | UpDownCounter | `By` |

`hw.id` is required on every `hw.*` metric and `hw.type` on `hw.errors` / `hw.status`. Set
them as **datapoint** attributes with `transform/<profile>`, not as resource attributes.

Do not invent a mapping. If a source metric has no convention counterpart, or its unit or
meaning is uncertain, leave it under its original name and record why in the profile's
`metrics.md`. A wrong mapping is worse than none.

### 6. Machine class goes in resource attributes

The `otel_metrics_*` and `otel_logs` tables are shared by every profile, so the class has to
be queryable rather than implied by a table name. Each profile sets, via `resource/<profile>`:

| Attribute | Values |
|---|---|
| `deploy.platform` | `host` · `gpu` · `baremetal` · `vm` · `container` · `k8s` |

`host.name` and `host.id` come from `resourcedetection/common` in `common/resource.yaml`.
Keep resource attributes low-cardinality: they are repeated on every row.

### 7. Secrets and paths

Credentials are referenced as `${env:NAME}` and never written into a tracked file. Every
profile ships a `.env.example` with empty values. Paths are relative or container paths —
the repository's hygiene check rejects a host home path in any non-Markdown file.

### 8. Verification

A profile's README may only carry a `Verified on …` line once its config has run end to end
against that class of hardware and the data was confirmed **by SQL** — `verify.sql` in the
profile directory, or `bin/verify.sh`. A HyperDX screenshot is not a verification claim.
Record three versions: ClickHouse, ClickStack/HyperDX, and — for Tier B — the sidecar
collector image tag.

### 9. Required files

| Tier A | Tier B | Purpose |
|---|---|---|
| `README.md` | `README.md` | bilingual, English first; prerequisites, tier, verification |
| `custom.config.yaml` | `sidecar.config.yaml` | the config fragment |
| `.env.example` | `.env.example` | endpoints and credentials, empty values |
| `metrics.md` | `metrics.md` | source metric to `hw.*` mapping, and what is deliberately unmapped |
| `verify.sql` | `verify.sql` | ingestion check |

`bin/lint.sh` enforces 2, 3 and 9.

---

## 한국어

`profiles/` 아래 모든 프로파일이 따르는 규칙입니다. 취향이 아니라 ClickStack이 설정을 읽는
방식 때문에 생긴 제약이며, 1~3번을 어기면 **ClickStack 자체 수집이 조용히 멈춥니다.**

### 1. ClickStack의 커스텀 설정 로딩 방식

standalone 모드에서 컨테이너는 `--config`를 여러 개 붙여 컬렉터를 실행합니다
(`hyperdxio/hyperdx`의 `docker/otel-collector/entrypoint.sh`):

```
/otelcontribcol --config config.yaml --config standalone-config.yaml --config $CUSTOM_OTELCOL_CONFIG_FILE
```

컬렉터 기본 `confmap` 병합이므로 **맵은 깊게 병합되지만 리스트는 마지막 소스로 교체됩니다.**
`confmap.enableMergeAppendOption` 게이트가 리스트를 append 해주지만 업스트림 README가
"기본값이 되지 **않는다**"고 명시하므로 프로파일이 이에 의존해서는 안 됩니다.

### 2. 파이프라인에는 반드시 이름을 붙인다

`metrics:`, `logs:`, `traces:`를 그대로 정의하면 안 됩니다. 파이프라인 안의 `receivers`,
`processors`, `exporters`는 **리스트**라서, 이름 없는 키는 ClickStack의 파이프라인을
교체하고 해당 신호의 OTLP 수집을 없애버립니다.

```yaml
# 잘못됨 — ClickStack의 metrics 파이프라인을 교체함
service:
  pipelines:
    metrics:
      receivers: [hostmetrics/linux-host]

# 올바름 — 새 맵 키이므로 기존 파이프라인과 나란히 병합됨
service:
  pipelines:
    metrics/linux-host:
      receivers: [hostmetrics/linux-host]
```

이름 규칙은 `<신호>/<프로파일 디렉토리명>`입니다. 그래서 프로파일끼리 절대 충돌하지 않고,
이것이 조합 가능한 이유입니다.

### 3. 베이스 컴포넌트를 재정의하지 않는다

아래는 ClickStack이 이미 설정해둔 것이므로 이름으로만 참조합니다.

| 종류 | 이름 | 비고 |
|---|---|---|
| exporter | `clickhouse` | Tier A 프로파일이 쓸 유일한 익스포터 |
| processor | `memory_limiter` | 모든 파이프라인의 맨 앞 |
| processor | `batch` | 맨 뒤 |
| processor | `transform` | HyperDX 로그 정형화 — `logs/*` 파이프라인에 포함 |
| receiver | `otlp/hyperdx` | 베이스 OTLP 수집. Tier B가 여기로 보냄 |

`memory_limiter`에 부분 병합은 통하지 않습니다. `limit_mib`와 `limit_percentage`가 동시에
설정되면 프로세서가 조용히 `limit_mib`를 택하고, 베이스가 그중 하나를 이미 설정합니다.
베이스 프로세서를 바꾸려면 새 이름(`memory_limiter/custom`)을 만들어 참조하세요.

프로파일이 정의하는 모든 컴포넌트에는 같은 이유로 프로파일 이름을 접미사로 붙입니다 —
`hostmetrics/linux-host`, `resource/gpu-nvidia`.

### 4. Tier A와 Tier B

ClickStack은 `otelcol-contrib`를 쓰지 않습니다. OCB 빌드(`otelcol-hyperdx`,
`packages/otel-collector/builder-config.yaml`)이고 포함된 receiver는 다음뿐입니다.

`nop` `otlp` `datadog` `dockerstats` `filelog` `fluentforward` `hostmetrics` `k8scluster`
`kubeletstats` `prometheus` `statsd`

| Tier | 프로파일이 제공하는 파일 | 실행 위치 |
|---|---|---|
| **A** | `custom.config.yaml` | ClickStack 내부, 설정에 병합됨 |
| **B** | `sidecar.config.yaml` | 별도 컬렉터 이미지, OTLP로 ClickStack에 전달 |

위 목록에 없는 receiver는 무조건 Tier B입니다. Tier B 프로파일을 추가하기 전에 해당
receiver의 `metadata.yaml`에서 `distributions:`를 확인하세요. `[contrib]`이면 공개
`otel/opentelemetry-collector-contrib` 이미지에 들어있고, `[]`이면 OCB 커스텀 빌드가
필요하므로 프로파일 README에 반드시 명시해야 합니다.

대부분의 하드웨어 프로파일이 Tier A에 머무를 수 있는 이유는 `prometheus` receiver입니다.
이미 있는 exporter를 스크레이프하는 데는 새 receiver가 필요 없습니다.

### 5. `hw.*`로 정규화하되 원본을 남긴다

exporter 원본 이름은 장비군 사이에 공통점이 없어서, 그 위에 만든 대시보드는 한 장비군에만
동작합니다. 프로파일은 하드웨어 메트릭을
[하드웨어 semantic convention](https://opentelemetry.io/docs/specs/semconv/hardware/)
(`hw.*`, semconv 1.44.0 기준 Development)으로 매핑하며, `metricstransform`의
`action: insert`를 써서 **원본 메트릭을 그대로 남깁니다.** 정보 손실이 없고 기존
exporter 기준 쿼리도 계속 동작합니다.

단위가 규약과 맞아야 하므로 매핑에는 보통 스케일 변환이 따릅니다.

| 규약 | 계측기 | 단위 |
|---|---|---|
| `hw.power` | Gauge | `W` |
| `hw.energy` | Counter | `J` |
| `hw.errors` | Counter | `{error}` |
| `hw.status` | UpDownCounter | `1` |
| `hw.temperature` | Gauge | `Cel` |
| `hw.gpu.utilization` | Gauge | `1` (비율, 퍼센트 아님) |
| `hw.gpu.memory.usage` / `.limit` | UpDownCounter | `By` |

모든 `hw.*` 메트릭에 `hw.id`가 필수이고 `hw.errors` / `hw.status`에는 `hw.type`이
필수입니다. 이들은 리소스 속성이 아니라 `transform/<profile>`로 **데이터포인트 속성**으로
설정합니다.

매핑을 발명하지 마세요. 대응하는 규약이 없거나 단위·의미가 불확실하면 원본 이름 그대로
두고 그 이유를 프로파일의 `metrics.md`에 기록합니다. 틀린 매핑은 매핑 없는 것보다 나쁩니다.

### 6. 장비군은 리소스 속성에 넣는다

`otel_metrics_*`와 `otel_logs` 테이블을 모든 프로파일이 공유하므로, 장비군은 테이블 이름으로
암시하는 게 아니라 쿼리 가능해야 합니다. 각 프로파일이 `resource/<profile>`로 설정합니다.

| 속성 | 값 |
|---|---|
| `deploy.platform` | `host` · `gpu` · `baremetal` · `vm` · `container` · `k8s` |

`host.name`과 `host.id`는 `common/resource.yaml`의 `resourcedetection/common`이 채웁니다.
리소스 속성은 모든 행에 반복되므로 카디널리티를 낮게 유지하세요.

### 7. 비밀값과 경로

자격증명은 `${env:NAME}`으로만 참조하고 추적되는 파일에 쓰지 않습니다. 모든 프로파일은 값이
빈 `.env.example`를 함께 제공합니다. 경로는 상대 경로나 컨테이너 경로만 씁니다 — 저장소
hygiene 검사가 Markdown이 아닌 파일의 호스트 홈 경로를 거부합니다.

### 8. 검증

프로파일 README의 `Verified on …` 줄은 해당 장비군에서 설정을 end-to-end로 실행하고
**SQL로** 데이터를 확인한 뒤에만 쓸 수 있습니다 — 프로파일 디렉토리의 `verify.sql` 또는
`bin/verify.sh`. HyperDX 스크린샷은 검증이 아닙니다. 세 버전을 기록하세요: ClickHouse,
ClickStack/HyperDX, 그리고 Tier B라면 사이드카 컬렉터 이미지 태그.

### 9. 필수 파일

| Tier A | Tier B | 용도 |
|---|---|---|
| `README.md` | `README.md` | 이중 언어(영어 먼저), 전제조건·Tier·검증 |
| `custom.config.yaml` | `sidecar.config.yaml` | 설정 조각 |
| `.env.example` | `.env.example` | 엔드포인트·자격증명, 값은 비움 |
| `metrics.md` | `metrics.md` | 원본 메트릭 → `hw.*` 매핑, 의도적으로 매핑하지 않은 것 |
| `verify.sql` | `verify.sql` | 수집 확인 |

`bin/lint.sh`가 2·3·9번을 검사합니다.
