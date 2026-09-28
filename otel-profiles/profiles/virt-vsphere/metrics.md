# virt-vsphere metrics

Source: the `vcenter` receiver, running in a Tier B sidecar.

## Mapping

None. The receiver already emits metrics under its own `vcenter.*` convention
with proper units and resource attributes, and they are virtual-infrastructure
metrics rather than hardware sensor readings, so `hw.*` does not apply.

## Emitted

| Prefix | Scope |
|---|---|
| `vcenter.cluster.*` | hosts, VM counts, CPU/memory limits and effective capacity |
| `vcenter.host.*` | ESXi host CPU, memory, disk latency and throughput, network |
| `vcenter.vm.*` | per-VM CPU, memory, disk and network |
| `vcenter.datastore.*` | capacity and usage |
| `vcenter.resource_pool.*` | shares, CPU and memory usage |

Resource attributes carry the inventory path — `vcenter.cluster.name`,
`vcenter.host.name`, `vcenter.datacenter.name`, `vcenter.virtual_machine.name` —
which is what makes the data navigable in HyperDX.

## Stability

`vcenter` metrics are **alpha** upstream. Metric names, units and attributes can
change between collector releases, and at least one behaviour is behind a feature
gate (`receiver.vcenter.resourcePoolMemoryUsageAttribute`). Pin the sidecar image
tag and re-run `verify.sql` after any bump; a dashboard built on alpha metric
names will break silently otherwise.

## Operational notes

- **`collection_interval` is 5m, not the receiver's 2m default.** vCenter's
  `QueryPerf` API is the bottleneck on any real inventory. If collection starts
  overrunning the interval, raise the interval before touching
  `max_query_metrics` — lowering the batch size makes more API calls, not fewer.
- **`max_query_metrics` must not exceed vCenter's own `vpxd.stats.maxQueryMetrics`**
  (default 256). The receiver defaults to 256 to match; if your vCenter is set
  lower, set it lower here too or calls will be rejected.
- **Read-only credentials.** The receiver only reads, so give it a read-only
  vSphere role. It needs no write permission anywhere.
- **Per-VM cardinality** grows with the inventory and every VM produces several
  metric streams. On a large estate this is the profile to watch, and the reason
  `collection_interval` is deliberately coarse.

## Not covered

- **vSphere events and tasks** are logs, not metrics, and the receiver does not
  emit them — it is metrics-only (`stability: alpha: [metrics]`).
- **Physical host hardware sensors** reached through vCenter's hardware status
  view are not exposed by this receiver. For those, take the BMC directly with
  the `baremetal-node` profile.
