# STATUS.md

**As of 2026-09-29** — split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10) with history.

## CI

`checks` (on pull requests): `links`, `syntax`, `otel-profiles`, `secrets` (gitleaks), `hygiene` — green.
`pages` (on push to `main`): builds the site with `.github/scripts/build_site.py` and deploys it.
GitHub secret scanning and push protection are on.

## Inventory

3 labs in the README tables; 2 single-language: `workshops/o11y-vector-ai`, `workshops/observability-waf`.

7 OTel profiles in `otel-profiles/` — `linux-host`, `gpu-nvidia`, `baremetal-node`, `virt-kvm`,
`virt-vsphere`, `mysql`, `aws-rds-mysql`.
**None verified**: each needs its own class of hardware (or, for `aws-rds-mysql`, a real RDS
instance) to run against, so none carries a `Verified on …` line yet.

`_base/` has the local OSS stack and the Cloud target shape, plus `bin/check.sh` (readiness)
and `bin/verify.sh` (telemetry through to search). `verify.sh` has not been run end to end:
it needs the ingestion API key, which is only obtainable from the ClickStack UI.

Version pins, and why they differ from each other:

| Pin | Version | Why |
|---|---|---|
| `clickstack` | 2.39.1 (bundles ClickHouse 26.8.7.19) | the version `otel-profiles/` was written against |
| `elasticsearch` (`elastic` profile) | 8.17.0 | the version the migration in #24–#26 runs |
| `clickhouse-target` (`migration` profile) | 26.6.8.7 | newest public patch of the 26.6 line ClickHouse Cloud's regular release channel runs (a live Cloud service reports `26.6.1.2191`) |

`labs/elastic-migration/` verifies against the last two, not the ClickStack bundle: a
migration must not be verified against a newer ClickHouse than its destination.

`clickstack-config/` applies and destroys cleanly against the local OSS stack. **Not verified
against Cloud** — that needs org credentials, so no `Verified on …` line.

`labs/elastic-migration/data/` is written and verified end to end on Elasticsearch 8.17.0
into ClickHouse 26.6.8.7 -- `mapping_to_ddl.py`, `export.py`, `load.sh`, `parity_checks.py`.
`ingest/` is still a plan only (#21).

## Open work

Tracked as issues — [all open](https://github.com/litkhai/clickstack-hyperdx-hols/issues) · [needs a re-run](https://github.com/litkhai/clickstack-hyperdx-hols/issues?q=is%3Aopen+label%3Are-verify):

- [F0: _base/ shared ClickStack environment](https://github.com/litkhai/clickstack-hyperdx-hols/issues/1)
- [F1–F7: roadmap labs](https://github.com/litkhai/clickstack-hyperdx-hols/issues/2)
- [F8: shorten and translate the two workshops](https://github.com/litkhai/clickstack-hyperdx-hols/issues/3)
- [Turn on a Pages site](https://github.com/litkhai/clickstack-hyperdx-hols/issues/4)
- [Dashboard skills design](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5)
- [labs/elastic-migration/: three parts](https://github.com/litkhai/clickstack-hyperdx-hols/issues/19) — [ingest](https://github.com/litkhai/clickstack-hyperdx-hols/issues/21) (data, #20, is done)
- Blocking a migration in progress, all `priority:high`:
  [size and chunk the move](https://github.com/litkhai/clickstack-hyperdx-hols/issues/24),
  [one run state for resume and progress](https://github.com/litkhai/clickstack-hyperdx-hols/issues/25),
  [ID translation larger than memory](https://github.com/litkhai/clickstack-hyperdx-hols/issues/26)
