#!/usr/bin/env python3
# Generated from khai-workbench domains/clickhouse/tools/labs_json.py @e1e87f4 — edit there, not here (sync.py).
"""labs_json — write docs/labs.json, the labs this repository publishes to the notes site.

    tools/labs_json.py            write docs/labs.json
    tools/labs_json.py --check    fail if docs/labs.json is not what this would write

A lab publishes when its lab.yaml sets `web: true` (lab.schema.md, "Publishing keys").
Only those labs are written; with none, `labs` is an empty list. `generated` changes
only when the published list changes, so a re-run is a no-op and --check is exact.
A Pages builder imports build() and adds the result to its own output, passing a
page_url(path) that returns the lab's page URL or None.

Needs only Python 3 and git (for the repository name, from remote.origin.url).
"""
import argparse, datetime, json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = "docs/labs.json"
CATEGORIES = ("cloud", "feature", "case-study", "core-architecture",
              "third-party", "competition", "customer-story")
REQUIRED = ("title_ko", "summary_ko", "category")
# Order of the keys in each published entry, after `path`.
KEYS = ("title_ko", "summary_ko", "title_en", "summary_en", "category",
        "target", "tier", "clickhouse", "verified_on")


def load(path):
    """Same flat `key: value` reader as hol (no PyYAML needed)."""
    meta = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].rstrip()
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip().strip('"')
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip('"') for x in v[1:-1].split(",") if x.strip()]
            meta[k.strip()] = v
    return meta


def repo_slug(root):
    url = subprocess.run(["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                         capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        sys.exit("labs_json: cannot tell the GitHub repository from remote.origin.url %r" % url)
    return m.group(1)


def build(root=ROOT, page_url=None):
    """Return (text of docs/labs.json, number of published labs). Exits on a bad lab.yaml."""
    slug = repo_slug(root)
    blob = "https://github.com/%s/blob/main" % slug
    labs, errors = [], []
    for f in sorted(p for p in root.rglob("lab.yaml") if ".git" not in p.parts):
        path = f.parent.relative_to(root).as_posix()
        meta = load(f)
        web = meta.get("web", "false")
        if web not in ("true", "false"):
            errors.append("%s: web must be true or false, not %r" % (path, web))
        if meta.get("category") and meta["category"] not in CATEGORIES:
            errors.append("%s: unknown category %r" % (path, meta["category"]))
        if web != "true":
            continue
        missing = [k for k in REQUIRED if not meta.get(k)]
        if missing:
            errors.append("%s: web: true needs %s" % (path, ", ".join(missing)))
            continue
        lab = {"path": path}
        lab.update((k, meta[k]) for k in KEYS if isinstance(meta.get(k), str) and meta[k])
        if (f.parent / "README.md").exists():
            lab["readme_ko"] = "%s/%s/README.md#한국어" % (blob, path)
            lab["readme_en"] = "%s/%s/README.md#english" % (blob, path)
        url = page_url(path) if page_url else None
        if url:
            lab["pages"] = url
        labs.append(lab)
    if errors:
        sys.exit("labs_json: %s not written\n  %s" % (OUT, "\n  ".join(errors)))

    name = slug.split("/", 1)[1]
    try:
        prev = json.loads((root / OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    if prev.get("repo") == name and prev.get("labs") == labs and prev.get("generated"):
        generated = prev["generated"]
    else:
        generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"repo": name, "generated": generated, "labs": labs}
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n", len(labs)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if docs/labs.json is out of date")
    a = ap.parse_args()
    text, n = build()
    out = ROOT / OUT
    if a.check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            print("%s is out of date; run: tools/labs_json.py" % OUT)
            return 1
        print("OK: %s matches (%d published)" % (OUT, n))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("wrote %s: %d published" % (OUT, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
