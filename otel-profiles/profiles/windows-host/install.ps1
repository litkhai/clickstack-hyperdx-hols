# windows-host install — run once per host as Administrator (or from SSM Run Command / State Manager).
#
#   install.ps1 -Version 0.155.0 -Endpoint http://gateway.example.internal:4317 -ApiKey <key> `
#               -Services '[GameServer]' -ProcessNames "['^GameServer\.exe$']" -Ec2Tags "['^svc\.']"
#
# Puts otelcol-contrib in C:\Program Files\otelcol, the built config (bin/build-config.sh --tier b
# windows-host) and process-check.ps1 next to it, sets the machine-level variables the config reads,
# registers the collector as an automatic LocalSystem service that restarts on failure, and the
# check as a 1-minute SYSTEM task. Re-running upgrades in place.
# Verified end to end in labs/hostmetrics-volume section 5 on Windows Server 2022 (Windows PowerShell
# 5.1), 2026-11-07: installed through SSM Run Command with 0.155.0 (108 s), re-run with 0.155.0 and
# 0.164.0 (one service, one task each time).
param(
    [Parameter(Mandatory)] [string] $Version,
    [Parameter(Mandatory)] [string] $Endpoint,
    [Parameter(Mandatory)] [string] $ApiKey,
    [string] $Insecure = 'true',
    # Required: an empty include_services makes windows_service report every service on the host.
    [Parameter(Mandatory)] [string] $Services,
    [string] $ProcessNames = "['^$']",
    [string] $Ec2Tags = '[]',
    [string] $ConfigPath = "$PSScriptRoot\windows-host.config.yaml"
)
$ErrorActionPreference = 'Stop'
$dir = 'C:\Program Files\otelcol'
New-Item -ItemType Directory -Force -Path $dir, 'C:\ProgramData\otelcol' | Out-Null

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$tgz = Join-Path $env:TEMP "otelcol-contrib_$Version.tar.gz"
Invoke-WebRequest -UseBasicParsing -OutFile $tgz `
    -Uri "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v$Version/otelcol-contrib_$($Version)_windows_amd64.tar.gz"
if (Get-Service otelcol -ErrorAction SilentlyContinue) { Stop-Service otelcol -Force }
tar.exe -xzf $tgz -C $dir otelcol-contrib.exe
Copy-Item $ConfigPath "$dir\config.yaml" -Force
Copy-Item "$PSScriptRoot\process-check.ps1" "$dir\process-check.ps1" -Force
if (-not (Test-Path 'C:\ProgramData\otelcol\expected-processes.json')) {
    '[]' | Set-Content -Encoding ascii 'C:\ProgramData\otelcol\expected-processes.json'
}

$vars = @{
    CLICKSTACK_OTLP_ENDPOINT = $Endpoint; CLICKSTACK_OTLP_INSECURE = $Insecure; CLICKSTACK_API_KEY = $ApiKey
    WINDOWS_HOST_SERVICES = $Services; WINDOWS_HOST_PROCESS_NAMES = $ProcessNames; WINDOWS_HOST_EC2_TAGS = $Ec2Tags
}
foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, $vars[$k], 'Machine') }

if (-not (Get-Service otelcol -ErrorAction SilentlyContinue)) {
    try { New-EventLog -LogName Application -Source otelcol } catch { }
    New-Service -Name otelcol -DisplayName 'OpenTelemetry Collector (windows-host)' -StartupType Automatic `
        -BinaryPathName "`"$dir\otelcol-contrib.exe`" --config `"$dir\config.yaml`"" | Out-Null
    # restart after 5 s, 30 s, 60 s; reset the failure count after a day
    sc.exe failure otelcol reset= 86400 actions= restart/5000/restart/30000/restart/60000 | Out-Null
}
Start-Service otelcol

$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\process-check.ps1`""
Register-ScheduledTask -TaskName 'otelcol process-check' -User 'SYSTEM' -RunLevel Highest -Force `
    -Settings $settings -Trigger $trigger -Action $action | Out-Null
"windows-host installed: otelcol $Version, $((Get-Service otelcol).Status)"
