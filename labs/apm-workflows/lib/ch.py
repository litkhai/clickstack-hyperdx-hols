#!/usr/bin/env python3
"""Stdlib HTTPS client for the lab's ClickHouse Cloud service.

* Credentials come from an env file named by CH_ENV_FILE (set in the lab's own
  gitignored `.env`, or in the process environment).  Only CH_HOST, CH_USER and
  CH_PASSWORD are read from it; values are never printed.
* Every request goes to the lab database `apm_workflows`.  Any other database,
  and any write statement that names another database, is refused unless the
  caller passes `--database` explicitly (and even then the write guard keeps
  CREATE USER / GRANT and friends out).
* Query parameters: `{name:Type}` in the SQL plus `params={"name": value}`.

CLI (used by the bin/ scripts):
    ch.py apply FILE...                 run every statement of the SQL files
    ch.py query [--file F | SQL] [--param k=v ...] [--format TSV|JSON]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent.parent
LAB_DATABASE = "apm_workflows"
HTTPS_PORT = 8443


class ChError(Exception):
    """The server answered with an error (text is the server's message)."""


class ScopeError(Exception):
    """The statement or database is outside the lab's database."""


# ---------------------------------------------------------------- env loading

def parse_env_file(path):
    """Parse KEY=VALUE lines (optional `export`, quotes, comments)."""
    out = {}
    for raw in Path(path).expanduser().read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)
        out[key] = value
    return out


def load_env(lab_dir=LAB_DIR, environ=None):
    """Return {"CH_HOST", "CH_USER", "CH_PASSWORD"} (+ any lab settings).

    Lookup order for the env-file path: process env CH_ENV_FILE, then the lab's
    own `.env`.  Variables other than the three connection ones are ignored
    from the shared file (its CH_DATABASE belongs to another lab).
    """
    environ = os.environ if environ is None else environ
    lab_env = {}
    dotenv = Path(lab_dir) / ".env"
    if dotenv.is_file():
        lab_env = parse_env_file(dotenv)
    path = environ.get("CH_ENV_FILE") or lab_env.get("CH_ENV_FILE")
    if not path:
        raise ChError("CH_ENV_FILE is not set (put it in the lab's .env, see .env.example)")
    shared = parse_env_file(path)
    merged = {}
    for key in ("CH_HOST", "CH_USER", "CH_PASSWORD"):
        value = shared.get(key) or environ.get(key)
        if not value:
            raise ChError("%s is missing from the env file named by CH_ENV_FILE" % key)
        merged[key] = value
    return merged


# ------------------------------------------------------------------ SQL scope

