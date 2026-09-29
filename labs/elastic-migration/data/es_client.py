#!/usr/bin/env python3
"""One Elasticsearch client for every tool here: auth, TLS, and nothing else.

Security is on by default in Elasticsearch 8.x, so a tool that cannot
authenticate cannot be pointed at a real cluster -- and the workaround people
reach for is disabling security on the source, or putting credentials in a URL
where they end up in shell history and error messages. So this is shared
rather than duplicated four times: one place where the credential handling is
either right or wrong.

Every tool in this directory calls add_arguments() and configure(), then
request(). Credentials come from the environment by default:

    ES_URL, ES_USER, ES_PASSWORD        basic auth
    ES_API_KEY                          the `encoded` value from
                                        POST /_security/api_key
    ES_CA_CERT                          PEM bundle for a private CA -- 8.x
                                        generates one on first start
    ES_INSECURE=1                       skip certificate verification

Rules this enforces, each because the alternative is worse:

  * basic auth and an API key together is an error, not a precedence rule. A
    tool that silently picks one will be debugged against the wrong identity.
  * --es-insecure warns on every single call. Skipping verification is a
    decision that should stay visible for as long as it is in effect.
  * credentials are never echoed, and userinfo is stripped from any URL these
    tools print or put in an error message. `https://user:pass@host` in a
    traceback is a leaked password.

Needs only Python 3's standard library.
"""
import base64
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

_state = {"headers": {}, "context": None, "insecure": False, "described": False}


def child_env(args, base=None):
    """Credentials for a subprocess, in its environment and not its argv.

    A password in a command line is visible in `ps` to every user on the
    machine and lands in whatever logs the command. Every tool here reads
    these variables as its defaults, so a child process ends up on the same
    connection without the credential ever appearing in an argument list.
    """
    env = dict(os.environ if base is None else base)
    for name, value in (("ES_USER", getattr(args, "es_user", None)),
                        ("ES_PASSWORD", getattr(args, "es_password", None)),
                        ("ES_API_KEY", getattr(args, "es_api_key", None)),
                        ("ES_CA_CERT", getattr(args, "es_ca_cert", None))):
        if value:
            env[name] = value
    if getattr(args, "es_insecure", False):
        env["ES_INSECURE"] = "1"
    return env


def add_arguments(parser):
    """Add the Elasticsearch credential and TLS arguments to a tool's parser.

    Each tool keeps its own URL argument -- they are not all spelled --url --
    and passes it to configure(). Credential defaults come from the
    environment so one never has to appear in a command line, which is where
    shell history and process listings read from.
    """
    parser.add_argument("--es-user", default=os.environ.get("ES_USER"),
                        help="basic auth user (env ES_USER)")
    parser.add_argument("--es-password", default=os.environ.get("ES_PASSWORD"),
                        help="basic auth password (env ES_PASSWORD; prefer the env var)")
    parser.add_argument("--es-api-key", default=os.environ.get("ES_API_KEY"),
                        help="API key, the `encoded` value from POST /_security/api_key "
                             "(env ES_API_KEY)")
    parser.add_argument("--es-ca-cert", default=os.environ.get("ES_CA_CERT"),
                        help="PEM bundle for the cluster's CA (env ES_CA_CERT). 8.x "
                             "generates one at config/certs/http_ca.crt")
    parser.add_argument("--es-insecure", action="store_true",
                        default=os.environ.get("ES_INSECURE", "") not in ("", "0", "false"),
                        help="skip TLS verification (env ES_INSECURE=1). Warns on every "
                             "call, on purpose")
    return parser


def redact(url):
    """A URL safe to print: no user:password@ left in it."""
    return re.sub(r"//[^/@]*@", "//<credentials>@", url or "")


