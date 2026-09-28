# virt-kvm metrics

Two sources: a libvirt Prometheus exporter for per-domain counters, and
`hostmetrics` for the hypervisor's own OS metrics.

## Mapping

**None, on purpose.** Two reasons:

1. Per-domain CPU, memory, block and network counters are not hardware
   telemetry. `hw.*` is for physical components — there is no convention to map
   a guest's vCPU time onto.
2. There is no canonical libvirt exporter. The two in common use disagree on
   metric names, on label names, and on whether memory is reported in bytes or
   KiB. Any mapping written here would be wrong for at least one of them, and
   `CONVENTIONS.md` rule 5 says a wrong mapping is worse than none.

The hypervisor's `hostmetrics` output needs no mapping either — it is already
`system.*`.

## Exporters

| Exporter | Default port | Metric prefix |
|---|---|---|
| `libvirt-exporter` (AlexZzz) | 9177 | `libvirt_domain_info_*`, `libvirt_domain_block_stats_*`, `libvirt_domain_interface_stats_*` |
| `libvirt_exporter` (Tinkoff fork) | 9177 | same family, different suffixes |

Neither is pinned here, and no image is shipped, because which one you run
determines the metric names — check yours against query 2 in `verify.sql` and
write your own `metricstransform` block if you need normalised names.

## Guest-side telemetry

The hypervisor sees a guest from outside: it knows the vCPU time consumed, not
what the guest thinks its load average is, and it cannot see inside the guest's
filesystem. For that, run the `linux-host` profile inside the guest. The two
profiles compose:

```bash
./bin/build-config.sh virt-kvm linux-host > custom.config.yaml
```

Guest and hypervisor rows are then told apart by `deploy.platform` — `host` from
inside, `vm` from the hypervisor.

## Cardinality

Per-domain metrics scale with the number of VMs multiplied by the number of
block devices and interfaces each one has. On a dense host that is the largest
contributor in this profile by a wide margin; a `filter` processor restricting
the domains you care about is worth adding before this goes near a fleet.
