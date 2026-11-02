#!/usr/bin/env python3
"""Mine log patterns the way HyperDX 2.39.1 Event Patterns does, outside the UI.

What HyperDX does (read from @hyperdx/app@2.39.1, packages/app/src/hooks/usePatterns.tsx
and components/PatternTable.tsx):
  - samples 10,000 rows of the current search: ORDER BY rand() LIMIT 10000
  - mines them in the browser with drain3 0.9.11 (Pyodide), default TemplateMinerConfig
    plus drain_extra_delimiters = every ASCII punctuation character
  - groups rows by cluster id, shows the last row's template rebuilt over its original
    text (reconstructTemplate), and scales the sample count to the total ("~N")

This script repeats those steps against ClickHouse over HTTP and prints, per pattern:
estimated count, sample count, the template as HyperDX shows it, and the RE2 regex that
matches exactly that template (template_to_regex below; sql/02-functions.sql has the same
conversion as a SQL function).

    uv run --with drain3==0.9.11 bin/mine_patterns.py [--where SQL] [--samples 10000] [--json]

Connection: CH_URL, CH_USER, CH_PASSWORD (as in _base/.env.example).
"""
import argparse
import base64
import json
import os
import re
import string
import sys
import urllib.parse
import urllib.request

LAB_FILTER = "ResourceAttributes['lab'] = 'log-pattern-exclusion'"


def query(sql):
    url = os.environ.get("CH_URL", "http://localhost:8123").rstrip("/") + "/"
    req = urllib.request.Request(url, data=sql.encode(), method="POST")
    user = os.environ.get("CH_USER", "default")
    token = base64.b64encode(f"{user}:{os.environ.get('CH_PASSWORD', '')}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode()


def reconstruct_template(original_log, template_mined):
    """Port of packages/app/src/components/Patterns/reconstructTemplate.ts (2.39.1)."""
    normalized = re.sub(r"\s+", " ", original_log)
    tokens = [t for t in template_mined.split(" ") if t]
    if not tokens:
        return normalized
    out, idx = [], 0
    for m in re.finditer(r"([A-Za-z0-9]+)|([^A-Za-z0-9]+)", normalized):
        if m.group(1) is not None:
            out.append(tokens[idx] if idx < len(tokens) else m.group(1))
            idx += 1
        else:
            out.append(m.group(2))
    return "".join(out)


RE2_SPECIAL = set("\\.^$|?*+()[]{}")


def template_to_regex(template):
    """Whole-message RE2 regex for a template as HyperDX shows it.

    reconstructTemplate puts <*> only where a whole [A-Za-z0-9]+ run was, and keeps every
    separator from the original row with whitespace squeezed to one space. So <*> becomes
    [A-Za-z0-9]+, a space becomes \\s+, everything else is literal. Anchored at both ends,
    so a sibling pattern with the same opening words does not match.
    """
    parts = template.split("<*>")
    lit = []
    for part in parts:
        esc = "".join("\\" + c if c in RE2_SPECIAL else c for c in part)
        lit.append(esc.replace(" ", r"\s+"))
    return "^" + "[A-Za-z0-9]+".join(lit) + "$"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--where", default=LAB_FILTER, help="SQL condition for the sample (default: this lab's rows)")
    ap.add_argument("--samples", type=int, default=10000)
    ap.add_argument("--json", action="store_true", help="one JSON object per pattern")
    args = ap.parse_args()

    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig

    total = int(query(f"SELECT count() FROM default.otel_logs WHERE {args.where} FORMAT TSV").strip())
    rows = [json.loads(line) for line in query(
        f"SELECT Body FROM default.otel_logs WHERE {args.where} "
        f"ORDER BY rand() DESC LIMIT {args.samples} FORMAT JSONEachRow").splitlines() if line]

    config = TemplateMinerConfig()
    config.drain_extra_delimiters = list(string.punctuation)
    miner = TemplateMiner(None, config)

    groups = {}
    for row in rows:
        r = miner.add_log_message(row["Body"])
        groups.setdefault(r["cluster_id"], []).append((row["Body"], r["template_mined"]))

    multiplier = total / len(rows) if rows else 1
    out = []
    for cid, members in groups.items():
        body, mined = members[-1]
        template = reconstruct_template(body, mined)
        out.append({
            "cluster_id": cid,
            "est_count": max(round(len(members) * multiplier), 1),
            "sample_count": len(members),
            "template": template,
            "regex": template_to_regex(template),
        })
    out.sort(key=lambda p: -p["sample_count"])

    print(f"# {len(rows)} sampled of {total} rows, {len(out)} patterns", file=sys.stderr)
    for p in out:
        if args.json:
            print(json.dumps(p, ensure_ascii=False))
        else:
            print(f"~{p['est_count']}\t{p['sample_count']}\t{p['template']}")


if __name__ == "__main__":
    main()
