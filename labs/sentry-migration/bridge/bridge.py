#!/usr/bin/env python3
"""Sentry envelope -> OTLP logs bridge.

A client keeps its Sentry SDK and changes only the DSN host to this bridge.
The bridge accepts what the SDK sends and writes it to an OTLP/HTTP logs
endpoint (the ClickStack collector):

  POST /api/<project_id>/envelope/   errors, sessions, session aggregates,
                                     attachments (minidumps) -- the SDK default
  POST /api/<project_id>/store/      legacy single-event JSON
  POST /api/<project_id>/minidump/   Crashpad / Breakpad multipart upload

Each Sentry item becomes one OTel log record:

  event            body "<type>: <value>", severity from level, exception.*
                   attributes, a grouping fingerprint over in-app frames
  session          one record per update (status ok/exited/crashed/abnormal)
  sessions         one record per aggregate bucket
  attachment       a receipt (name, size, sha256); the file is kept on disk
  anything else    counted in /metrics, not forwarded

Optionally every request body is also forwarded unchanged to UPSTREAM_URL
(the existing Sentry), so both systems can be compared during a migration.

Standard library only. Configuration is environment variables:

  OTLP_LOGS_URL     http://clickstack:4318/v1/logs
  OTLP_HEADERS      "authorization=<key>,x-other=..." (optional)
  PROJECTS          "<project_id>:<public_key>:<service.name>,..." -- the DSNs
                    this bridge accepts. A request whose key does not match
                    gets 401.
  UPSTREAM_URL      e.g. https://o0.ingest.sentry.io (optional tee)
  ATTACHMENT_DIR    /data/attachments
  LISTEN            0.0.0.0:9800
"""

import email.parser
import email.policy
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OTLP_LOGS_URL = os.environ.get("OTLP_LOGS_URL", "http://clickstack:4318/v1/logs")
OTLP_HEADERS = dict(
    kv.split("=", 1) for kv in os.environ.get("OTLP_HEADERS", "").split(",") if "=" in kv
)
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "").rstrip("/")
ATTACHMENT_DIR = os.environ.get("ATTACHMENT_DIR", "/data/attachments")
LISTEN = os.environ.get("LISTEN", "0.0.0.0:9800")

PROJECTS = {}
for spec in os.environ.get("PROJECTS", "").split(","):
    if spec.count(":") == 2:
        pid, key, svc = spec.split(":")
        PROJECTS[pid] = {"key": key, "service": svc}

STATS = {}
STATS_LOCK = threading.Lock()


def count(name, n=1):
    with STATS_LOCK:
        STATS[name] = STATS.get(name, 0) + n


SEVERITY = {
    "debug": (5, "DEBUG"),
    "info": (9, "INFO"),
    "warning": (13, "WARN"),
    "error": (17, "ERROR"),
    "fatal": (21, "FATAL"),
}


# ---------------------------------------------------------------- OTLP helpers

def any_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if v is None:
        return {"stringValue": ""}
    if isinstance(v, (dict, list)):
        return {"stringValue": json.dumps(v, ensure_ascii=False, sort_keys=True)}
    return {"stringValue": str(v)}


def attrs(d):
    return [{"key": k, "value": any_value(v)} for k, v in d.items() if v is not None and v != ""]


def ts_nanos(value):
    """Sentry timestamps are RFC 3339 strings or float seconds."""
    if value is None:
        return str(time.time_ns())
    if isinstance(value, (int, float)):
        return str(int(value * 1e9))
    s = str(value).replace("Z", "+00:00")
    try:
        from datetime import datetime

        return str(int(datetime.fromisoformat(s).timestamp() * 1e9))
    except ValueError:
        return str(time.time_ns())


def export(resource, records):
    """Send records sharing one resource to the OTLP logs endpoint."""
    if not records:
        return
    body = {
        "resourceLogs": [
            {
                "resource": {"attributes": attrs(resource)},
                "scopeLogs": [
                    {"scope": {"name": "sentry-bridge", "version": "1"}, "logRecords": records}
                ],
            }
        ]
    }
    req = urllib.request.Request(
        OTLP_LOGS_URL,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **OTLP_HEADERS},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        count("otlp_records_sent", len(records))
    except urllib.error.URLError as e:
        count("otlp_errors")
        print(f"otlp export failed: {e}", file=sys.stderr, flush=True)


# ------------------------------------------------------------- Sentry mapping

def frames_of(exc):
    st = exc.get("stacktrace") or {}
    return st.get("frames") or []


