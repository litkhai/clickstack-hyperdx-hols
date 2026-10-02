-- tile: Fault switches and deploys
-- display: table
-- layout: 12 46 12 6
SELECT ts, 'fault' AS kind, fault AS what, target, if(enabled = 1, 'on', 'off') AS state, run_id
FROM apm_workflows.fault_events
WHERE ts >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND ts < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
UNION ALL
SELECT ts, 'deploy', concat(service, ' ', version), '', if(regression = 1, 'regression', ''), ''
FROM apm_workflows.deploy_events
WHERE ts >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND ts < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
ORDER BY ts DESC
