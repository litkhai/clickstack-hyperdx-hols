# STATUS.md

**As of 2026-09-29** — split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10) with history.

## CI

`checks` (on pull requests): `links`, `syntax`, `otel-profiles`, `secrets` (gitleaks), `hygiene` — green.
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

`clickstack-config/` applies and destroys cleanly against the local OSS stack. **Not verified
against Cloud** — that needs org credentials, so no `Verified on …` line.

## Open work

Tracked as issues — [all open](https://github.com/litkhai/clickstack-hyperdx-hols/issues) · [needs a re-run](https://github.com/litkhai/clickstack-hyperdx-hols/issues?q=is%3Aopen+label%3Are-verify):

- [F0: _base/ shared ClickStack environment](https://github.com/litkhai/clickstack-hyperdx-hols/issues/1)
- [F1–F7: roadmap labs](https://github.com/litkhai/clickstack-hyperdx-hols/issues/2)
- [F8: shorten and translate the two workshops](https://github.com/litkhai/clickstack-hyperdx-hols/issues/3)
- [Turn on a Pages site](https://github.com/litkhai/clickstack-hyperdx-hols/issues/4)
- [Dashboard skills design](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5)
- [verify/: staged end-to-end verification](https://github.com/litkhai/clickstack-hyperdx-hols/issues/13) — scope cut to `_base/bin/verify.sh`