def format_stack(exceptions):
    lines = []
    for exc in exceptions:
        lines.append(f"{exc.get('type', 'Error')}: {exc.get('value', '')}")
        # Sentry orders frames oldest first; print innermost first like a traceback.
        for f in reversed(frames_of(exc)):
            where = f.get("filename") or f.get("abs_path") or f.get("module") or "?"
            mark = "" if f.get("in_app", True) else " [lib]"
            addr = f" {f['instruction_addr']}" if f.get("instruction_addr") else ""
            lines.append(f"  at {f.get('function', '?')} ({where}:{f.get('lineno', '?')}){addr}{mark}")
    return "\n".join(lines)


def fingerprint(event, exceptions):
    """A grouping key in the spirit of Sentry's default: exception type plus
    the in-app frames' module/function, ignoring line numbers. An explicit
    `fingerprint` on the event wins, as it does in Sentry."""
    explicit = event.get("fingerprint")
    if explicit and explicit != ["{{ default }}"]:
        basis = "|".join(explicit)
    elif exceptions:
        exc = exceptions[-1]
        frames = [f for f in frames_of(exc) if f.get("in_app")] or frames_of(exc)
        basis = exc.get("type", "") + "|" + "|".join(
            f"{f.get('module') or f.get('filename', '')}:{f.get('function', '')}" for f in frames[-5:]
        )
    else:
        msg = event.get("logentry", {}).get("message") or event.get("message") or ""
        basis = re.sub(r"\d+", "N", str(msg))
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def resource_for(project, event):
    contexts = event.get("contexts") or {}
    os_ctx = contexts.get("os") or {}
    device = contexts.get("device") or {}
    app = contexts.get("app") or {}
    sdk = event.get("sdk") or {}
    return {
        "service.name": (event.get("tags") or {}).get("service") or project["service"],
        "service.version": event.get("release"),
        "deployment.environment": event.get("environment"),
        "os.name": os_ctx.get("name"),
        "os.version": os_ctx.get("version"),
        "device.model.identifier": device.get("model"),
        "app.build": app.get("app_build"),
        "telemetry.sdk.name": sdk.get("name"),
        "telemetry.sdk.version": sdk.get("version"),
        "sentry.platform": event.get("platform"),
    }


def tags_dict(event):
    tags = event.get("tags") or {}
    if isinstance(tags, list):  # some SDKs send [[k, v], ...]
        tags = {k: v for k, v in tags}
    return tags


def event_record(project, event, trace_id=None):
    exceptions = (event.get("exception") or {}).get("values") or []
    level = event.get("level") or ("error" if exceptions else "info")
    sev_num, sev_text = SEVERITY.get(level, (17, "ERROR"))
    last = exceptions[-1] if exceptions else {}
    mechanism = last.get("mechanism") or {}
    if exceptions:
        body = f"{last.get('type', 'Error')}: {last.get('value', '')}"
    else:
        body = (event.get("logentry") or {}).get("formatted") or (event.get("logentry") or {}).get(
            "message"
        ) or event.get("message") or "sentry event"
    user = event.get("user") or {}
    a = {
        "event.name": "sentry.error",
        "sentry.event_id": event.get("event_id"),
        "sentry.level": level,
        "sentry.fingerprint": fingerprint(event, exceptions),
        "sentry.handled": mechanism.get("handled"),
        "sentry.mechanism": mechanism.get("type"),
        "sentry.transaction": event.get("transaction"),
        "exception.type": last.get("type"),
        "exception.message": last.get("value"),
        "exception.stacktrace": format_stack(exceptions) if exceptions else None,
        # user.id only; Sentry user context can carry email/IP, which stays out.
        "user.id": user.get("id"),
    }
    for k, v in tags_dict(event).items():
        a[f"sentry.tag.{k}"] = v
    rec = {
        "timeUnixNano": ts_nanos(event.get("timestamp")),
        "observedTimeUnixNano": str(time.time_ns()),
        "severityNumber": sev_num,
        "severityText": sev_text,
        "body": {"stringValue": body},
        "attributes": attrs(a),
    }
    trace = ((event.get("contexts") or {}).get("trace") or {}).get("trace_id") or trace_id
    if trace and re.fullmatch(r"[0-9a-f]{32}", trace):
        rec["traceId"] = trace
    return rec


