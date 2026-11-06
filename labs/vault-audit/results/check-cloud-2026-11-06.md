# bin/check.py on Managed ClickStack — 2026-11-06

Target: ClickHouse Cloud 26.8.1.608, database `vault_audit`, written by otelcol-contrib 0.160.0
over the native protocol (9440, TLS). Vault: hashicorp/vault 2.2.1 (3-node Raft) on Docker Desktop.
Managed ClickStack HyperDX 2.42.0-sha6e4d740 (read from the app, `window.hdx.appVersion`).
Run by bin/cloud.sh in 162 s. S7's negative runs against the local target only.

```
PASS  S1 every audit line is one row  -- 1030 rows, 1030 lines on 3 nodes
PASS  S1 request and response share one TraceId  -- 516 TraceIds, 0 not a pair, 2 audit-enable halves
PASS  S2 the value's HMAC is in the log (positive control)  -- 3 rows hold hmac-sha256:a8c8fcb35c4b...
PASS  S2 the value itself is nowhere in ClickHouse  -- 0 rows
PASS  S3 the login is recorded with its accessor and policies  -- auth/approle/login | ["app-ro","default"] | 900
PASS  S3 the token's later uses join to the login (accessor = client token lookup)  -- cert_issue=2, denied=1, key_use=1, secret_read=1, token_issue=1; by token HMAC: 6
PASS  S3 an issued, never-used token has no uses  -- issued 1, uses 0
PASS  S4 a read outside the policy: WARN, allowed=false, denied  -- HTTP 403; WARN false denied
PASS  S4 the allowed read by the same token: INFO, allowed=true  -- INFO true secret_read
PASS  S5 tuned mount: the issued serial is in the log as it is  -- 25:20:93:d2:8f:29:2b:34:4f:91:16:89:23:4d:51:e1:11:fa:89:21: 1 row
PASS  S5 untuned mount: the serial is only its HMAC  -- 3f:e1:fe:71:c5:33:92:66:cd:86:64:30:3f:52:47:1c:2f:d2:2d:c1 -> hmac-sha256:8045537e24b21b7f9319...
PASS  S6 a standby takes over and writes the next entries  -- vault-1 stopped, vault-2 active after 1s; the write sent through vault-3 is in vault-2's file
PASS  S6 the node that stayed standby wrote nothing  -- vault-3: 0 rows since the stop
PASS  S6 vault_core_active moved; the stopped node's up went to 0  -- active={'vault-1:8200': '0', 'vault-2:8200': '1', 'vault-3:8200': '0'} up(during stop)={'vault-1:8200': '0', 'vault-2:8200': '1', 'vault-3:8200': '1'}
PASS  S6 nothing lost across the failover  -- 1044 rows, 1044 lines
PASS  S7 after a Collector restart: no loss, no duplicates  -- 1046 rows, 1046 lines, 0 duplicates
SKIP  S7 negative (Collector without file_storage): local target only
PASS  S8 exactly one of three nodes reports vault_core_active=1  -- {'vault-1:8200': '0', 'vault-2:8200': '1', 'vault-3:8200': '0'}
PASS  S8 the count connector's denied total equals the denied rows  -- metric 39, rows 39
PASS  S8 a family outside the keep list (go_*) never arrives  -- 0 rows

all passed
```
