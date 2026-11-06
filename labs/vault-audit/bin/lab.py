"""Stdlib helpers shared by setup_hyperdx.py and check.py. No third-party packages.

Local state lives in the gitignored .run/ (mode 700): the Vault unseal key and root token written
by bin/vault_ha.sh and the HyperDX test user written by setup_hyperdx.py. Values are never printed.
"""
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(LAB, ".run")
PORT = {k: int(os.environ.get(f"VAULTLAB_{k}_PORT", d)) for k, d in
        (("UI", 48080), ("API", 48000), ("CH_HTTP", 48123),
         ("VAULT1", 48201), ("VAULT2", 48202), ("VAULT3", 48203))}
NODES = {"vault-1": PORT["VAULT1"], "vault-2": PORT["VAULT2"], "vault-3": PORT["VAULT3"]}
# Local by default. For Managed ClickStack, set CH_HTTP_URL=https://<service>.clickhouse.cloud:8443
# and the same CH_USER / CH_PASSWORD the Collector uses (.env.example).
CLOUD = bool(os.environ.get("CH_HTTP_URL"))
CH = os.environ.get("CH_HTTP_URL") or f"http://localhost:{PORT['CH_HTTP']}"
API = f"http://localhost:{PORT['API']}"
CH_AUTH = (f"{os.environ.get('CH_USER', '')}:{os.environ.get('CH_PASSWORD', '')}" if CLOUD
           else "api:api")  # published dev user of the clickstack-all-in-one image
DB = os.environ.get("CH_DATABASE", "vault_audit")


def read_run(name):
    path = os.path.join(RUN, name)
    return open(path).read().strip() if os.path.exists(path) else None


def write_run(name, value):
    os.makedirs(RUN, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(RUN, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


def root_token():
    return json.loads(read_run("vault-init.json"))["root_token"]


def sql(q):
    """Run one query over ClickHouse HTTP and return the TSV body (stripped)."""
    req = urllib.request.Request(f"{CH}/?default_format=TSV", data=q.encode(), method="POST",
                                 headers={"Authorization": "Basic " + base64.b64encode(CH_AUTH.encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ClickHouse HTTP {e.code}: {e.read().decode()[:300]}") from None


def wait_for(fn, timeout, step=3):
    """Call fn() until it returns a truthy value or timeout seconds pass; return the last value."""
    end = time.time() + timeout
    while True:
        try:
            v = fn()
        except RuntimeError:
            v = None
        if v or time.time() > end:
            return v
        time.sleep(step)


def vault(method, path, token=None, data=None, node="vault-1"):
    """Vault HTTP API on one node's host port. Returns (status, parsed JSON or None)."""
    h = {"X-Vault-Token": token} if token else {}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{NODES[node]}/v1/{path}", data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.strip() else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (json.loads(text) if text.strip().startswith("{") else None)
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return 0, None


def health(node):
    """200 active, 429 standby, 503 sealed, 501 uninitialised, 0 down (sys/health)."""
    return vault("GET", "sys/health", node=node)[0]


def active_node():
    return next((n for n in NODES if health(n) == 200), None)


def audit_hash(value, token):
    """HMAC of a value with the file audit device's salt -- what the audit log holds for it."""
    st, body = vault("POST", "sys/audit-hash/file", token, {"input": value}, node=active_node())
    if st != 200:
        raise RuntimeError(f"sys/audit-hash: HTTP {st}")
    return body["data"]["hash"]


def audit_lines():
    """Total lines across the three nodes' audit files (a node that never was active has none)."""
    total = 0
    for n in NODES:
        r = compose("exec", "-T", n, "sh", "-c", "cat /vault/logs/audit.log* 2>/dev/null | wc -l")
        total += int(r.stdout.strip() or 0)
    return total


def compose(*args, stdin=None):
    return subprocess.run(["docker", "compose", *args], cwd=LAB, input=stdin, capture_output=True, text=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Login answers 302 to FRONTEND_URL, which is not this lab's port."""

    def redirect_request(self, *a, **k):
        return None


class HyperDX:
    """HyperDX API client. Keeps session cookies by hand: HyperDX sets `Domain=localhost`,
    which Python's cookiejar refuses."""

    def __init__(self):
        self.cookies = {}
        self.op = urllib.request.build_opener(_NoRedirect)

    def call(self, method, path, data=None, form=None):
        h, body = {}, None
        if data is not None:
            body, h["content-type"] = json.dumps(data).encode(), "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        req = urllib.request.Request(API + path, data=body, headers=h, method=method)
        try:
            with self.op.open(req, timeout=120) as r:
                resp, status, text = r, r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            resp, status, text = e, e.code, e.read().decode()
        for sc in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = sc.partition("=")
            self.cookies[name] = rest.split(";", 1)[0]
        return status, text

    def json(self, method, path, data=None):
        st, text = self.call(method, path, data=data)
        if st != 200:
            raise RuntimeError(f"HyperDX {method} {path}: HTTP {st} {text[:300]}")
        return json.loads(text) if text.strip() else None  # PUT /sources answers 200 with no body

    def login(self):
        user = json.loads(read_run("hyperdx-user") or "{}")
        if not user:
            raise RuntimeError("no HyperDX user in .run/ -- run bin/setup_hyperdx.py first")
        st, _ = self.call("POST", "/login/password", form={"email": user["email"], "password": user["password"]})
        if st not in (200, 302, 303) or self.call("GET", "/me")[0] != 200:  # a wrong password also redirects
            raise RuntimeError(f"HyperDX login failed (HTTP {st})")
