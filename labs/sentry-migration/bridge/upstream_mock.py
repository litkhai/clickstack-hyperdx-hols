#!/usr/bin/env python3
"""Stand-in for the existing Sentry during a parallel run.

It accepts what the bridge tees to it and counts envelope items by type, so a
run can compare "what the old system received" with what reached ClickStack.
GET /counts returns the totals. Standard library only.
"""
import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COUNTS = {}
LOCK = threading.Lock()


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


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        with LOCK:
            body = json.dumps(COUNTS).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = read_body(self)
        if self.path.startswith("/webhook"):
            # HyperDX alert webhook lands here in the lab
            with LOCK:
                COUNTS["webhook"] = COUNTS.get("webhook", 0) + 1
                COUNTS["last_webhook"] = raw.decode(errors="replace")[:2000]
            self.send_response(200)
            self.end_headers()
            return
        if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        kinds = []
        if "/minidump/" in self.path:
            kinds = ["minidump"]
        else:
            lines = raw.split(b"\n")
            i = 1
            while i < len(lines):
                line = lines[i].strip()
                i += 1
                if not line:
                    continue
                try:
                    ih = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ih, dict) and "type" in ih:
                    kinds.append(ih["type"])
                    if "length" not in ih:
                        i += 1
        with LOCK:
            COUNTS["requests"] = COUNTS.get("requests", 0) + 1
            for k in kinds:
                COUNTS[k] = COUNTS.get(k, 0) + 1
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id":"upstream-mock"}')


ThreadingHTTPServer(("0.0.0.0", 9900), H).serve_forever()
