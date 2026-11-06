#!/usr/bin/env bash
# Drive the measurement through SSM. Needs aws, jq, terraform, base64; AWS credentials in the
# environment; the lab applied.
#
#   bin/run.sh ready      agents, game server instances and tasks up; files growing
#   bin/run.sh start      window start: zero the nft counters, snapshot the gateway's self-metrics
#   bin/run.sh end        window end: analyze.py on the gateway -> results/<date>-report.json
#   bin/run.sh killtest   stop/start each game server instance (~14 min) -> results/<date>-events.json
#   bin/run.sh timeline   timeline.py over the kill test -> results/<date>-timeline.json
#   bin/run.sh n2         reachability: endpoint yes; gateway VPC directly no; reverse no
#   bin/run.sh logs       agent warnings and errors, host facts, service accounts
#   bin/run.sh win '<ps>' / linux '<sh>'   one ad-hoc command on that host
#
# Nothing here creates or deletes AWS resources.
set -euo pipefail
LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="$LAB_DIR/terraform"
RESULTS="$LAB_DIR/results"
DAY=$(date -u +%F)
export AWS_PAGER=""
mkdir -p "$RESULTS"

out() { terraform -chdir="$TF_DIR" output -raw "$1"; }
LINUX=$(out linux_instance_id)
WINDOWS=$(out windows_instance_id)

# ssm <instance> <AWS-RunShellScript|AWS-RunPowerShellScript> <script>  -> stdout of the script
ssm() {
    local inst=$1 doc=$2 script=$3 id status=Pending
    id=$(aws ssm send-command --instance-ids "$inst" --document-name "$doc" \
        --parameters "$(jq -cn --arg s "$script" '{commands: [$s], executionTimeout: ["600"]}')" \
        --query Command.CommandId --output text)
    for _ in $(seq 1 120); do
        sleep 3
        status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$inst" \
            --query Status --output text 2>/dev/null || echo Pending)
        case $status in Success|Failed|Cancelled|TimedOut) break ;; esac
    done
    aws ssm get-command-invocation --command-id "$id" --instance-id "$inst" \
        --query StandardOutputContent --output text
    [ "$status" = Success ] || { echo "ssm $doc on $inst: $status" >&2; return 1; }
}

# py <script.py> <args>: run one of bin/*.py on the gateway (sent base64 through SSM)
py() {
    local b64; b64=$(base64 < "$LAB_DIR/bin/$1" | tr -d '\n')
    ssm "$LINUX" AWS-RunShellScript "set -e; cd /var/lib/hmv; echo $b64 | base64 -d > /tmp/$1; python3 -I /tmp/$1 $2"
}

ps_win() { ssm "$WINDOWS" AWS-RunPowerShellScript "$1"; }
event() { jq -cn --arg e "$1" --arg t "$(date +%s)" '{event: $e, t: ($t|tonumber)}' >> "$RESULTS/$DAY-events.jsonl"; echo "$(date -u +%T) $1"; }

case ${1:-} in
ready)
    ssm "$LINUX" AWS-RunShellScript \
        'systemctl is-active otel-gateway otel-agent; tail -1 /var/log/hmv-user-data.log; ls -l /var/lib/hmv'
    ps_win 'Get-Service otelcol-agent, otelcol-agent-ls, GameServer | Format-Table -AutoSize Name, Status; Get-ScheduledTask GameServer2, ProcessCheck | Format-Table -AutoSize TaskName, State; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "GameServer.exe" } | Format-Table -AutoSize ProcessId, CommandLine; Get-Content C:\hmv-user-data.log -Tail 2'
    ;;
start)
    ssm "$LINUX" AWS-RunShellScript \
        'set -e; cd /var/lib/hmv; nft reset counters table inet hmv >/dev/null; curl -s localhost:8888/metrics > prom-start.txt; date +%s > t0; echo "window start $(cat t0)"'
    ;;
