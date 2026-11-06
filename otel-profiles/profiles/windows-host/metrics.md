# windows-host metrics

## Mapping

None. `hostmetrics` emits `system.*` and `process.*` under the OpenTelemetry conventions;
`windows.service.status` is the `windows_service` receiver's own metric; `process.check.up` is
this profile's (not a semantic convention).

## Emitted, and points per scrape

Measured on Windows Server 2022 Datacenter, `t3.medium` (2 vCPU, 1 network adapter, 1 disk,
1 volume), otelcol-contrib 0.155.0, 60 s interval, 30 scrapes, 2026-11-06
([`labs/hostmetrics-volume`](../../../labs/hostmetrics-volume/README.md)).

| Scraper | Metric | Points / scrape | Grows with |
|---|---|---|---|
| `cpu` | `system.cpu.time`, `system.cpu.utilization` | 8 + 8 | 4 states × vCPU each |
| `load` | `system.cpu.load_average.1m/5m/15m` | 3 | — (emulated on Windows) |
| `memory` | `system.memory.usage`, `system.memory.utilization` | 2 + 2 | — |
| `disk` | `system.disk.io`, `.operations`, `.operation_time` (2 each), `.io_time`, `.pending_operations` (1 each) | 8 | per disk |
| `filesystem` | `system.filesystem.usage` | 2 | per volume |
| `network` | `system.network.io`, `.packets`, `.errors`, `.dropped` (2 each) | 8 | per adapter |
| `network` | `system.network.connections` | 12 | — (TCP states) |
| `paging` | `system.paging.usage`, `.operations`, `.faults` | 6 | — |
| `system` | `system.uptime` | 1 | — |
| **base total** | | **68** | ≈ 52 + 8 × vCPU |
| `process` | `process.cpu.time` (2), `process.disk.io` (2), `process.memory.usage`, `process.memory.virtual` | 6 | per matched process |
| `windows_service` | `windows.service.status` | 1 | per listed service |
| check | `process.check.up` | 1 | per expected instance |

On the wire (OTLP/HTTP, gzip, through an NLB and an interface endpoint): 2,912 bytes per minute
for the base set, 43.1 bytes per point.

Resource attributes on every point: `host.name`, `os.type`, `host.id`, `host.image.id`,
`host.type`, `cloud.provider`, `cloud.platform`, `cloud.region`, `cloud.account.id`,
`cloud.availability_zone`, the mapped `ec2.tag.*`, plus `deploy.platform` and `service.name` from
this profile. The process scraper adds `process.pid`, `process.parent_pid`,
`process.executable.name`, `process.executable.path`, `process.command`,
`process.command_line`, `process.owner`.

## Deliberately not enabled

- **`processes`** — not supported on Windows.
- **`process` for every process** — one stream per process; only the executables in
  `WINDOWS_HOST_PROCESS_NAMES`.
- **`windowsperfcounters` Process object** — instance names (`GameServer`, `GameServer#1`) follow
  start order: when the first instance stopped, the second was renamed to `GameServer`, and each
  change logged "A counter with a negative value was detected". `Process V2` (PID in the name)
  needs Windows 11 or later. Use it for other counters (for example `Processor Queue Length`) if a
  current Zabbix item needs one.
