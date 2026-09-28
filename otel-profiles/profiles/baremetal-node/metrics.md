# baremetal-node metrics

Two sources, scraped by one `prometheus` receiver: `node_exporter` for in-band OS
and hwmon data, `ipmi_exporter` for out-of-band BMC sensors. `hw.*` metrics are
**inserted**, so the originals survive.

## Mapped

| Source metric | Exporter | `hw.*` | Unit | Attributes |
|---|---|---|---|---|
| `ipmi_temperature_celsius` | ipmi | `hw.temperature` | `Cel` | `hw.id` ← `id`, `hw.sensor_location` ← `name` |
| `node_hwmon_temp_celsius` | node | `hw.temperature` | `Cel` | `hw.id` ← `sensor`, `hw.sensor_location` ← `chip` |
| `ipmi_dcmi_power_consumption_watts` | ipmi | `hw.power` | `W` | `hw.id=dcmi`, `hw.type=power_supply` |

Both temperature sources land on the same metric name, distinguished by
`hw.sensor_location` and by which exporter produced them. That is intentional —
in-band and out-of-band readings of the same machine rarely agree, and seeing
them side by side on one chart is the point.

## Deliberately not mapped

- **Fan speed** (`ipmi_fan_speed_rpm`, `node_hwmon_fan_rpm`). `fan` is a valid
  `hw.type`, but the hardware conventions define no fan-speed metric, so there
  is no correct name to map onto. Query the original names.
- **Voltage, current and `ipmi_sensor_value`.** Same reason: `voltage` is a
  valid `hw.type` with no corresponding metric in the convention.
- **`ipmi_sensor_state` / `node_hwmon_sensor_alarm`** could become `hw.status`,
  which requires `hw.state` to be one of `ok`, `degraded`, `failed`,
  `needs_cleaning`, `predicted_failure`. IPMI's own states do not map cleanly
  onto that set, and an `hw.status` row claiming `ok` for a state that actually
  meant something else is worse than no row.
- **Everything else `node_exporter` emits** stays under `node_*`. It is not
  hardware telemetry, and the `hostmetrics` receiver already covers the same
  ground under the `system.*` conventions. If you want `system.*` here, compose
  this profile with `linux-host`.

## Out-of-band alternatives

This profile takes the BMC in-band, through `ipmi_exporter`. The two other routes:

| Route | Status | Why not the default |
|---|---|---|
| `redfish` receiver | `development`, `distributions: []` | Not in the contrib image and not in ClickStack's build, so it needs a custom OCB build |
| `snmp` receiver | `alpha`, `distributions: [contrib]` | Works in a Tier B sidecar, but every metric is a hand-written vendor OID — a Dell iDRAC config is useless on an HPE iLO |

Both are worth a profile of their own once there is hardware to verify against.

## Operational notes

- **`ipmi_exporter` holds the BMC credentials**, not this profile. They go in the
  exporter's own config file; `.env.example` only carries the `?target=` value.
- **Scrape interval.** BMCs are slow and some rate-limit. 60s is already
  aggressive for IPMI over LAN; `ipmi_exporter`'s own timeout should be below
  the scrape interval or scrapes will overlap.
