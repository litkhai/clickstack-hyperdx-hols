"""Small stdlib helpers shared by setup_hyperdx.py and check.py. No third-party packages."""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(HERE, ".env")


def load_env():
    env = {}
    for path in (os.path.join(HERE, ".env.example"), ENV_FILE):
        if os.path.exists(path):
            for line in open(path):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
    env.update({k: v for k, v in os.environ.items() if k.startswith(("PROMQL_", "HYPERDX_"))})
    return env


def append_env(key, value):
    """Add KEY=value to .env (gitignored, mode 600). Never printed."""
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(f"{key}={value}\n")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Login answers 302 to FRONTEND_URL (localhost:8080), which is not this lab's port."""

    def redirect_request(self, *a, **k):
        return None


class Http:
    """Keeps session cookies by hand: HyperDX sets `Domain=localhost`, which Python's cookiejar
    refuses, and a cookie without a port also works on both the API and the UI port."""

    def __init__(self):
        self.cookies = {}
        self.op = urllib.request.build_opener(_NoRedirect)

    def call(self, method, url, data=None, form=None, headers=None, timeout=120):
        h = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            h["content-type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with self.op.open(req, timeout=timeout) as r:
                resp, status = r, r.status
                text = r.read().decode()
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
            text = e.read().decode()
        for sc in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = sc.partition("=")
            self.cookies[name] = rest.split(";", 1)[0]
        return status, text


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def login(http_client, env):
    api = f"http://localhost:{env['PROMQL_API_PORT']}"
    st, body = http_client.call("POST", f"{api}/login/password",
                                form={"email": env["HYPERDX_EMAIL"], "password": env["HYPERDX_PASSWORD"]})
    if st not in (200, 302, 303):
        return st
    # a wrong password also answers with a redirect: only /me proves the session
    return http_client.call("GET", f"{api}/me")[0]
