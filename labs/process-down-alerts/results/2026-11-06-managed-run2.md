# process-down alerts on Managed ClickStack, 2026-11-06

ClickHouse 26.8.1.608; alerts created through the ClickHouse Cloud API; webhook to example.com (not delivered)

Phases: P0 both up @07:29:05 · P1 host-b stopped @07:32:14 · P2 both up @07:37:32 · P3 all down @07:40:42 · P4 both up @07:44:53 · end @07:48:03

Alert fields returned by the API: channel, channels, createdAt, dashboardId, detectionMode, displayName, executionErrors, id, interval, message, name, note, numConsecutiveWindows, source, state, tags, teamId, threshold, thresholdType, tileId, updatedAt

| alert | P0 both up | P1 host-b stopped | P2 both up | P3 all down | P4 both up |
|---|---|---|---|---|---|
| M2 SQL, group by host | ALERT → OK | OK | OK | OK → ALERT | ALERT → OK |
| M3 SQL, expected hosts | OK | OK → ALERT | ALERT → OK | OK → ALERT | ALERT → OK |
| M4 SQL number, host-b | OK | OK → ALERT | ALERT → OK | OK → ALERT | ALERT → OK |
| M5 SQL, no group | OK | OK | OK | OK → ALERT | ALERT → OK |
