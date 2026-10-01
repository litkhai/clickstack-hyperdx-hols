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
bind mount. The `regex_parser` accepts two line shapes, because rsyslog's
default file format differs between Ubuntu LTS releases:

| Format | Example | Where it is the default |
|---|---|---|
| RFC 3164 | `Oct  1 04:14:15 host tag[pid]: msg` | Ubuntu 22.04 (`$ActionFileDefaultTemplate RSYSLOG_TraditionalFileFormat` in `/etc/rsyslog.conf`) |
| ISO 8601 / RFC 3339 | `2026-10-01T04:13:39.637057+00:00 host tag[pid]: msg` | Ubuntu 24.04 (that line is gone, so rsyslog's high-precision default applies) |

Both shapes fill the same attributes (`ts`, `host`, `unit`, `pid`, `msg`); one
`time_parser` per shape, each selected by an `if:` on the form of `ts`. The
`regex_parser` uses `on_error: send`, so a line that matches neither shape is
still ingested with its raw body instead of being dropped — journald-only
distributions will show mostly unparsed lines, which is the signal to reach for
a journald sidecar instead (Tier B, no profile yet).

The RFC 3164 `time_parser` uses the `%b %d %H:%M:%S` layout, which carries no
year and no zone. The collector assumes the current year, so lines from a log
rotated across New Year will be timestamped wrong, and it reads the time in its
own local zone. The ISO 8601 form has both in the line, so neither caveat
applies to it.
