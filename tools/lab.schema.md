<!-- Generated from khai-workbench domains/clickhouse/tools/lab.schema.md @e1e87f4 — edit there, not here (sync.py). -->
# lab.yaml — the lab contract

[English](#english) | [한국어](#한국어)

## English

Read by `hol` (the runner) and `labs_json.py` (the site export), both in this directory. Lab
repositories carry generated copies under `tools/`, written by `sync.py`: `lab.schema.md` and
`labs_json.py` everywhere, `hol` only where the repository already has one (clickhouse-hols).
A lab opts in with a flat `lab.yaml` next to its README: `key: value` per line, `#` comments,
lists as `[a, b]`. No nesting — both tools read it without PyYAML. A `#` always starts a
comment, so a value cannot contain one; surrounding double quotes are stripped.

### Runner keys

| Key | Meaning | Used by `hol` |
|---|---|---|
| `target` | `oss` (local container) — other targets are not run by `hol` yet | `list` column |
| `tier` | `T0` = SQL only, one ClickHouse server, self-generated data — the only tier `smoke` runs. `T1` = also needs another local service (listed in `services`); `hol` does not start it, so a T1 lab is run by hand with that service up (first: `local/releases/25.8`, MinIO, 2026-09-30) | `list --tier` filter, `smoke` matrix |
| `clickhouse` | image tag: `clickhouse/clickhouse-server:<this>` | `up`, `run` |
| `services` | `[clickhouse]` for T0; T1 adds the other service, e.g. `[clickhouse, minio]` | informational |
| `verified_on` | the build the SQL last ran against end to end | `list` column; changed only by a real run (core "Verification claims") |

`run` = `up`, then every `NN-*.sql` in the lab directory in order through `clickhouse-client`,
non-zero on the first failure. The container is named from the lab path and removed by `down`.

### Publishing keys

Optional, read by `labs_json.py`. It writes `docs/labs.json` with only the labs that set
`web: true`; the notes site fetches that file from `main` of each lab repository. Which labs
publish is the owner's choice, lab by lab. A `lab.yaml` may hold publishing keys alone: `hol`
then lists the lab but cannot run it (no `clickhouse` key).

| Key | Meaning | Required when `web: true` |
|---|---|---|
| `web` | `true` publishes the lab; `false` or absent does not | — |
| `title_ko` | card title, Korean | yes |
| `summary_ko` | one-line summary, Korean | yes |
| `category` | one of `cloud`, `feature`, `case-study`, `core-architecture`, `third-party`, `competition`, `customer-story` — the site's article categories | yes |
| `title_en`, `summary_en` | English title and summary | no |

Each published entry carries `path`, the publishing keys that are set, the runner keys
`target` · `tier` · `clickhouse` · `verified_on` that are set, `readme_ko` / `readme_en` (the
`## 한국어` / `## English` halves of the lab's `README.md` on GitHub — one bilingual README per
lab, not a separate `README.ko.md`) and `pages` (only from a Pages builder that has a page for
the lab). `generated` changes only when the published list changes, so a re-run is a no-op.
`labs_json.py` writes nothing and fails on a `web` value other than `true`/`false`, an unknown
`category`, or a published lab without a required key — the same entries the site would reject.

```bash
tools/labs_json.py            # write docs/labs.json
tools/labs_json.py --check    # fail if docs/labs.json is out of date
```

Workshop profiles that include labs follow this contract; they do not define their own.

## 한국어

`hol`(러너)과 `labs_json.py`(사이트 내보내기)가 읽는다. 둘 다 이 디렉터리가 정본이다. 실습
저장소는 `sync.py`가 만든 사본을 `tools/`에 둔다. `lab.schema.md`·`labs_json.py`는 모든
저장소에, `hol`은 이미 사본이 있는 저장소(clickhouse-hols)에만 복사한다.
실습은 README 옆의 평평한 `lab.yaml`로 참여한다. 한 줄에 `key: value`, `#` 주석, 목록은
`[a, b]`. 중첩 없음 — 두 도구 모두 PyYAML 없이 읽는다. `#`은 항상 주석의 시작이라 값에
넣을 수 없고, 값을 감싼 큰따옴표는 벗겨진다.

### 러너 키

| 키 | 뜻 | `hol`의 사용 |
|---|---|---|
| `target` | `oss`(로컬 컨테이너). 다른 대상은 아직 `hol`이 실행하지 않음 | `list` 열 |
| `tier` | `T0` = SQL만, ClickHouse 서버 하나, 스스로 만든 데이터 — `smoke`가 실행하는 유일한 tier. `T1` = 다른 로컬 서비스도 필요(`services`에 적음). `hol`은 그 서비스를 띄우지 않으므로 T1 실습은 서비스를 올린 채 손으로 실행(첫 사례: `local/releases/25.8`, MinIO, 2026-09-30) | `list --tier` 필터, `smoke` 행렬 |
| `clickhouse` | 이미지 태그: `clickhouse/clickhouse-server:<값>` | `up`, `run` |
| `services` | T0는 `[clickhouse]`, T1은 다른 서비스를 더함(예: `[clickhouse, minio]`) | 참고용 |
| `verified_on` | SQL이 마지막으로 처음부터 끝까지 실행된 빌드 | `list` 열. 실제 실행으로만 바꾼다(core "Verification claims") |

`run` = `up` 뒤 실습 디렉터리의 `NN-*.sql`을 순서대로 `clickhouse-client`로 실행, 첫 실패에서
0이 아닌 값으로 끝난다. 컨테이너 이름은 실습 경로에서 만들고 `down`이 지운다.

### 게시 키

선택 키. `labs_json.py`가 읽어 `web: true`인 실습만 `docs/labs.json`에 쓴다. 노트 사이트는
각 실습 저장소 `main`에서 이 파일을 가져간다. 어느 실습을 게시할지는 소유자가 실습마다
정한다. `lab.yaml`에 게시 키만 있어도 된다. 그러면 `hol`은 그 실습을 목록에 보이지만
실행하지 못한다(`clickhouse` 키 없음).

| 키 | 뜻 | `web: true`일 때 필수 |
|---|---|---|
| `web` | `true`면 게시, `false`이거나 없으면 게시 안 함 | — |
| `title_ko` | 카드 제목(한국어) | 예 |
| `summary_ko` | 한 줄 요약(한국어) | 예 |
| `category` | `cloud`, `feature`, `case-study`, `core-architecture`, `third-party`, `competition`, `customer-story` 중 하나 — 사이트 글 분류와 같다 | 예 |
| `title_en`, `summary_en` | 영어 제목과 요약 | 아니오 |

게시된 항목에는 `path`, 설정된 게시 키, 설정된 러너 키 `target` · `tier` · `clickhouse` ·
`verified_on`, `readme_ko` / `readme_en`(GitHub에서 실습 `README.md`의 `## 한국어` /
`## English` 절 — 실습마다 이중 언어 README 하나이고 `README.ko.md`는 따로 없다), `pages`(그
실습의 페이지가 있는 Pages 빌더가 만들 때만)가 들어간다. `generated`는 게시 목록이 바뀔 때만
바뀌므로 다시 돌려도 그대로다. `web` 값이 `true`/`false`가 아니거나, `category`가 목록에
없거나, 게시할 실습에 필수 키가 없으면 `labs_json.py`는 아무것도 쓰지 않고 실패한다 — 사이트가
거부할 항목과 같다.

```bash
tools/labs_json.py            # docs/labs.json 쓰기
tools/labs_json.py --check    # docs/labs.json이 낡았으면 실패
```

실습을 포함하는 워크숍 프로필은 이 계약을 따르고 따로 정의하지 않는다.
