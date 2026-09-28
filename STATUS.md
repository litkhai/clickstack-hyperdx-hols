# STATUS.md

**As of 2026-09-28** — split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10) with history.

## CI

`checks`: `links`, `syntax`, `otel-profiles`, `shellcheck` (advisory), `secrets` (gitleaks), `hygiene` — green.
GitHub secret scanning and push protection are on.

## Inventory

3 labs in the README tables; 2 single-language: `workshops/o11y-vector-ai`, `workshops/observability-waf`.

7 OTel profiles in `otel-profiles/` — `linux-host`, `gpu-nvidia`, `baremetal-node`, `virt-kvm`,
`virt-vsphere`, `mysql`, `aws-rds-mysql`.
**None verified**: each needs its own class of hardware (or, for `aws-rds-mysql`, a real RDS
instance) to run against, so none carries a `Verified on …` line yet.

## Open work

Tracked as issues — [all open](https://github.com/litkhai/clickstack-hyperdx-hols/issues) · [needs a re-run](https://github.com/litkhai/clickstack-hyperdx-hols/issues?q=is%3Aopen+label%3Are-verify):

- [F0: _base/ shared ClickStack environment](https://github.com/litkhai/clickstack-hyperdx-hols/issues/1)
- [F1–F7: roadmap labs](https://github.com/litkhai/clickstack-hyperdx-hols/issues/2)
- [F8: shorten and translate the two workshops](https://github.com/litkhai/clickstack-hyperdx-hols/issues/3)
- [Turn on a Pages site](https://github.com/litkhai/clickstack-hyperdx-hols/issues/4)
- [Dashboard skills design](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5)
- [otel-profiles/: per-target-class collector config](https://github.com/litkhai/clickstack-hyperdx-hols/issues/8)
