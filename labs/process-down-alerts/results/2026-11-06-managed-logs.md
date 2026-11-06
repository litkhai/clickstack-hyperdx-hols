# log alerts on Managed ClickStack, 2026-11-06

ClickHouse 26.8.1.608; alerts created through the ClickHouse Cloud API; webhook to example.com (not delivered)

Phases: P0 both normal @08:06:09 · P1 svc-b errors @08:09:21 · P2 svc-b silent @08:13:33 · P3 both normal @08:18:49 · end @08:21:59

Alert fields returned by the API: channel, channels, createdAt, dashboardId, detectionMode, displayName, executionErrors, id, interval, message, name, note, numConsecutiveWindows, source, state, tags, teamId, threshold, thresholdType, tileId, updatedAt

| alert | P0 both normal | P1 svc-b errors | P2 svc-b silent | P3 both normal |
|---|---|---|---|---|
| E1 errors per service (above 5) | OK | OK → ALERT | ALERT → OK | OK |
| E2 log lines per service (below 1) | OK | OK | OK | OK |
| E3 log lines vs expected services (below 1) | OK | OK | OK → ALERT | ALERT → OK |