def configure(args, url, announce=True):
    """Validate and store the connection settings. Returns a printable summary.

    Called once per process, before any request. The summary names the auth
    mode and never the credential, so it is safe in logs and in a PR body.
    """
    user = getattr(args, "es_user", None)
    password = getattr(args, "es_password", None)
    api_key = getattr(args, "es_api_key", None)
    ca_cert = getattr(args, "es_ca_cert", None)
    insecure = bool(getattr(args, "es_insecure", False))

    if api_key and (user or password):
        raise SystemExit(
            "both an API key and basic auth were given (ES_API_KEY and ES_USER/ES_PASSWORD).\n"
            "Pick one: a tool that silently prefers one of them gets debugged against the "
            "wrong identity.")
    if user and not password:
        raise SystemExit("--es-user was given without --es-password (or ES_PASSWORD)")
    if password and not user:
        raise SystemExit("--es-password was given without --es-user (or ES_USER)")
    if re.match(r"^[a-z]+://[^/@]*@", url or ""):
        raise SystemExit(
            f"credentials in the URL ({redact(url)}).\n"
            "Pass them as ES_USER/ES_PASSWORD or ES_API_KEY instead: a URL ends up in shell "
            "history, process listings and error messages.")

    headers = {}
    mode = "no authentication"
    if api_key:
        headers["Authorization"] = "ApiKey " + api_key
        mode = "API key"
    elif user:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = "Basic " + token
        mode = f"basic auth as {user}"

    context = None
    tls = ""
    if url.startswith("https://"):
        if insecure:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            tls = ", TLS NOT VERIFIED"
        elif ca_cert:
            if not os.path.exists(ca_cert):
                raise SystemExit(f"--es-ca-cert {ca_cert} does not exist")
            context = ssl.create_default_context(cafile=ca_cert)
            tls = f", TLS verified against {ca_cert}"
        else:
            context = ssl.create_default_context()
            tls = ", TLS verified against the system trust store"
    elif ca_cert or insecure:
        # Saying nothing here would let someone believe a TLS option took
        # effect on a plain-http URL.
        tls = " (TLS options ignored: the URL is http)"

    _state.update({"headers": headers, "context": context, "insecure": insecure and
                   url.startswith("https://")})
    summary = f"Elasticsearch {redact(url)} -- {mode}{tls}"
    if announce and not _state["described"]:
        print(summary, file=sys.stderr)
        _state["described"] = True
    if _state["insecure"]:
        print("WARNING: --es-insecure: certificates are not verified. Use ES_CA_CERT with "
              "the cluster's CA (8.x writes one to config/certs/http_ca.crt) before this "
              "touches anything that matters.", file=sys.stderr)
    return summary


def request(base_url, method, path, body=None, timeout=60, ndjson=False):
    """One Elasticsearch request. Returns the decoded body as bytes.

    Kept deliberately thin: each tool already formats its own errors, and the
    only thing that has to be identical across them is the credential and TLS
    handling.
    """
    data = None
    headers = dict(_state["headers"])
    if body is not None:
        if isinstance(body, (bytes, str)):
            data = body.encode("utf-8") if isinstance(body, str) else body
            headers["Content-Type"] = ("application/x-ndjson" if ndjson
                                       else "application/json")
        else:
            import json as _json
            data = _json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers,
                                 method=method)
    kwargs = {"timeout": timeout}
    if _state["context"] is not None:
        kwargs["context"] = _state["context"]
    with urllib.request.urlopen(req, **kwargs) as resp:
        return resp.status, resp.read()


def hint(error):
    """A line to add to an Elasticsearch error, when the cause is the credential.

    401 and 403 against a secured cluster are the two failures every tool here
    will see first, and "HTTP 401" on its own sends people to the wrong place.
    """
    code = getattr(error, "code", None)
    if code == 401:
        return ("401: Elasticsearch rejected the credential. Security is on by default in "
                "8.x -- set ES_USER/ES_PASSWORD, or ES_API_KEY.")
    if code == 403:
        # Verified against 8.17.0 by narrowing an API key until each tool
        # broke: _cat/indices alone needs both the cluster `monitor`
        # privilege (cluster:monitor/state) and the *index* `monitor`
        # privilege (indices:monitor/stats), which is the one everybody
        # leaves out.
        return ("403: authenticated, but this credential may not do that. The export path "
                "needs cluster privilege [monitor] and index privileges "
                "[read, view_index_metadata, monitor] on the indices being exported.")
    if isinstance(error, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(error):
        return ("TLS verification failed. 8.x generates its own CA: copy "
                "config/certs/http_ca.crt out of the cluster and pass it as ES_CA_CERT.")
    return ""


def cli_error(error):
    """Format a fatal Elasticsearch error for a command line, with the hint.

    A wrong password should print a sentence, not a traceback: the traceback
    sends the reader into this file, and the cause is in their environment.
    """
    if isinstance(error, urllib.error.HTTPError):
        try:
            detail = error.read().decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            detail = ""
        text = f"HTTP {error.code} {detail}".strip()
    elif isinstance(error, urllib.error.URLError):
        text = str(error.reason)
        error = error.reason
    else:
        text = str(error)
    # A tool that wraps an HTTPError in a RuntimeError keeps the original as
    # __cause__ (`raise ... from e`), and the status code lives there -- so
    # look at both or the 401 loses its explanation.
    tip = hint(error) or hint(getattr(error, "__cause__", None))
    return f"error: {text}" + (f"\n       {tip}" if tip else "")
