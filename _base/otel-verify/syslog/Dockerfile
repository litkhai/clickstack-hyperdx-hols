# Fixture for otel-profiles/profiles/linux-host: a real rsyslog writing
# /var/log/syslog, because Docker Desktop's VM has no syslog of its own.
# Built by ../../docker-compose.otel-verify.yml, which passes the exact Ubuntu
# tag and the exact rsyslog package version as build arguments.

ARG UBUNTU_TAG
FROM ubuntu:${UBUNTU_TAG}

ARG RSYSLOG_VERSION
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        "rsyslog=${RSYSLOG_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

# The package's default configuration, with one change: imklog off. A
# container has no kernel log to read (/proc/kmsg is not readable here), so the
# module only produces errors. The grep fails the build if the line was not
# there to comment out, so the sed cannot silently do nothing.
RUN sed -i 's/^\(module(load="imklog".*\)$/#\1/' /etc/rsyslog.conf \
 && grep -q '^#module(load="imklog"' /etc/rsyslog.conf

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
