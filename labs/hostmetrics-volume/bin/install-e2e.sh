#!/usr/bin/env bash
# install.ps1 end to end (windows_mode = install): the otel-profiles windows-host profile installed
# the way a fleet gets it — files and one command through SSM Run Command — then checked on the host
# and on the gateway. Needs aws, jq, terraform, base64; AWS credentials in the environment; the lab
# applied with -var windows_mode=install.
#
#   bin/install-e2e.sh ready              stand-in game servers up on Windows; gateway up
#   bin/install-e2e.sh install <version>  push install.ps1 + built config + process-check.ps1, run it
#   bin/install-e2e.sh state              service account / start mode / failure actions, task, variables
#   bin/install-e2e.sh data <minutes>     what reached the gateway in the last <minutes> (install_check.py)
#   bin/install-e2e.sh killtest           stop/start gs1 service, kill/start gs2, kill the collector
#   bin/install-e2e.sh logs               collector warnings and errors from the Application log
#   bin/install-e2e.sh win '<ps>' / linux '<sh>'   one ad-hoc command on that host
#
# Events go to results/<date>-install-events.jsonl. Nothing here creates or deletes AWS resources.
set -euo pipefail
LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$LAB_DIR/../.." && pwd)"
PROFILE="$ROOT/otel-profiles/profiles/windows-host"
TF_DIR="$LAB_DIR/terraform"
RESULTS="$LAB_DIR/results"
DAY=$(date -u +%F)
EVENTS="$RESULTS/$DAY-install-events.jsonl"
export AWS_PAGER=""
mkdir -p "$RESULTS"

out() { terraform -chdir="$TF_DIR" output -raw "$1"; }
LINUX=$(out linux_instance_id)
WINDOWS=$(out windows_instance_id)

ssm() {
    local inst=$1 doc=$2 script=$3 id status=Pending
    id=$(aws ssm send-command --instance-ids "$inst" --document-name "$doc" \
        --parameters "$(jq -cn --arg s "$script" '{commands: [$s], executionTimeout: ["900"]}')" \
        --query Command.CommandId --output text)
    for _ in $(seq 1 300); do
        sleep 3
        status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$inst" \
            --query Status --output text 2>/dev/null || echo Pending)
        case $status in Success|Failed|Cancelled|TimedOut) break ;; esac
    done
    aws ssm get-command-invocation --command-id "$id" --instance-id "$inst" \
        --query '[StandardOutputContent, StandardErrorContent]' --output text
    [ "$status" = Success ] || { echo "ssm $doc on $inst: $status" >&2; return 1; }
}
ps_win() { ssm "$WINDOWS" AWS-RunPowerShellScript "$1"; }
event() { jq -cn --arg e "$1" --arg t "$(date +%s)" '{event: $e, t: ($t|tonumber)}' >> "$EVENTS"; echo "$(date -u +%T) $1"; }
b64() { base64 < "$1" | tr -d '\n'; }

case ${1:-} in
ready)
    ssm "$LINUX" AWS-RunShellScript 'systemctl is-active otel-gateway; tail -1 /var/log/hmv-user-data.log; ls -l /var/lib/hmv'
    ps_win 'Get-Service GameServer | Format-Table -AutoSize Name, Status; Get-ScheduledTask GameServer2 | Format-Table -AutoSize TaskName, State; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "GameServer.exe" } | Format-Table -AutoSize ProcessId, CommandLine; Select-String -Path C:\hmv-user-data.log -Pattern "user data done" | Select-Object -Last 1 | ForEach-Object { $_.Line }'
    ;;
install)
    version=${2:?version}
    cfg=$(mktemp); "$ROOT/otel-profiles/bin/build-config.sh" --tier b windows-host > "$cfg"
    ep=$(out endpoint_dns)
    # expected instances: gs1 (service) and gs2 (task), told apart by their config file
    expected='[{"instance":"gs1","executable":"GameServer.exe","match":"*gs1.yaml*"},{"instance":"gs2","executable":"GameServer.exe","match":"*gs2.yaml*"}]'
    script=$(cat <<PS
\$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path C:\\install, C:\\ProgramData\\otelcol | Out-Null
[IO.File]::WriteAllBytes('C:\\install\\install.ps1', [Convert]::FromBase64String('$(b64 "$PROFILE/install.ps1")'))
[IO.File]::WriteAllBytes('C:\\install\\process-check.ps1', [Convert]::FromBase64String('$(b64 "$PROFILE/process-check.ps1")'))
[IO.File]::WriteAllBytes('C:\\install\\windows-host.config.yaml', [Convert]::FromBase64String('$(b64 "$cfg")'))
'$expected' | Set-Content -Encoding ascii C:\\ProgramData\\otelcol\\expected-processes.json
\$sw = [Diagnostics.Stopwatch]::StartNew()
& C:\\install\\install.ps1 -Version $version -Endpoint '$ep:4317' -ApiKey 'lab-no-auth' -Services '[GameServer]' -ProcessNames "['^GameServer\\.exe\$']" -Ec2Tags "['^lab\\.service\\.']"
"install.ps1 took \$([int]\$sw.Elapsed.TotalSeconds) s"
PS
)
    rm -f "$cfg"
    event "install $version start"
    ps_win "$script"
    event "install $version done"
    ;;
