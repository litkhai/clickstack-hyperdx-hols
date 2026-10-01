# STATUS.md

**As of 2026-10-01** — split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10) with history.

## CI

`checks` (on pull requests): `links`, `syntax`, `otel-profiles`, `terraform`, `secrets`
(gitleaks), `hygiene` — green.
`pages` (on push to `main`): builds the site with `.github/scripts/build_site.py` and deploys
it to https://litkhai.github.io/clickstack-hyperdx-hols/. Each page sits at its own
repository path — `/labs/elastic-migration/`, `/otel-profiles/` — so a URL mirrors the tree.
GitHub secret scanning and push protection are on.

## Inventory

6 pages across four areas of the README tables: two in `labs/`, two in `workshops/`, plus
`otel-profiles/` and `clickstack-config/`. The two workshops are still single-language (#3).

7 OTel profiles in `otel-profiles/` — `linux-host`, `gpu-nvidia`, `baremetal-node`, `virt-kvm`,
`virt-vsphere`, `mysql`, `aws-rds-mysql`.
**Two verified, in Docker only**: `linux-host` (Docker Desktop's VM as the host, syslog from
Ubuntu 24.04 and 22.04 rsyslog fixtures) and `mysql` (MySQL 8.4.11 container, both tiers), via
`_base/docker-compose.otel-verify.yml`. Neither has run on a physical or cloud Linux host. The
other five each need their own class of hardware (or, for `aws-rds-mysql`, a real RDS instance),
so they carry no `Verified on …` line.

`_base/` has the local OSS stack and the Cloud target shape, plus `bin/check.sh` (readiness)
and `bin/verify.sh` (telemetry through to search). `verify.sh` is verified end to end on
ClickStack 2.39.1 (ClickHouse 26.8.7.19), with a negative control showing the OTLP receiver
refuses a wrong key. Its first run found the search layer passing on an error response; that
is fixed.

Three services sit behind compose profiles, for `labs/elastic-migration/` only, so a plain
`docker compose up -d` is unaffected:

| Service | Port | Profile | What it is |
|---|---|---|---|
| `elasticsearch` | 9200 | `elastic` | the source cluster, security off — the quick path |
| `elasticsearch-secure` | 9201 | `elastic-secure` | the same version with security **on**, which is the 8.x default; this is what the authenticated path is verified against |
| `clickhouse-target` | 8124 / 9001 | `elastic`, `migration` | the migration destination, pinned separately from the ClickStack bundle |

Version pins, and why they differ from each other:

| Pin | Version | Why |
|---|---|---|
| `clickstack` | 2.39.1 (bundles ClickHouse 26.8.7.19) | the version `otel-profiles/` was written against |
| `elasticsearch`, `elasticsearch-secure` | 8.17.0 | the version the migration these labs were built for runs |
| `clickhouse-target` (`migration` profile) | 26.6.8.7 | newest public patch of the 26.6 line ClickHouse Cloud's regular release channel runs (a live Cloud service reports `26.6.1.2191`) |

`labs/elastic-migration/` verifies against the last two, not the ClickStack bundle: a
migration must not be verified against a newer ClickHouse than its destination.

`clickstack-config/` applies and destroys cleanly against the local OSS stack. **Not verified
against Cloud** — that needs org credentials, so no `Verified on …` line.

`labs/elastic-migration/data/` is written and verified end to end on Elasticsearch 8.17.0
into ClickHouse 26.6.8.7. Each tool carries its own `Verified on …` line:

| Tool | What it does |
|---|---|
| `plan.py` | sizes the move and cuts it into row-equal chunks; refuses a pattern whose indices disagree about a field's type |
| `mapping_to_ddl.py` | `_mapping` to DDL, every field classified, and the sort key measured and marked `NEEDS REVIEW` rather than defaulted silently |
| `export.py` | PIT + `search_after` per slice, checkpointed and resumable |
| `load.sh` | NDJSON into ClickHouse, resumable per part |
| `run.py` | one run state for the whole migration: `pending → exported → loaded → verified`, retries, `--status` |
| `parity_checks.py` | query pairs, one per system |
| `es_client.py` | Elasticsearch auth and TLS for all of the above |
| `idmap/` | ID translation in ClickHouse, with the case matrix as 70 executable assertions and the dictionary layouts measured |

`labs/elastic-migration/AGENTS.md` is written for an agent **running** a migration rather
than changing the lab, and opens with what the lab does not do. `ingest/` is still a plan
only (#21).

## Open work

Tracked as issues — [all open](https://github.com/litkhai/clickstack-hyperdx-hols/issues) · [needs a re-run](https://github.com/litkhai/clickstack-hyperdx-hols/issues?q=is%3Aopen+label%3Are-verify):

- [F1–F7: roadmap labs](https://github.com/litkhai/clickstack-hyperdx-hols/issues/2)
- [F8: shorten and translate the two workshops](https://github.com/litkhai/clickstack-hyperdx-hols/issues/3)
- [Dashboard skills design](https://github.com/litkhai/clickstack-hyperdx-hols/issues/5)
- [labs/elastic-migration/: three parts](https://github.com/litkhai/clickstack-hyperdx-hols/issues/19) — [ingest](https://github.com/litkhai/clickstack-hyperdx-hols/issues/21) is the part still outstanding
- Found while building the data path, none of them blocking:
  [parity check 4 assumes a single-pass export](https://github.com/litkhai/clickstack-hyperdx-hols/issues/32),
  [translation is not a tracked run stage](https://github.com/litkhai/clickstack-hyperdx-hols/issues/33),
  [the DIRECT dictionary layout is unmeasured](https://github.com/litkhai/clickstack-hyperdx-hols/issues/34)
- Needs the real destination to answer, so labelled `re-verify`:
  [the Cloud-specific path — `s3()` load, `ssd_cache` PATH, memory ceiling — is documented but never run](https://github.com/litkhai/clickstack-hyperdx-hols/issues/41)