def session_record(project, s):
    attrs_ = s.get("attrs") or {}
    status = s.get("status", "ok")
    sev = (13, "WARN") if status in ("crashed", "abnormal") else (9, "INFO")
    resource = {
        "service.name": project["service"],
        "service.version": attrs_.get("release"),
        "deployment.environment": attrs_.get("environment"),
    }
    rec = {
        "timeUnixNano": ts_nanos(s.get("timestamp")),
        "observedTimeUnixNano": str(time.time_ns()),
        "severityNumber": sev[0],
        "severityText": sev[1],
        "body": {"stringValue": f"session {status}"},
        "attributes": attrs(
            {
                "event.name": "sentry.session",
                "sentry.session.id": s.get("sid"),
                "sentry.session.status": status,
                "sentry.session.init": s.get("init"),
                "sentry.session.errors": s.get("errors", 0),
                "sentry.session.duration": s.get("duration"),
                "sentry.session.started": s.get("started"),
                "user.id": s.get("did"),
            }
        ),
    }
    return resource, rec


def aggregate_records(project, agg):
    attrs_ = agg.get("attrs") or {}
    resource = {
        "service.name": project["service"],
        "service.version": attrs_.get("release"),
        "deployment.environment": attrs_.get("environment"),
    }
    out = []
    for b in agg.get("aggregates") or []:
        out.append(
            {
                "timeUnixNano": ts_nanos(b.get("started")),
                "observedTimeUnixNano": str(time.time_ns()),
                "severityNumber": 9,
                "severityText": "INFO",
                "body": {"stringValue": "session aggregate"},
                "attributes": attrs(
                    {
                        "event.name": "sentry.sessions",
                        "sentry.sessions.exited": b.get("exited", 0),
                        "sentry.sessions.errored": b.get("errored", 0),
                        "sentry.sessions.crashed": b.get("crashed", 0),
                        "sentry.sessions.abnormal": b.get("abnormal", 0),
                    }
                ),
            }
        )
    return resource, out


def attachment_record(project, header, payload, event_id, filename=None):
    os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    name = filename or header.get("filename") or "attachment.bin"
    path = os.path.join(ATTACHMENT_DIR, f"{digest[:16]}-{os.path.basename(name)}")
    with open(path, "wb") as fh:
        fh.write(payload)
    kind = header.get("attachment_type", "event.attachment")
    return {
        "timeUnixNano": str(time.time_ns()),
        "observedTimeUnixNano": str(time.time_ns()),
        "severityNumber": 17 if kind == "event.minidump" else 9,
        "severityText": "ERROR" if kind == "event.minidump" else "INFO",
        "body": {"stringValue": f"{kind} {name} ({len(payload)} bytes)"},
        "attributes": attrs(
            {
                "event.name": "sentry.attachment",
                "sentry.event_id": event_id,
                "sentry.attachment.type": kind,
                "sentry.attachment.name": name,
                "sentry.attachment.size": len(payload),
                "sentry.attachment.sha256": digest,
                "sentry.attachment.path": path,
            }
        ),
    }


# ----------------------------------------------------------- envelope parsing

def parse_envelope(raw):
    """Envelope = header line, then items: item-header line + payload.
    The payload length comes from the item header when given, else the
    payload runs to the next newline."""
    pos = raw.find(b"\n")
    if pos < 0:
        return json.loads(raw or b"{}"), []
    header = json.loads(raw[:pos] or b"{}")
    pos += 1
    items = []
    while pos < len(raw):
        nl = raw.find(b"\n", pos)
        if nl < 0:
            nl = len(raw)
        line = raw[pos:nl].strip()
        pos = nl + 1
        if not line:
            continue
        ih = json.loads(line)
        length = ih.get("length")
        if length is not None:
            payload = raw[pos : pos + length]
            pos += length
            if pos < len(raw) and raw[pos : pos + 1] == b"\n":
                pos += 1
        else:
            nl = raw.find(b"\n", pos)
            if nl < 0:
                nl = len(raw)
            payload = raw[pos:nl]
            pos = nl + 1
        items.append((ih, payload))
    return header, items


def handle_envelope(project, raw):
    header, items = parse_envelope(raw)
    event_id = header.get("event_id")
    trace_id = (header.get("trace") or {}).get("trace_id")
    count("envelopes")
    batches = []  # (resource, [records])
    for ih, payload in items:
        kind = ih.get("type")
        count(f"item.{kind}")
        if kind == "event":
            ev = json.loads(payload)
            batches.append((resource_for(project, ev), [event_record(project, ev, trace_id)]))
        elif kind == "session":
            r, rec = session_record(project, json.loads(payload))
            batches.append((r, [rec]))
        elif kind == "sessions":
            r, recs = aggregate_records(project, json.loads(payload))
            batches.append((r, recs))
        elif kind == "attachment":
            batches.append(
                ({"service.name": project["service"]}, [attachment_record(project, ih, payload, event_id)])
            )
        # transaction, client_report, profile, replay_*: counted, not forwarded
    for resource, recs in batches:
        export(resource, recs)
    return event_id


