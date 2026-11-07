# windows-host process check — run every minute by a SYSTEM scheduled task.
#
# Reports process.check.up = number of running processes that match each expected instance, so a
# stopped instance is an explicit 0 rather than a series that stops. Posts OTLP/HTTP JSON to the
# local collector (otlp/windows-host-check, 127.0.0.1:4318), which adds the host's resource
# attributes. The metric name is this profile's, not an OpenTelemetry semantic convention.
#
# Expected instances: C:\ProgramData\otelcol\expected-processes.json, one entry per instance:
#   [ { "instance": "channel-1", "executable": "GameServer.exe", "match": "*channel-1.cfg*" },
#     { "instance": "channel-2", "executable": "GameServer.exe", "match": "*channel-2.cfg*" } ]
# `match` is a -like pattern on the command line; "*" matches any command line.
# This generalised form (list from the file) verified through install.ps1 on Windows Server 2022,
# Windows PowerShell 5.1, 2026-11-07 (labs/hostmetrics-volume section 5): gs1 1 / gs2 0 after gs2 was killed.
$listPath = 'C:\ProgramData\otelcol\expected-processes.json'
# Two steps, not @(Get-Content ... | ConvertFrom-Json): Windows PowerShell 5.1 emits a JSON array as
# ONE object, so @() made a one-element list and every instance merged into "gs1 gs2" = 0
# (install.ps1 end to end, 2026-11-07). @($var) on the stored array keeps its elements.
$parsed = Get-Content -Raw $listPath | ConvertFrom-Json
$expected = @($parsed)
$byExe = @{}
$now = [string]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * 1000000)
$points = @()
foreach ($e in $expected) {
    if (-not $byExe.ContainsKey($e.executable)) {
        $byExe[$e.executable] = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq $e.executable })
    }
    $n = @($byExe[$e.executable] | Where-Object { $_.CommandLine -like $e.match }).Count
    $points += @{
        timeUnixNano = $now
        asInt        = [string]$n
        attributes   = @(
            @{ key = 'instance'; value = @{ stringValue = [string]$e.instance } },
            @{ key = 'process.executable.name'; value = @{ stringValue = [string]$e.executable } }
        )
    }
}
$body = @{
    resourceMetrics = @(@{
        resource     = @{ attributes = @(@{ key = 'service.name'; value = @{ stringValue = 'windows-host' } }) }
        scopeMetrics = @(@{
            scope   = @{ name = 'windows-host/process-check' }
            metrics = @(@{ name = 'process.check.up'; unit = '{process}'; gauge = @{ dataPoints = $points } })
        })
    })
}
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:4318/v1/metrics' -ContentType 'application/json' `
    -Body ($body | ConvertTo-Json -Depth 12 -Compress) | Out-Null
