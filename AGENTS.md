# AGENTS.md

Instructions for coding agents working in this repository.

This repository was split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols). Its
[AGENTS.md](https://github.com/litkhai/clickhouse-hols/blob/main/AGENTS.md) still applies here —
bilingual READMEs (English first, `## English` / `## 한국어`), no links to labs
that do not exist yet, and the **Verification claims** rule: only write
*"Verified on …"* when the scripts actually ran end to end against that version.

Differences from the core repository:

- No Pages site and no `site` CI job (decision D7). The root README tables are
  documentation only, not a site index.
- Enable the guard once per clone: `git config core.hooksPath .githooks`.

## Rules for this repository

- Record **both** versions in every verification line: ClickHouse and ClickStack/HyperDX
  (and the OTel Collector image when a lab ships one).
- Verify with SQL, not the UI: row counts in `otel_traces` / `otel_logs`, spans per service.
  A UI screenshot is not a verification claim.
- Roadmap slots in the README are code spans, not links — a link to a lab that does not exist
  yet breaks the `links` job.
- There is no Pages site yet (decision D7). Turn one on before the repository reaches about
  ten labs; `build_site.py` in clickhouse-hols is the starting point.

## Where things came from

Paths were renamed by `git filter-repo`, so `git log --follow` works across the
split. The original locations:

| In clickhouse-hols | Here |
|---|---|
| `chc/tool/ch2otel/` | `labs/ch2otel/` |
| `workshop/o11y-vector-ai/` | `workshops/o11y-vector-ai/` |
| `workshop/observability-waf/` | `workshops/observability-waf/` |

## Tracking work

Planned work, re-verification and follow-ups are **GitHub issues**; every change
lands through a **pull request** that references its issue (`Closes #N`).
`STATUS.md` is a snapshot of the current state and links to the open issues
instead of keeping its own to-do list. When you find something to do that you
are not doing now, open an issue rather than writing it into a README or
`STATUS.md`. Labels: `re-verify` (changed but not re-run), `enhancement`,
`docs`, `ops`, `security`.

한국어: 해야 할 일은 GitHub 이슈로, 변경은 이슈를 참조하는 PR로 관리합니다. `STATUS.md`는 열린 이슈를 링크합니다.
