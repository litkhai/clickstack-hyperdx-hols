# linux-host metrics

## Mapping

None. `hostmetrics` already emits `system.*` under the OpenTelemetry system
metrics conventions, so there is nothing to normalise — the `hw.*` mapping in
other profiles exists only because Prometheus exporters do not follow a
convention.

## Emitted

| Scraper | Metric prefix | Notes |
|---|---|---|
| `cpu` | `system.cpu.*` | `system.cpu.utilization` is opt-in and enabled here |
| `load` | `system.cpu.load_average.*` | 1m/5m/15m |
| `memory` | `system.memory.*` | `system.memory.utilization` is opt-in and enabled here |
| `disk` | `system.disk.*` | per-device IO counters |
| `filesystem` | `system.filesystem.*` | needs `root_path` to see host mounts |
| `network` | `system.network.*` | per-interface counters |
| `paging` | `system.paging.*` | swap in/out and utilisation |
| `processes` | `system.processes.*` | Linux, macOS and BSD only |
| `system` | `system.uptime` | |

## Deliberately not enabled

- **`process`** (per-process metrics, note the singular) is off. It is the
  highest-cardinality scraper by far — one metric stream per process — and on a
  busy host it dominates both ingest and storage. Enable it only with a
  `filter` processor restricting it to the processes you care about.
- **`nfs`** is off; it only reports anything on an NFS client or server.

## Logs

`filelog` reads `/var/log/syslog` and `/var/log/messages` through the `/hostfs`
bind mount. The `regex_parser` uses `on_error: send`, so a line that does not
match the RFC 3164 shape is still ingested with its raw body instead of being
dropped — journald-only distributions will show mostly unparsed lines, which is
the signal to reach for a journald sidecar instead (Tier B, no profile yet).

`time_parser` uses the `%b %d %H:%M:%S` layout, which carries no year. The
collector assumes the current year; lines from a log rotated across New Year
will be timestamped wrong.