end)
    ssm "$LINUX" AWS-RunShellScript \
        'set -e; cd /var/lib/hmv; date +%s > t1; curl -s localhost:8888/metrics > prom-end.txt; nft -j list counters table inet hmv > nft.json; echo ok' >/dev/null
    py analyze.py '--dir . --start $(cat t0) --end $(cat t1) --nft nft.json --prom-start prom-start.txt --prom-end prom-end.txt' \
        > "$RESULTS/$DAY-report.json"
    echo "wrote $RESULTS/$DAY-report.json"
    jq '{window, sets: (.sets | map_values({points_per_min, points_per_scrape, series, cpus, per_cpu_points, wire_bytes_per_min, wire_bytes_per_point, receiver_accepted_points_delta, points}))}' "$RESULTS/$DAY-report.json"
    ;;
killtest)
    : > "$RESULTS/$DAY-events.jsonl"
    event "start"
    ps_win 'Stop-Service GameServer -Force; (Get-Service GameServer).Status' >/dev/null; event "S1 gs1 service stopped"
    sleep 240
    ps_win 'Start-Service GameServer; (Get-Service GameServer).Status' >/dev/null; event "S2 gs1 service started"
    sleep 180
    ps_win 'Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "GameServer.exe" } | Where-Object { $_.CommandLine -like "*gs2.yaml*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }' >/dev/null
    event "S3 gs2 task process killed"
    sleep 240
    ps_win 'Start-ScheduledTask -TaskName GameServer2' >/dev/null; event "S4 gs2 task started"
    sleep 180
    event "end"
    ;;
timeline)
    s=$(jq -s 'map(select(.event=="start"))[0].t - 120' "$RESULTS/$DAY-events.jsonl")
    e=$(jq -s 'map(select(.event=="end"))[0].t' "$RESULTS/$DAY-events.jsonl")
    py timeline.py "--dir . --start $s --end $e" > "$RESULTS/$DAY-timeline.json"
    echo "wrote $RESULTS/$DAY-timeline.json"
    ;;
n2)
    ep=$(out endpoint_dns); lip=$(out linux_private_ip); wip=$(out windows_private_ip)
    echo "== from Windows (source VPC): endpoint, then the gateway's own address"
    ps_win "foreach (\$h in @('$ep', '$lip')) { \$r = Test-NetConnection -ComputerName \$h -Port 4319 -WarningAction SilentlyContinue; \"\$h 4319 TcpTestSucceeded=\$(\$r.TcpTestSucceeded)\" }"
    echo "== from the gateway (gateway VPC) to the Windows host"
    ssm "$LINUX" AWS-RunShellScript "if timeout 5 bash -c '</dev/tcp/$wip/3389' 2>/dev/null; then echo '$wip 3389 reachable'; else echo '$wip 3389 not reachable'; fi"
    ;;
logs)
    echo "== linux"
    ssm "$LINUX" AWS-RunShellScript \
        'echo "vcpu=$(nproc) disks=$(lsblk -dn | wc -l) ifaces=$(ls /sys/class/net | wc -l)"; /opt/otel/otelcol-contrib --version; journalctl -u otel-agent --no-pager | grep -E "\"(warn|error)\"|\twarn\t|\terror\t" | grep -v deprecated | cut -c1-300 | tail -10'
    echo "== windows"
    ps_win '"vcpu=$((Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum) disks=$((Get-CimInstance Win32_DiskDrive).Count) ifaces=$((Get-NetAdapter).Count) os=$((Get-CimInstance Win32_OperatingSystem).Caption)"; Get-CimInstance Win32_Service | Where-Object { $_.Name -like "otelcol*" -or $_.Name -eq "GameServer" } | Format-Table -AutoSize Name, StartName, State; foreach ($s in @("otelcol-agent", "otelcol-agent-ls")) { "-- $s"; Get-WinEvent -FilterHashtable @{LogName="Application"; ProviderName=$s} -MaxEvents 300 -ErrorAction SilentlyContinue | Where-Object { $_.Message -match "warn|error" -and $_.Message -notmatch "deprecated" } | Select-Object -First 6 | ForEach-Object { $_.Message.Substring(0, [Math]::Min(500, $_.Message.Length)) } }'
    ;;
win) ps_win "$2" ;;
linux) ssm "$LINUX" AWS-RunShellScript "$2" ;;
*)
    sed -n '4,14p' "$0"; exit 2 ;;
esac
