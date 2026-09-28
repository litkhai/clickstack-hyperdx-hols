# gpu-nvidia metrics

Source: `dcgm-exporter`, scraped by the `prometheus` receiver. Every `hw.*` metric
below is **inserted**, so the original `DCGM_FI_*` metric is still there.

## Mapped

| DCGM metric | Source unit | `hw.*` | Unit | Scale |
|---|---|---|---|---|
| `DCGM_FI_DEV_GPU_UTIL` | % (0–100) | `hw.gpu.utilization` | `1` | ×0.01 |
| `DCGM_FI_DEV_FB_USED` | MiB | `hw.gpu.memory.usage` | `By` | ×1048576 |
| `DCGM_FI_DEV_FB_TOTAL` | MiB | `hw.gpu.memory.limit` | `By` | ×1048576 |
| `DCGM_FI_DEV_POWER_USAGE` | W | `hw.power` | `W` | — |
| `DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION` | mJ | `hw.energy` | `J` | ×0.001 |
| `DCGM_FI_DEV_GPU_TEMP` | °C | `hw.temperature` | `Cel` | — |
| `DCGM_FI_DEV_XID_ERRORS` | count | `hw.errors` (`error.type=xid`) | `{error}` | — |
| `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` | count | `hw.errors` (`error.type=corrected`) | `{error}` | — |
| `DCGM_FI_DEV_ECC_DBE_VOL_TOTAL` | count | `hw.errors` (`error.type=uncorrected`) | `{error}` | — |

Attributes on the inserted copies: `hw.id` from the exporter's `UUID` label,
`hw.name` from `modelName`, and `hw.type=gpu` where the convention requires it
(`hw.power`, `hw.energy`, `hw.errors`).

## Deliberately not mapped

- **`DCGM_FI_DEV_MEM_COPY_UTIL`** looks like it belongs on
  `hw.gpu.memory.utilization`, and does not. It is memory *bandwidth*
  utilisation — the fraction of time the memory interface was busy — not
  used/total. Mapping it would make a memory-pressure dashboard read the wrong
  thing entirely.
- **`hw.gpu.memory.utilization`** is therefore not emitted at all. The
  convention wants used ÷ total, and `metricstransform` cannot divide one metric
  by another. Compute it at query time from `hw.gpu.memory.usage` and
  `hw.gpu.memory.limit`, or in a ClickHouse view.
- **`DCGM_FI_PROF_PCIE_TX_BYTES` / `RX_BYTES`** are the natural candidates for
  `hw.gpu.io`, but whether a given dcgm-exporter build reports them as bytes or
  bytes-per-second depends on the profiling fields enabled, and the convention
  requires a `By` counter. Left alone rather than guessed at.

## Things to check on your own hardware

- **`DCGM_FI_DEV_FB_TOTAL` may be absent.** dcgm-exporter only exports the
  fields listed in its counters file (`-f/--collectors`), and the default set
  varies by version. If query 2 in `verify.sql` shows no
  `hw.gpu.memory.limit`, add `DCGM_FI_DEV_FB_TOTAL` to that file.
- **MIG.** On a MIG-partitioned GPU, dcgm-exporter adds `GPU_I_ID`/`GPU_I_PROFILE`
  labels and `UUID` is the parent device, so `hw.id` will collide across
  instances. Add `GPU_I_ID` to `hw.id` before trusting per-instance numbers.
- **Energy is a counter that resets.** `DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION`
  restarts at zero when `nv-hostengine` restarts. `hw.energy` inherits that.

## Cardinality

The `prometheus` receiver keeps every exporter label as a datapoint attribute,
including `UUID`, `device`, `modelName`, `Hostname` and — on Kubernetes —
`pod`, `namespace` and `container`. On a large fleet the pod labels are the
cardinality problem, not the GPU ones. Drop what you do not query with a
`filter` or `attributes` processor before it reaches ClickHouse.
