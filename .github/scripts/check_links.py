#!/usr/bin/env python3
"""Report relative markdown links that do not resolve.

Run from the repository root:  python3 .github/scripts/check_links.py
Exits non-zero when a link is broken, so CI fails on it.
"""
import pathlib
import re
import subprocess
import sys

LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "#", "data:")


def main() -> int:
    root = pathlib.Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    files = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.split()

    broken = []
    for rel in files:
        path = root / rel
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, target in LINK.findall(line):
                if target.startswith(SKIP_SCHEMES):
                    continue
                clean = target.split("#", 1)[0].split("?", 1)[0]
                if not clean:
                    continue
                if not (path.parent / clean).resolve().exists():
                    broken.append((rel, lineno, label[:40], target))

    for rel, lineno, label, target in broken:
        print(f"{rel}:{lineno}  [{label}] -> {target}")

    if broken:
        print(f"\n{len(broken)} broken relative links in "
              f"{len({b[0] for b in broken})} files")
        return 1
    print(f"OK: every relative link in {len(files)} markdown files resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
