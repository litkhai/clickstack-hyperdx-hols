#!/bin/sh
# The official image has no /var/log/mysql, and the log volume is mounted at
# /var/log (see ../../docker-compose.otel-verify.yml), so create the directory
# the error and slow logs go in, owned by the user mysqld drops to, before
# handing over to the image's own entrypoint.
set -eu
mkdir -p /var/log/mysql
chown mysql:mysql /var/log/mysql
exec docker-entrypoint.sh "$@"
