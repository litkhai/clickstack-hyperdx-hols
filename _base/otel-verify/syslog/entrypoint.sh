#!/bin/sh
# Start rsyslog, then write one distinct line every few seconds through
# logger(1) so /var/log/syslog has something to read.
#
# Every line carries a counter. The package's default configuration sets
# $RepeatedMsgReduction on, which collapses identical consecutive messages into
# "last message repeated N times" -- a line with no unit, which would read as a
# parser failure that is really a fixture artefact.
set -eu

# Two Ubuntu releases share one log volume, one after the other, and their
# `syslog` users have different uids, so the second rsyslog cannot append to
# the first one's 0640 file. Rotate it aside first, as logrotate would on a
# real host. The new file is a new file to the collector's filelog receiver.
if [ -e /var/log/syslog ]; then
    mv /var/log/syslog "/var/log/syslog.$(date -u +%Y%m%dT%H%M%SZ)"
fi

rsyslogd

trap 'kill "$(cat /run/rsyslogd.pid)" 2>/dev/null || true; exit 0' TERM INT

host="$(hostname)"
n=0
while :; do
    n=$((n + 1))
    logger -t fixture -i "$host heartbeat $n"
    logger -t fixture-nopid "$host heartbeat $n without a pid"
    sleep 3 &
    wait $!
done
