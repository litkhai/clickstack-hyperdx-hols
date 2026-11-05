#!/usr/bin/env python3
"""Register a localhost test user (first run only) and create the PromQL source.

Credentials go to the gitignored .env as HYPERDX_EMAIL / HYPERDX_PASSWORD and are never
printed. The source uses the stack's own `Local ClickHouse` connection, which the image
creates at first start. Re-running is safe: it looks the source up by name first.
"""
import json
import secrets
import sys

import hdx

SOURCE_NAME = "PromQL metrics"


def main():
    env = hdx.load_env()
    api = f"http://localhost:{env['PROMQL_API_PORT']}"
    c = hdx.Http()

    if "HYPERDX_PASSWORD" not in env:
        email, password = "promql-lab@example.com", secrets.token_urlsafe(18) + "!A1"  # the form requires a special character
        st, body = c.call("POST", f"{api}/register/password",
                          form={"email": email, "password": password, "confirmPassword": password})
        if st not in (200, 302, 303):
            sys.exit(f"register failed: HTTP {st} {body[:200]}")
        hdx.append_env("HYPERDX_EMAIL", email)
        hdx.append_env("HYPERDX_PASSWORD", password)
        print("registered a local test user (credentials in .env)")
    else:
        st = hdx.login(c, env)
        if st != 200:
            sys.exit(f"login failed: HTTP {st}")

    st, body = c.call("GET", f"{api}/sources")
    sources = json.loads(body)
    for s in sources:
        if s["kind"] == "promql" and s["name"] == SOURCE_NAME:
            print("source exists:", s["id"])
            return
    st, body = c.call("GET", f"{api}/connections")
    conn = next(x for x in json.loads(body) if x["name"] == "Local ClickHouse")
    st, body = c.call("POST", f"{api}/sources", data={
        "kind": "promql", "name": SOURCE_NAME, "connection": conn["id"],
        "from": {"databaseName": "promql", "tableName": "metrics"},
        "timestampValueExpression": "timestamp",
    })
    if st != 200:
        sys.exit(f"source create failed: HTTP {st} {body[:300]}")
    print("source created:", json.loads(body)["id"])


if __name__ == "__main__":
    main()
