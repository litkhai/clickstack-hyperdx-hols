"""Alert webhook receiver: appends {received_at, path, body} per POST to /out/webhook.jsonl."""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n).decode(errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw
        with open("/out/webhook.jsonl", "a") as f:
            f.write(json.dumps({"received_at": time.time(), "path": self.path, "body": body}) + "\n")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", 9000), H).serve_forever()
