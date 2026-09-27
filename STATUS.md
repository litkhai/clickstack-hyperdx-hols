# STATUS.md

**As of 2026-09-27** — split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10) with history.

## CI

`checks`: `links`, `syntax`, `shellcheck` (advisory), `secrets` (gitleaks), `hygiene` — green.
GitHub secret scanning and push protection are on.

## Inventory

3 labs in the README tables; 2 single-language: `workshops/o11y-vector-ai`, `workshops/observability-waf`.

## Re-verification notes

Not re-run; update a README's verification line only after a real end-to-end run.

| What | Note |
|------|------|
| `workshops/*` | Single-language (translation backlog). |
| Roadmap slots in README | Planned, not written. Needs a ClickStack instance (`_base/` first). |