def _blank_literals(sql):
    """Replace string literals and comments by spaces (keeps offsets)."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_statements(text):
    """Split a SQL script at `;` outside literals/comments; drop empties."""
    blank = _blank_literals(text)
    stmts, start = [], 0
    for i, c in enumerate(blank):
        if c == ";":
            stmts.append(text[start:i])
            start = i + 1
    stmts.append(text[start:])
    return [s.strip() for s in stmts if _blank_literals(s).strip()]


_NAME = r"((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][\w$]*))?)"
_WRITE_TARGETS = [
    # DDL on objects
    re.compile(r"^\s*(?:CREATE|ATTACH)\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?"
               r"(?:MATERIALIZED\s+VIEW|LIVE\s+VIEW|WINDOW\s+VIEW|VIEW|TABLE|DICTIONARY|DATABASE)\s+"
               r"(?:IF\s+NOT\s+EXISTS\s+)?" + _NAME, re.I),
    re.compile(r"^\s*(?:DROP|DETACH|UNDROP|TRUNCATE)\s+(?:TEMPORARY\s+)?"
               r"(?:TABLE|VIEW|DICTIONARY|DATABASE)?\s*(?:IF\s+EXISTS\s+)?" + _NAME, re.I),
    re.compile(r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?" + _NAME, re.I),
    re.compile(r"^\s*OPTIMIZE\s+TABLE\s+" + _NAME, re.I),
    re.compile(r"^\s*RENAME\s+(?:TABLE|DATABASE)\s+" + _NAME, re.I),
    re.compile(r"^\s*EXCHANGE\s+TABLES\s+" + _NAME, re.I),
    re.compile(r"^\s*INSERT\s+INTO\s+(?:TABLE\s+)?(?:FUNCTION\s+)?" + _NAME, re.I),
    re.compile(r"^\s*DELETE\s+FROM\s+" + _NAME, re.I),
    re.compile(r"^\s*SYSTEM\s+(?:STOP|START|REFRESH|CANCEL|WAIT)\s+VIEW[S]?\s+" + _NAME, re.I),
]
_ALWAYS_REFUSED = re.compile(
    r"^\s*(?:GRANT|REVOKE|KILL|CREATE\s+(?:USER|ROLE|QUOTA|ROW\s+POLICY|SETTINGS\s+PROFILE|FUNCTION)|"
    r"ALTER\s+(?:USER|ROLE|QUOTA)|DROP\s+(?:USER|ROLE|QUOTA|FUNCTION)|SET\s+ROLE)\b", re.I)
_SYSTEM_ANY = re.compile(r"^\s*SYSTEM\b", re.I)
_SYSTEM_VIEW_OK = re.compile(r"^\s*SYSTEM\s+(?:STOP|START|REFRESH|CANCEL|WAIT)\s+VIEW[S]?\b", re.I)


def _unquote(ident):
    return ident.strip().strip("`")


def check_write_scope(sql, database=LAB_DATABASE):
    """Raise ScopeError if `sql` writes outside `database` (or does something a
    lab script never needs).  Reads are not restricted."""
    stmt = _blank_literals(sql)
    if _ALWAYS_REFUSED.match(stmt):
        raise ScopeError("statement type is not allowed in this lab: %s" % stmt.strip()[:60])
    if _SYSTEM_ANY.match(stmt) and not _SYSTEM_VIEW_OK.match(stmt):
        raise ScopeError("only SYSTEM STOP/START/REFRESH/CANCEL/WAIT VIEW is allowed: %s" % stmt.strip()[:60])
    for pattern in _WRITE_TARGETS:
        m = pattern.match(stmt)
        if not m:
            continue
        name = re.sub(r"\s+", "", m.group(1))
        parts = [_unquote(p) for p in name.split(".")]
        is_db_stmt = bool(re.match(r"^\s*(?:CREATE|DROP|DETACH|ATTACH|UNDROP|RENAME)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?DATABASE\b", stmt, re.I))
        if is_db_stmt:
            if parts[0] != database:
                raise ScopeError("database %r is not %r" % (parts[0], database))
        elif len(parts) == 2 and parts[0] != database:
            raise ScopeError("write to %s.%s is outside %r" % (parts[0], parts[1], database))
        return
    # SELECT/SHOW/EXISTS/DESCRIBE/EXPLAIN/WITH/SET: reads, nothing to check.


# ------------------------------------------------------------------- client

class Client:
    def __init__(self, host, user, password, database=LAB_DATABASE,
                 explicit_database=False, port=HTTPS_PORT, timeout=600):
        if database != LAB_DATABASE and not explicit_database:
            raise ScopeError(
                "database %r is not %r; pass --database explicitly to use it" % (database, LAB_DATABASE))
        self.host, self.user, self.password = host, user, password
        self.database, self.port, self.timeout = database, port, timeout
        self._ctx = ssl.create_default_context()

    def _url(self, params, settings, fmt, with_database):
        q = {}
        if with_database and self.database:
            q["database"] = self.database
        if fmt:
            q["default_format"] = fmt
        for k, v in (params or {}).items():
            q["param_" + k] = str(v)
        for k, v in (settings or {}).items():
            q[k] = str(v)
        return "https://%s:%d/?%s" % (self.host, self.port, urllib.parse.urlencode(q))

    def query(self, sql, params=None, fmt=None, settings=None, timeout=None):
        """Run one statement; return the response text."""
        check_write_scope(sql, self.database or LAB_DATABASE)
        # CREATE/DROP DATABASE run without the `database` parameter: the server refuses any request that names a
        # database that does not exist (UNKNOWN_DATABASE), so naming it would break the first install and a repeated
        # uninstall.
        bootstrap = bool(re.match(r"^\s*(?:CREATE|DROP)\s+DATABASE\b", _blank_literals(sql), re.I))
        req = urllib.request.Request(
            self._url(params, settings, fmt, with_database=not bootstrap),
            data=sql.encode("utf-8"), method="POST")
        token = ("%s:%s" % (self.user, self.password)).encode("utf-8")
        import base64
        req.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self._ctx) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise ChError(e.read().decode("utf-8", "replace").strip()) from None
        except urllib.error.URLError as e:
            raise ChError("connection failed: %s" % e.reason) from None

    def rows(self, sql, params=None, settings=None, timeout=None):
        """Run a SELECT and return a list of dicts (FORMAT JSON)."""
        text = self.query(sql, params=params, fmt="JSON", settings=settings, timeout=timeout)
        return json.loads(text)["data"] if text.strip() else []

    def apply_script(self, text, params=None, log=None, settings=None):
        for stmt in split_statements(text):
            self.query(stmt, params=params, settings=settings)
            if log:
                log(stmt)


def client_from_env(database=None, timeout=600, lab_dir=LAB_DIR, environ=None):
    env = load_env(lab_dir, environ)
    return Client(env["CH_HOST"], env["CH_USER"], env["CH_PASSWORD"],
                  database=database or LAB_DATABASE,
                  explicit_database=database is not None, timeout=timeout)


# ---------------------------------------------------------------------- CLI

def _first_line(stmt):
    for line in stmt.splitlines():
        line = line.strip()
        if line and not line.startswith("--"):
            return line[:100]
    return stmt[:100]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", help="use a database other than %s (explicit opt-in)" % LAB_DATABASE)
    ap.add_argument("--timeout", type=int, default=600)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_apply = sub.add_parser("apply", help="run the statements of SQL files")
    p_apply.add_argument("files", nargs="+")
    p_apply.add_argument("--param", action="append", default=[], metavar="K=V")
    p_apply.add_argument("--setting", action="append", default=[], metavar="K=V")
    p_query = sub.add_parser("query", help="run one statement or file, print the result")
    p_query.add_argument("sql", nargs="?")
    p_query.add_argument("--file")
    p_query.add_argument("--param", action="append", default=[], metavar="K=V")
    p_query.add_argument("--setting", action="append", default=[], metavar="K=V")
    p_query.add_argument("--format", default="TSVWithNames")
    args = ap.parse_args(argv)

    params = dict(p.split("=", 1) for p in args.param)
    settings = dict(p.split("=", 1) for p in args.setting)
    try:
        client = client_from_env(database=args.database, timeout=args.timeout)
        if args.cmd == "apply":
            for f in args.files:
                print("-- apply %s" % Path(f).name)
                client.apply_script(Path(f).read_text(), params=params, settings=settings,
                                    log=lambda s: print("   ok: " + _first_line(s)))
        else:
            sql = Path(args.file).read_text() if args.file else args.sql
            if not sql:
                ap.error("give SQL text or --file")
            for stmt in split_statements(sql):
                out = client.query(stmt, params=params, fmt=args.format, settings=settings)
                if out:
                    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    except (ChError, ScopeError, OSError) as e:
        msg = str(e)
        print("ch.py: %s" % (msg if len(msg) <= 2000 else msg[:2000] + " ...[%d chars cut]" % (len(msg) - 2000)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
