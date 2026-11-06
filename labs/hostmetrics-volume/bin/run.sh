#!/usr/bin/env bash
# Drive the measurement through SSM. Needs aws, jq, terraform; AWS_PROFILE set; the lab applied.
#
#   bin/run.sh ready      both agents up, both base files growing
#   bin/run.sh start      window start: zero the nft counters, snapshot the gateway's self-metrics
#   bin/run.sh end        window end: analyze on the gateway, save results/<date>-report.json
#   bin/run.sh logs       agent warnings and errors (journal on Linux, event log on Windows) and
#                         the host facts the counts depend on (vCPUs, disks, interfaces)
#
# Nothing here creates or deletes AWS resources.
set -euo pipefail
LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="$LAB_DIR/terraform"
RESULTS="$LAB_DIR/results"
export AWS_PAGER=""

out() { terraform -chdir="$TF_DIR" output -raw "$1"; }
LINUX=$(out linux_instance_id)
WINDOWS=$(out windows_instance_id)

# ssm <instance> <AWS-RunShellScript|AWS-RunPowerShellScript> <script>  -> stdout of the script
ssm() {
    local inst=$1 doc=$2 script=$3 id status
    id=$(aws ssm send-command --instance-ids "$inst" --document-name "$doc" \
        --parameters "$(jq -cn --arg s "$script" '{commands: [$s]}')" \
        --query Command.CommandId --output text)
    for _ in $(seq 1 60); do
        sleep 3
        status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$inst" \
            --query Status --output text 2>/dev/null || echo Pending)
        case $status in Success|Failed|Cancelled|TimedOut) break ;; esac
    done
    aws ssm get-command-invocation --command-id "$id" --instance-id "$inst" \
        --query StandardOutputContent --output text
    [ "$status" = Success ] || { echo "ssm $doc on $inst: $status" >&2; return 1; }
}

case ${1:-} in
ready)
    ssm "$LINUX" AWS-RunShellScript \
        'systemctl is-active otel-gateway otel-agent; tail -1 /var/log/hmv-user-data.log; ls -l /var/lib/hmv'
    ssm "$WINDOWS" AWS-RunPowerShellScript \
        '(Get-Service otelcol-agent).Status; Get-Content C:\hmv-user-data.log -Tail 3'
    ;;
start)
    ssm "$LINUX" AWS-RunShellScript \
        'set -e; cd /var/lib/hmv; nft reset counters table inet hmv >/dev/null; curl -s localhost:8888/metrics > prom-start.txt; date +%s > t0; echo "window start $(cat t0)"'
    ;;
end)
    mkdir -p "$RESULTS"
    f="$RESULTS/$(date -u +%F)-report.json"
    ssm "$LINUX" AWS-RunShellScript \
        'set -e; cd /var/lib/hmv; date +%s > t1; curl -s localhost:8888/metrics > prom-end.txt; nft -j list counters table inet hmv > nft.json; python3 -I /opt/otel/analyze.py --dir . --start $(cat t0) --end $(cat t1) --nft nft.json --prom-start prom-start.txt --prom-end prom-end.txt' \
        > "$f"
    echo "wrote $f"
    jq '{window, sets: (.sets | map_values({points_per_min, points_per_scrape, series, cpus, per_cpu_points, wire_bytes_per_min, wire_bytes_per_point, receiver_accepted_points_delta, points}))}' "$f"
    ;;
logs)
    echo "== linux"
    ssm "$LINUX" AWS-RunShellScript \
        'echo "vcpu=$(nproc) disks=$(lsblk -dn | wc -l) ifaces=$(ls /sys/class/net | wc -l) fs=$(findmnt -rn | wc -l)"; /opt/otel/otelcol-contrib --version; journalctl -u otel-agent --no-pager | grep -E "\"(warn|error)\"|\twarn\t|\terror\t" | grep -v deprecated | cut -c1-400 | tail -20'
    echo "== windows"
    ssm "$WINDOWS" AWS-RunPowerShellScript \
        '"vcpu=$((Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum) disks=$((Get-CimInstance Win32_DiskDrive).Count) ifaces=$((Get-NetAdapter).Count) account=$((Get-CimInstance Win32_Service -Filter "Name=''otelcol-agent''").StartName)"; Get-WinEvent -FilterHashtable @{LogName="Application"; ProviderName="otelcol-agent"} -MaxEvents 200 -ErrorAction SilentlyContinue | Where-Object { $_.Message -match "warn|error" -and $_.Message -notmatch "deprecated" } | Select-Object -First 20 | ForEach-Object { $_.Message.Substring(0, [Math]::Min(400, $_.Message.Length)) }'
    ;;
*)
    sed -n '2,10p' "$0"; exit 2 ;;
esac