state)
    ps_win '$s = Get-CimInstance Win32_Service | Where-Object { $_.Name -eq "otelcol" }; "service: $($s.Name) state=$($s.State) start=$($s.StartMode) account=$($s.StartName) pid=$($s.ProcessId)"; "path: $($s.PathName)"; "services named otelcol*: $(@(Get-Service otelcol* ).Count)"; sc.exe qfailure otelcol | Select-String "RESET|RESTART"; $t = Get-ScheduledTask -TaskName "otelcol process-check"; "task: user=$($t.Principal.UserId) runlevel=$($t.Principal.RunLevel) interval=$($t.Triggers[0].Repetition.Interval) state=$($t.State) tasks named otelcol*: $(@(Get-ScheduledTask -TaskName "otelcol*").Count)"; $i = Get-ScheduledTaskInfo -TaskName "otelcol process-check"; "task last run: $($i.LastRunTime) result=$($i.LastTaskResult)"; foreach ($k in "CLICKSTACK_OTLP_ENDPOINT","CLICKSTACK_OTLP_INSECURE","WINDOWS_HOST_SERVICES","WINDOWS_HOST_PROCESS_NAMES","WINDOWS_HOST_EC2_TAGS") { "$k=$([Environment]::GetEnvironmentVariable($k, "Machine"))" }; "api key set: $([bool][Environment]::GetEnvironmentVariable("CLICKSTACK_API_KEY", "Machine"))"; & "C:\Program Files\otelcol\otelcol-contrib.exe" --version; "expected: $(Get-Content -Raw C:\ProgramData\otelcol\expected-processes.json)"'
    ;;
data)
    min=${2:-5}
    b=$(b64 "$LAB_DIR/bin/install_check.py")
    ssm "$LINUX" AWS-RunShellScript "set -e; echo $b | base64 -d > /tmp/install_check.py; e=\$(date +%s); python3 -I /tmp/install_check.py --file /var/lib/hmv/windows-install.jsonl --start \$((e - $min * 60)) --end \$e"
    ;;
killtest)
    event "killtest start"
    ps_win 'Stop-Service GameServer -Force; (Get-Service GameServer).Status' >/dev/null; event "S1 gs1 service stopped"
    sleep 180
    ps_win 'Start-Service GameServer; (Get-Service GameServer).Status' >/dev/null; event "S2 gs1 service started"
    sleep 120
    ps_win 'Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "GameServer.exe" -and $_.CommandLine -like "*gs2.yaml*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }' >/dev/null
    event "S3 gs2 task process killed"
    sleep 180
    ps_win 'Start-ScheduledTask -TaskName GameServer2' >/dev/null; event "S4 gs2 task started"
    sleep 120
    # S5: the collector process dies; the service's failure actions should restart it after 5 s
    r=$(ps_win '$old = (Get-CimInstance Win32_Service | Where-Object { $_.Name -eq "otelcol" }).ProcessId; Stop-Process -Id $old -Force; $sw = [Diagnostics.Stopwatch]::StartNew(); do { Start-Sleep -Milliseconds 500; $s = Get-CimInstance Win32_Service | Where-Object { $_.Name -eq "otelcol" } } until (($s.State -eq "Running" -and $s.ProcessId -ne $old -and $s.ProcessId -ne 0) -or $sw.Elapsed.TotalSeconds -gt 120); "old pid $old new pid $($s.ProcessId) state $($s.State) after $([Math]::Round($sw.Elapsed.TotalSeconds, 1)) s"')
    event "S5 collector killed: $(echo "$r" | head -1)"
    sleep 120
    event "killtest end"
    ;;
logs)
    ps_win 'Get-WinEvent -FilterHashtable @{LogName="Application"; ProviderName="otelcol"} -MaxEvents 400 -ErrorAction SilentlyContinue | Where-Object { $_.Message -match "warn|error" -and $_.Message -notmatch "deprecated" } | Select-Object -First 8 | ForEach-Object { "$($_.TimeCreated.ToUniversalTime().ToString("s"))Z " + $_.Message.Substring(0, [Math]::Min(400, $_.Message.Length)) }; "-- Service Control Manager about otelcol:"; Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="Service Control Manager"} -MaxEvents 200 | Where-Object { $_.Message -match "OpenTelemetry Collector" } | Select-Object -First 6 | ForEach-Object { "$($_.TimeCreated.ToUniversalTime().ToString("s"))Z $($_.Id) $($_.Message)" }'
    ;;
win) ps_win "$2" ;;
linux) ssm "$LINUX" AWS-RunShellScript "$2" ;;
*)
    sed -n '2,15p' "$0"; exit 2 ;;
esac
