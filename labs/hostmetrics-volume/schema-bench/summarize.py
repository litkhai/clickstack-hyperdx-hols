#!/usr/bin/env python3
"""Read clickhouse local's query output interleaved with --print-profile-events; one line per query."""
import re
import sys

cur, res = None, {}
for line in sys.stdin:
    m = re.match(r"^(Q\d [A-Z][^\t]*)\t(\d+)", line)
    if m:
        cur = m.group(1)
        res[cur] = {"result": m.group(2)}
        continue
    if not cur and re.match(r"^(rows|[abc])\t", line):
        print(line.rstrip())
        continue
    if cur:
        for k in ("SelectedRows", "SelectedMarks", "ReadCompressedBytes", "RealTimeMicroseconds"):
            mm = re.search(k + r": (\d+) \(increment\)", line)
            if mm and k not in res[cur]:
                res[cur][k] = int(mm.group(1))
for k, v in res.items():
    print(f"{k:12} result={v['result']:>5} rows={v.get('SelectedRows', 0):>9} marks={v.get('SelectedMarks', 0):>5} "
          f"readMiB={v.get('ReadCompressedBytes', 0) / 2**20:7.2f} ms={v.get('RealTimeMicroseconds', 0) / 1000:7.1f}")