def handle_minidump(project, content_type, raw):
    """Crashpad/Breakpad upload: multipart with `upload_file_minidump` and
    optional `sentry` (JSON event) or `sentry[...]` form fields."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw
    )
    dump, fields = None, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        if name == "upload_file_minidump":
            dump = (part.get_filename() or "minidump.dmp", data)
        elif name:
            fields[name] = data.decode(errors="replace")
    count("minidumps")
    event = {}
    if "sentry" in fields:
        try:
            event = json.loads(fields["sentry"])
        except ValueError:
            pass
    for k, v in fields.items():
        m = re.fullmatch(r"sentry\[tags\]\[(.+)\]", k)
        if m:
            event.setdefault("tags", {})[m.group(1)] = v
        elif k in ("sentry[release]", "sentry[environment]"):
            event[k[7:-1]] = v
    event_id = event.get("event_id") or uuid.uuid4().hex
    event.setdefault("level", "fatal")
    event.setdefault("platform", "native")
    event.setdefault(
        "exception",
        {"values": [{"type": "Minidump", "value": "native crash (not symbolicated)",
                     "mechanism": {"type": "minidump", "handled": False}}]},
    )
    event["event_id"] = event_id
    resource = resource_for(project, event)
    recs = [event_record(project, event)]
    if dump:
        recs.append(attachment_record(project, {"attachment_type": "event.minidump"}, dump[1], event_id, dump[0]))
    export(resource, recs)
    return event_id


# ------------------------------------------------------------------- server

def sentry_key(handler, query):
    auth = handler.headers.get("X-Sentry-Auth", "")
    m = re.search(r"sentry_key=([0-9a-f]+)", auth)
    if m:
        return m.group(1)
    m = re.search(r"sentry_key=([0-9a-f]+)", query)
    return m.group(1) if m else None


def decode_body(handler, raw):
    enc = (handler.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        return zlib.decompress(raw)
    if enc in ("", "identity"):
        return raw
    raise ValueError(f"unsupported Content-Encoding {enc}")


def tee(path, handler, raw):
    if not UPSTREAM_URL:
        return
    headers = {k: v for k, v in handler.headers.items() if k.lower() not in ("host", "content-length", "transfer-encoding")}
    req = urllib.request.Request(UPSTREAM_URL + path, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        count("upstream_ok")
    except Exception as e:  # never fail the client because the tee failed
        count("upstream_errors")
        print(f"upstream tee failed: {e}", file=sys.stderr, flush=True)


def read_body(handler):
    """Content-Length body, or a chunked one (the Node SDK streams)."""
    if "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower():
        out = bytearray()
        while True:
            size = int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                handler.rfile.readline()  # trailing CRLF after the last chunk
                return bytes(out)
            out += handler.rfile.read(size)
            handler.rfile.readline()
    return handler.rfile.read(int(handler.headers.get("Content-Length") or 0))


ROUTE = re.compile(r"^/api/(\d+)/(envelope|store|minidump)/?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "sentry-bridge/1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"ok": True})
        if self.path == "/metrics":
            with STATS_LOCK:
                return self.reply(200, dict(STATS))
        self.reply(404, {"detail": "not found"})

    def do_POST(self):
        path, _, query = self.path.partition("?")
        m = ROUTE.match(path)
        if not m:
            return self.reply(404, {"detail": "not found"})
        pid, kind = m.groups()
        project = PROJECTS.get(pid)
        if not project or sentry_key(self, query) != project["key"]:
            count("rejected_auth")
            return self.reply(401, {"detail": "unknown project or key"})
        raw = read_body(self)
        tee(self.path, self, raw)
        try:
            if kind == "minidump":
                event_id = handle_minidump(project, self.headers.get("Content-Type", ""), raw)
            else:
                body = decode_body(self, raw)
                if kind == "store":
                    body = b'{}\n{"type":"event"}\n' + body
                event_id = handle_envelope(project, body)
        except Exception as e:
            count("parse_errors")
            print(f"parse failed: {e!r}", file=sys.stderr, flush=True)
            return self.reply(400, {"detail": str(e)})
        self.reply(200, {"id": event_id})


def main():
    host, port = LISTEN.rsplit(":", 1)
    print(f"sentry-bridge on {LISTEN} -> {OTLP_LOGS_URL}; projects {sorted(PROJECTS)}; "
          f"tee {'on' if UPSTREAM_URL else 'off'}", flush=True)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
