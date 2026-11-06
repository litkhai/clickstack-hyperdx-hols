# Method D: a process check that always reports, run every minute by a SYSTEM scheduled task.
# For each expected instance (told apart by its config file in the command line) it sends
# game.process.up = number of matching GameServer.exe processes: 1 when up, 0 when it is gone.
# A stopped process is an explicit 0, not a missing series. Posts OTLP/HTTP JSON to the local
# agent (otlp/script receiver), which forwards it with the same resource detection as the rest.
$expected = @('gs1', 'gs2')
$procs = @(Get-CimInstance Win32_Process -Filter "Name = 'GameServer.exe'")
$now = [string]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * 1000000)
$points = @()
foreach ($inst in $expected) {
    $n = @($procs | Where-Object { $_.CommandLine -like "*$inst.yaml*" }).Count
    $points += @{
        timeUnixNano = $now
        asInt        = [string]$n
        attributes   = @(@{ key = 'instance'; value = @{ stringValue = $inst } })
    }
}
$body = @{
    resourceMetrics = @(@{
        resource     = @{ attributes = @(@{ key = 'service.name'; value = @{ stringValue = 'process-check' } }) }
        scopeMetrics = @(@{
            scope   = @{ name = 'check.ps1' }
            metrics = @(@{ name = 'game.process.up'; unit = '{process}'; gauge = @{ dataPoints = $points } })
        })
    })
}
$json = $body | ConvertTo-Json -Depth 12 -Compress
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:4318/v1/metrics' -ContentType 'application/json' -Body $json | Out-Null
