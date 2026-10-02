#!/usr/bin/env python3
"""Generate the GitHub Pages site from the repository's own content.

Produces:
  docs/index.html                      the catalogue, built from the two indexes
  docs/<repo path>/index.html          one page per lab, from its README
  docs/assets/site.css, site.js        shared, so the pages stay small
  docs/<old path>/index.html           a redirect for each lab listed in MOVED.md
  docs/labs.json                       labs whose lab.yaml sets web: true, for the notes
                                       site (tools/labs_json.py, a copy from khai-workbench).
                                       The only file under docs/ that is committed

Nothing here is hand-written, so the site cannot drift from the repository.
The indexes it reads are:
  local/releases/README.md   the per-release table (EN and KO), optional
  README.md                  the area tables (EN and KO)
  MOVED.md                   old path -> new location, for labs that left, optional

This repository has no per-release table and no MOVED.md yet; both are read
only if present, and the index page adapts (no releases section) when the
per-release table is missing.

Run from the repository root:
    python3 .github/scripts/build_site.py            # write the site
    python3 .github/scripts/build_site.py --check    # fail if it would change

Requires the `markdown` package (pip install markdown).
"""
import argparse
import html
import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys

try:
    import markdown
except ImportError:
    sys.exit("this script needs the markdown package:  pip install markdown")

REPO = "litkhai/clickstack-hyperdx-hols"
TREE = f"https://github.com/{REPO}/tree/main"
BLOB = f"https://github.com/{REPO}/blob/main"
SITE = "https://litkhai.github.io/clickstack-hyperdx-hols"

RELEASE_ROW = re.compile(
    r"^\|\s*\[([0-9.]+)\]\([0-9.]+/\)\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*\|$"
)
AREA_ROW = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*(.+?)\s*\|$")
HEADING = re.compile(r"^### (.+)$")
LANG_NAV = re.compile(r"^\[English\]\(#english\)\s*\|\s*\[한국어\]\(#한국어\)\s*$", re.M)


# --------------------------------------------------------------------------- #
# reading the repository
# --------------------------------------------------------------------------- #

def md_inline(text, has_page=frozenset()):
    """Render the small subset of markdown that appears in the index tables.

    Links in those tables are repository-relative; point them at the lab page
    when one exists and at GitHub otherwise.
    """
    def link(m):
        label, href = m.group(1), m.group(2)
        if not href.startswith(("http://", "https://", "#", "mailto:")):
            clean = href.strip("/")
            href = ("%s/index.html" % clean if clean in has_page
                    else "%s/%s" % (TREE, clean))
        return '<a href="%s">%s</a>' % (href, label)

    out = html.escape(text)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def split_languages(text):
    """Return the English and Korean halves of a bilingual document."""
    marker = "\n## 한국어"
    if marker not in text:
        return text, None
    en, ko = text.split(marker, 1)
    return en, "## 한국어" + ko


def parse_releases(path):
    """[(version, released, labs, verified, features_en, features_ko)] newest first.

    This repository has no per-release table yet, so a missing file is normal:
    return [] and let the index page render without the releases section.
    """
    if not path.exists():
        return []
    en_half, ko_half = split_languages(path.read_text())

    def rows(chunk):
        found = {}
        for line in chunk.splitlines():
            m = RELEASE_ROW.match(line.strip())
            if m:
                found[m.group(1)] = m.groups()
        return found

    en, ko = rows(en_half), rows(ko_half)
    assert en and set(en) == set(ko), "release tables disagree between languages"
    ordered = sorted(en, key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True)
    return [(v, en[v][1], int(en[v][2]), en[v][3], en[v][4], ko[v][4]) for v in ordered]


def parse_areas(path):
    """[(heading_en, heading_ko, [(label, repo_path, desc_en, desc_ko)])]."""
    en_half, ko_half = split_languages(path.read_text())

    def sections(chunk):
        out, heading, rows = [], None, []
        for line in chunk.splitlines():
            h = HEADING.match(line)
            if h:
                if heading and rows:
                    out.append((heading, rows))
                heading, rows = h.group(1).strip(), []
                continue
            m = AREA_ROW.match(line.strip())
            if m and heading and not m.group(1).startswith(("Lab", "실습", "Version", "버전")):
                rows.append((m.group(1), m.group(2), m.group(3)))
        if heading and rows:
            out.append((heading, rows))
        return out

    def is_catalogue(rows):
        return all("/" in href and not href.startswith("http") for _, href, _ in rows)

    en_sections = [s for s in sections(en_half) if is_catalogue(s[1])]
    ko_sections = [s for s in sections(ko_half) if is_catalogue(s[1])]
    assert len(en_sections) == len(ko_sections), (
        "area sections disagree: %d EN vs %d KO" % (len(en_sections), len(ko_sections)))

    merged = []
    for (h_en, rows_en), (h_ko, rows_ko) in zip(en_sections, ko_sections):
        ko_by_href = {href: desc for _, href, desc in rows_ko}
        merged.append((h_en, h_ko, [
            (label, href.strip("/"), desc_en, ko_by_href.get(href, desc_en))
            for label, href, desc_en in rows_en
        ]))
    return merged


MOVED_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|$")


def parse_moved(path):
    """[(old, target)]: target is an http URL (another repository) or a repo path.

    Only the first table is read (the English half); the Korean half repeats no
    rows. A row whose old path is a prefix of another row is an area folder that
    never had a page, so it gets no redirect.
    """
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        m = MOVED_ROW.match(line.strip())
        if not m:
            continue
        target = m.group(2)
        link = re.match(r"\[[^\]]*\]\(([^)]+)\)", target)
        rows.append((m.group(1).strip("/"), (link.group(1) if link else target).strip("/")))
    olds = [o for o, _ in rows]
    return [(o, t) for o, t in rows
            if not any(x != o and x.startswith(o + "/") for x in olds)]


def redirect_page(old, target):
    """A meta-refresh page, kept out of the sitemap and out of search indexes."""
    url = target if target.startswith("http") else "%s/%s/" % (SITE, target)
    u = html.escape(url, quote=True)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<title>%s — moved</title>\n"
            "<meta http-equiv=\"refresh\" content=\"0; url=%s\">\n"
            "<link rel=\"canonical\" href=\"%s\">\n"
            "<meta name=\"robots\" content=\"noindex\">\n</head>\n<body>\n"
            "<p>This lab moved to <a href=\"%s\">%s</a>.</p>\n"
            "<p>이 실습은 <a href=\"%s\">%s</a>(으)로 옮겼습니다.</p>\n"
            "</body>\n</html>\n" % (html.escape(old), u, u, u, u, u, u))


# --------------------------------------------------------------------------- #
# page shell
# --------------------------------------------------------------------------- #

def bi(en, ko, tag="span"):
    return '<%s class="en">%s</%s><%s class="ko">%s</%s>' % (tag, en, tag, tag, ko, tag)


FAVICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
           "<rect width='64' height='64' rx='8' fill='%23faff69'/><g fill='%23000'>"
           "<rect x='10' y='12' width='8' height='40'/><rect x='22' y='12' width='8' height='40'/>"
           "<rect x='34' y='12' width='8' height='40'/><rect x='46' y='28' width='8' height='8'/>"
           "</g></svg>")


def shell(title, body, depth, description):
    """Wrap page content in the shared chrome. `depth` = directories below docs/."""
    up = "../" * depth
    footer = bi(
        "Generated from the repository by <code>.github/scripts/build_site.py</code>. "
        '<a href="%s/LICENSE">MIT</a>, except where a directory says otherwise. '
        '<a href="https://github.com/%s">Source on GitHub</a>.' % (BLOB, REPO),
        "저장소 내용에서 <code>.github/scripts/build_site.py</code>로 생성됩니다. "
        '<a href="%s/LICENSE">MIT</a>, 디렉토리에 별도 표기가 있는 경우는 예외입니다. '
        '<a href="https://github.com/%s">GitHub 저장소</a>.' % (BLOB, REPO))
    return """<!DOCTYPE html>
<html lang="en" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<meta name="description" content="%(desc)s">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="%(favicon)s">
<link rel="stylesheet" href="%(up)sassets/site.css">
<script src="%(up)sassets/site.js" defer></script>
</head>
<body>
<header>
  <div class="wrap bar">
    <a class="brand" href="%(up)sindex.html">
      <span class="mark" aria-hidden="true"></span>ClickStack &amp; HyperDX HOLs
    </a>
    <div class="spacer"></div>
    <div class="switch" role="group" aria-label="Language">
      <button type="button" data-set-lang="ko" aria-pressed="false">KO</button>
      <button type="button" data-set-lang="en" aria-pressed="true">EN</button>
    </div>
    <div class="switch" role="group" aria-label="Theme">
      <button type="button" data-set-theme="light" title="Light" aria-pressed="false">☀</button>
      <button type="button" data-set-theme="auto" title="System" aria-pressed="true">◐</button>
      <button type="button" data-set-theme="dark" title="Dark" aria-pressed="false">☾</button>
    </div>
    <a class="ghlink" href="https://github.com/%(repo)s">GitHub</a>
  </div>
</header>

%(body)s

<footer class="wrap">
  <p>%(footer)s</p>
</footer>
</body>
</html>
""" % {"title": html.escape(title), "desc": html.escape(description), "favicon": FAVICON,
       "up": up, "repo": REPO, "body": body, "footer": footer}


# --------------------------------------------------------------------------- #
# lab pages
# --------------------------------------------------------------------------- #

def rewrite_links(html_text, repo_path):
    """Point the README's relative links at GitHub, since the site has no files."""
    def sub(m):
        href = m.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        target = pathlib.PurePosixPath(repo_path).joinpath(href.split("#")[0])
        parts = []
        for part in target.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        base = TREE if href.endswith("/") else BLOB
        return 'href="%s/%s"' % (base, "/".join(parts))
    return re.sub(r'href="([^"]+)"', sub, html_text)


def render_markdown(text, repo_path):
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    return rewrite_links(md.convert(text), repo_path)


IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_.]{2,}")
CODE_SHAPED = re.compile(r"[_.0-9]|.[A-Z]")
# Upper-case SQL reads as code-shaped, but every lab contains it, so it
# separates nothing. Dropping it keeps the filter's hit counts meaningful.
UBIQUITOUS = frozenset("""select from where insert create table order group limit
    set engine values with and not null exists like into drop alter clickhouse sql cli
    the for use""".split())


def keywords(root, repo_path):
    """Terms to match a lab on beyond its one-line summary in the index.

    The summaries are short, so filtering on them alone misses labs that plainly
    cover what was typed — "iceberg" matched nothing while four labs used it.

    Only code-shaped tokens are kept: an underscore, a dot, a digit or an
    internal capital. That is what separates `icebergBucket` and
    `allow_experimental_json_type` from the prose around them, and taking prose
    too made "file" and "connect" match nearly every lab. Matching is by
    substring, so "iceberg" still finds `icebergBucket`. Identifiers read the
    same in both languages, so one attribute serves the KO and EN views alike.
    """
    readme = root / repo_path / "README.md"
    if not readme.is_file():
        return ""
    text = readme.read_text()
    terms = set()
    for chunk in re.findall(r"`([^`\n]{3,60})`", text) + re.findall(r"^#+ (.+)$", text, re.M):
        for word in IDENT.findall(chunk):
            low = word.lower()
            if CODE_SHAPED.search(word) and low not in UBIQUITOUS:
                terms.add(low)
    return " ".join(sorted(terms)[:150])


def lab_title(root, repo_path, label):
    """The lab's own H1, which is what the prev/next links should read."""
    readme = root / repo_path / "README.md"
    if not readme.is_file():
        return None
    m = re.search(r"^#\s+(.+)$", readme.read_text(), re.M)
    return m.group(1).strip() if m else label


def lab_page(root, repo_path, label, verified, prev=None, nxt=None):
    """Render one lab README, or None when the lab has no README."""
    readme = root / repo_path / "README.md"
    if not readme.is_file():
        return None
    raw = readme.read_text()
    raw = LANG_NAV.sub("", raw)                       # the site has its own switch
    en_raw, ko_raw = split_languages(raw)
    body_en = render_markdown(re.sub(r"^## English\s*$", "", en_raw, flags=re.M), repo_path)
    body_ko = (render_markdown(re.sub(r"^## 한국어\s*$", "", ko_raw, flags=re.M), repo_path)
               if ko_raw else body_en)

    m = re.search(r"^#\s+(.+)$", raw, re.M)
    title = m.group(1).strip() if m else label

    badge = ""
    if verified:
        badge = ('<span class="badge ok">%s %s</span>'
                 % (bi("verified on", "검증"), html.escape(verified)))

    # A page lives at docs/<repo path>/index.html, so it is exactly as deep
    # as its repo path -- no namespace directory above it.
    depth = len(pathlib.PurePosixPath(repo_path).parts)
    up = "../" * depth

    # Walk the labs in index order without going back to the index each time.
    def step(neighbour, css, arrow_en, arrow_ko):
        if not neighbour:
            return '<span class="step empty"></span>'
        path, text = neighbour
        return ('<a class="step %s" href="%s%s/index.html">'
                '<span class="dir">%s</span><span class="what">%s</span></a>'
                % (css, up, path, bi(arrow_en, arrow_ko), html.escape(text)))

    pager = ('<nav class="pager">%s%s</nav>'
             % (step(prev, "prev", "← Previous", "← 이전"),
                step(nxt, "next", "Next →", "다음 →")))

    body = """<main class="wrap doc">
  <nav class="crumbs">
    <a href="%(up)sindex.html">%(all)s</a>
    <span aria-hidden="true">/</span><code>%(path)s</code>
    %(badge)s
  </nav>
  <div class="readme en">%(en)s</div>
  <div class="readme ko">%(ko)s</div>
  <p class="src"><a href="%(tree)s/%(path)s">%(open)s</a></p>
  %(pager)s
</main>""" % {"up": up, "all": bi("All labs", "전체 실습"),
              "path": html.escape(repo_path), "badge": badge,
              "en": body_en, "ko": body_ko, "tree": TREE, "pager": pager,
              "open": bi("Open this lab on GitHub →", "GitHub에서 이 실습 열기 →")}
    return shell("%s · ClickStack & HyperDX HOLs" % title, body, depth,
                 "%s — hands-on ClickStack/HyperDX lab." % title)


# --------------------------------------------------------------------------- #
# index page
# --------------------------------------------------------------------------- #

def index_page(releases, areas, has_page, kw):
    """`releases` is [] for this repository: there is no per-release table yet,
    so the releases section, its stats and its quick-start snippet are all
    left out rather than rendered empty."""

    def href(repo_path):
        return ("%s/index.html" % repo_path if repo_path in has_page
                else "%s/%s" % (TREE, repo_path))

    n_labs = sum(len(items) for _, _, items in areas)
    n_areas = len(areas)

    blocks = []
    for h_en, h_ko, items in areas:
        cards = []
        for label, repo_path, desc_en, desc_ko in items:
            cards.append("""          <li data-kw="%s">
            <a href="%s"><code>%s</code></a>
            <p>%s</p>
          </li>""" % (kw.get(repo_path, ""), href(repo_path), html.escape(label),
                      bi(md_inline(desc_en, has_page), md_inline(desc_ko, has_page))))
        blocks.append("""      <section class="area">
        <h3>%s</h3>
        <ul class="cards">
%s
        </ul>
      </section>""" % (bi(html.escape(h_en), html.escape(h_ko)), chr(10).join(cards)))

    body = """<main class="wrap">
  <div class="hero">
    <h1>%(h1)s</h1>
    <p>%(lede)s</p>
    <div class="stats">
      <div class="stat"><b>%(n_labs)d</b><span>%(s1)s</span></div>
      <div class="stat"><b>%(n_areas)d</b><span>%(s2)s</span></div>
    </div>
  </div>

  <div class="filter">
    <input type="search" id="q" autocomplete="off" spellcheck="false"
           data-ph-en="Filter by feature or lab name — try: OTel, HyperDX, Terraform"
           data-ph-ko="기능·랩 이름으로 검색 — 예: OTel, HyperDX, Terraform">
    <p class="count" id="q-count" hidden></p>
  </div>

  <section class="group" id="g-other">
  <h2>%(h_other)s</h2>
  <p class="lede">%(lede_other)s</p>
%(blocks)s
  </section>

  <section id="g-start">
  <h2>%(h_start)s</h2>
  <pre><code>git clone https://github.com/%(repo)s.git
cd clickstack-hyperdx-hols</code></pre>
  <p class="lede">%(lede_start)s</p>
  </section>
</main>""" % {
        "h1": bi("ClickStack &amp; HyperDX Hands-on Labs", "ClickStack·HyperDX 실습"),
        "lede": bi(
            "Observability on ClickHouse: OpenTelemetry ingestion, the ClickStack/HyperDX UI, "
            "and ClickHouse watching itself. Every lab brings up what it needs, generates its "
            "own data, and can be run on its own.",
            "ClickHouse 기반 관측성 실습입니다. OpenTelemetry 수집, ClickStack/HyperDX UI, "
            "그리고 ClickHouse가 스스로를 관측하는 방법을 다룹니다. 각 랩은 필요한 환경을 스스로 "
            "띄우고 테스트 데이터를 생성하므로 독립적으로 실행됩니다."),
        "n_labs": n_labs, "n_areas": n_areas,
        "s1": bi("labs", "실습"),
        "s2": bi("areas", "영역"),
        "h_other": bi("Labs", "실습"),
        "lede_other": bi(
            "ClickStack/HyperDX labs, workshops, composable OTel Collector profiles and "
            "Terraform-managed ClickStack config.",
            "ClickStack/HyperDX 실습, 워크숍, 조합 가능한 OTel Collector 프로파일, "
            "Terraform으로 관리하는 ClickStack 설정입니다."),
        "blocks": chr(10).join(blocks),
        "h_start": bi("Getting started", "시작하기"),
        "repo": REPO,
        "lede_start": bi(
            "Then follow the setup and teardown steps in each lab's own README; nothing here is global.",
            "이후 각 실습의 README에 있는 설정·정리 방법을 따르세요. 전역 설정은 없습니다."),
    }
    return shell("ClickStack & HyperDX Hands-on Labs", body, 0,
                 "ClickStack & HyperDX hands-on labs: observability on ClickHouse with "
                 "OpenTelemetry, ClickStack and HyperDX. %d labs across %d areas."
                 % (n_labs, n_areas))


# --------------------------------------------------------------------------- #
# assets
# --------------------------------------------------------------------------- #

CSS = """/* Generated by .github/scripts/build_site.py — do not edit by hand. */

/* ClickHouse palette: the signature yellow on near-black, or on white. */
:root {
  --yellow: #faff69;
  --bg: #ffffff; --surface: #f7f7f5; --fg: #16161a; --muted: #5f6169;
  --line: #e3e3df; --accent: var(--yellow); --accent-fg: #16161a;
  --accent-line: #d8dd3f;
  --ok-bg: #eef7e6; --ok-fg: #2f6b1f; --unk-bg: #f0f0ee; --unk-fg: #6b6d75;
  --code-bg: #f2f2ef; --accent-soft: rgba(216, 221, 63, .35);
}
:root[data-theme="dark"] {
  --bg: #16161a; --surface: #1f1f24; --fg: #ececf0; --muted: #9a9ca6;
  --line: #2e2e35; --accent-line: #5c5f22;
  --ok-bg: #24301a; --ok-fg: #b6e26a; --unk-bg: #26262c; --unk-fg: #8b8d97;
  --code-bg: #202027; --accent-soft: rgba(250, 255, 105, .18);
}
@media (prefers-color-scheme: dark) {
  :root[data-theme="auto"] {
    --bg: #16161a; --surface: #1f1f24; --fg: #ececf0; --muted: #9a9ca6;
    --line: #2e2e35; --accent-line: #5c5f22;
    --ok-bg: #24301a; --ok-fg: #b6e26a; --unk-bg: #26262c; --unk-fg: #8b8d97;
    --code-bg: #202027; --accent-soft: rgba(250, 255, 105, .18);
  }
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.65 ui-sans-serif, -apple-system, "Segoe UI", "Apple SD Gothic Neo",
        "Noto Sans KR", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
code, pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace; }
code { font-size: .89em; background: var(--code-bg); padding: .12em .38em; border-radius: 4px; }
pre { background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
      padding: 14px 16px; overflow-x: auto; font-size: 13.5px; line-height: 1.55; }
pre code { background: none; padding: 0; }
a { color: inherit; text-underline-offset: 2px; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 20px; }

header { border-bottom: 1px solid var(--line); position: sticky; top: 0;
         background: var(--bg); z-index: 20; }
.bar { display: flex; align-items: center; gap: 10px; padding: 12px 0; }
.brand { display: inline-flex; align-items: center; gap: 9px; font-weight: 700;
         text-decoration: none; letter-spacing: -.01em; white-space: nowrap; }
.mark { width: 15px; height: 15px; border-radius: 3px; background: var(--yellow);
        box-shadow: inset 0 0 0 3px var(--bg), 0 0 0 1px var(--accent-line); }
.bar .spacer { flex: 1; }
.switch { display: inline-flex; border: 1px solid var(--line); border-radius: 999px;
          overflow: hidden; }
.switch button { font: inherit; font-size: 13px; font-weight: 600; line-height: 1;
                 padding: 6px 11px; border: 0; background: transparent;
                 color: var(--muted); cursor: pointer; }
.switch button:hover { color: var(--fg); }
.switch button[aria-pressed="true"] { background: var(--accent); color: var(--accent-fg); }
.ghlink { font-size: 14px; color: var(--muted); text-decoration: none; }
.ghlink:hover { color: var(--fg); }

.hero { padding: 60px 0 34px; }
.hero h1 { font-size: clamp(28px, 5vw, 44px); line-height: 1.12; margin: 0 0 14px;
           letter-spacing: -.025em; }
.hero p { color: var(--muted); font-size: 17px; margin: 0 0 28px; max-width: 62ch; }
.stats { display: flex; flex-wrap: wrap; gap: 12px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
        padding: 13px 18px; }
.stat b { display: block; font-size: 24px; line-height: 1.2; letter-spacing: -.02em; }
.stat span { font-size: 13px; color: var(--muted); }

h2 { font-size: 22px; margin: 52px 0 6px; letter-spacing: -.015em; }
.lede { color: var(--muted); margin: 0 0 18px; font-size: 15px; max-width: 76ch; }

table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
     color: var(--muted); font-weight: 600; }
tbody tr:hover { background: var(--surface); }
td.num { white-space: nowrap; color: var(--muted); font-variant-numeric: tabular-nums; }
a.ver { font-weight: 700; text-decoration: none; border-bottom: 2px solid var(--accent); }
.badge { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 9px;
         border-radius: 999px; white-space: nowrap; }
.badge.ok { background: var(--ok-bg); color: var(--ok-fg); }
.badge.unknown { background: var(--unk-bg); color: var(--unk-fg); }

.note { border-left: 3px solid var(--accent); background: var(--surface);
        padding: 14px 18px; border-radius: 0 10px 10px 0; margin: 18px 0 0;
        font-size: 14.5px; }

/* index filter */
.filter { margin: 30px 0 4px; }
.filter input { width: 100%; box-sizing: border-box; font: inherit; font-size: 15px;
                padding: 12px 16px; color: var(--fg); background: var(--surface);
                border: 1px solid var(--line); border-radius: 12px;
                transition: border-color .15s, box-shadow .15s; }
.filter input::placeholder { color: var(--muted); }
.filter input:focus { outline: none; border-color: var(--accent);
                      box-shadow: 0 0 0 3px var(--accent-soft); }
.filter .count { margin: 8px 2px 0; font-size: 13px; color: var(--muted); }
[hidden] { display: none !important; }

.area { margin-top: 34px; }
.area h3 { font-size: 17px; margin: 0 0 12px; }
ul.cards { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px;
           grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
ul.cards li { background: var(--surface); border: 1px solid var(--line);
              border-radius: 12px; padding: 12px 14px; transition: border-color .15s; }
ul.cards li:hover { border-color: var(--accent-line); }
ul.cards a { text-decoration: none; }
ul.cards a code { font-weight: 600; background: none; padding: 0; }
ul.cards p { margin: 6px 0 0; font-size: 13.5px; color: var(--muted); }

/* lab pages */
.doc { padding-bottom: 8px; }
.crumbs { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          padding: 22px 0 6px; font-size: 13.5px; color: var(--muted); }
.crumbs a { text-decoration: none; border-bottom: 1px solid var(--line); }
.crumbs a:hover { border-color: var(--accent); }
.readme { max-width: 82ch; }
.readme h1 { font-size: clamp(25px, 4vw, 34px); letter-spacing: -.02em; margin: 18px 0 8px; }
.readme h2 { font-size: 21px; margin: 40px 0 8px; padding-bottom: 6px;
             border-bottom: 1px solid var(--line); }
.readme h3 { font-size: 17px; margin: 30px 0 6px; }
.readme h4 { font-size: 15.5px; margin: 24px 0 4px; }
.readme table { margin: 12px 0; }
.readme ul, .readme ol { padding-left: 1.3em; }
.readme li { margin: 3px 0; }
.readme hr { border: 0; border-top: 1px solid var(--line); margin: 34px 0; }
.readme blockquote { margin: 12px 0; padding: 2px 16px; border-left: 3px solid var(--accent);
                     color: var(--muted); }
.src { margin: 34px 0 0; font-size: 14.5px; }
.src a { text-decoration: none; border-bottom: 2px solid var(--accent); font-weight: 600; }

/* walk the labs in index order without going back to the index */
.pager { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 40px 0 0; }
.pager .step { display: block; padding: 14px 16px; border-radius: 12px;
               border: 1px solid var(--line); background: var(--surface);
               text-decoration: none; transition: border-color .15s; }
.pager .step:hover { border-color: var(--accent-line); }
.pager .step.empty { border: 0; background: none; }
.pager .next { text-align: right; }
.pager .dir { display: block; font-size: 12.5px; color: var(--muted); }
.pager .what { display: block; margin-top: 3px; font-weight: 600; font-size: 14.5px; }

footer { margin-top: 64px; border-top: 1px solid var(--line); padding: 24px 0 48px;
         color: var(--muted); font-size: 14px; }

html[lang="en"] .ko, html[lang="ko"] .en { display: none; }
.scroll { overflow-x: auto; }

@media (max-width: 620px) {
  .bar { flex-wrap: wrap; gap: 8px; }
  .ghlink { order: 3; }
  .hero { padding: 36px 0 24px; }
}
"""

JS = """/* Generated by .github/scripts/build_site.py — do not edit by hand. */
(function () {
  var LANG = 'chhols-lang', THEME = 'chhols-theme';

  function read(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function write(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }

  function applyLang(lang) {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-set-lang]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.setLang === lang));
    });
    // The filter's placeholder is an attribute, so the CSS switch cannot reach it.
    document.querySelectorAll('[data-ph-en]').forEach(function (i) {
      i.placeholder = lang === 'ko' ? i.dataset.phKo : i.dataset.phEn;
    });
    if (typeof filter === 'function') filter();
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-set-theme]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.setTheme === theme));
    });
  }

  // Index filter. Matches the text of the language currently on screen, so
  // typing "벡터" works in Korean and "vector" in English.
  var box = document.getElementById('q');
  var count = document.getElementById('q-count');

  function visibleText(el) {
    var lang = document.documentElement.lang;
    var hidden = lang === 'ko' ? '.en' : '.ko';
    var copy = el.cloneNode(true);
    copy.querySelectorAll(hidden).forEach(function (n) { n.remove(); });
    return (copy.textContent || '').toLowerCase();
  }

  function filter() {
    if (!box) return;
    var q = box.value.trim().toLowerCase();
    var shown = 0, total = 0;

    document.querySelectorAll('tbody tr, .cards li').forEach(function (el) {
      total++;
      var hit = !q || visibleText(el).indexOf(q) !== -1
               || (el.dataset.kw || '').indexOf(q) !== -1;
      el.hidden = !hit;
      if (hit) shown++;
    });

    // Collapse a heading once nothing under it survives.
    document.querySelectorAll('.area').forEach(function (s) {
      s.hidden = !s.querySelector('.cards li:not([hidden])');
    });
    var other = document.getElementById('g-other');
    if (other) other.hidden = !other.querySelector('.cards li:not([hidden])');
    var start = document.getElementById('g-start');
    if (start) start.hidden = !!q;

    if (count) {
      count.hidden = !q;
      count.textContent = document.documentElement.lang === 'ko'
        ? (shown ? shown + ' / ' + total + '개 표시' : '일치하는 랩이 없습니다')
        : (shown ? shown + ' of ' + total + ' shown' : 'no labs match');
    }
  }

  if (box) {
    box.addEventListener('input', filter);
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { box.value = ''; filter(); }
    });
    // "/" focuses the filter, the shortcut people already expect.
    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && document.activeElement !== box) { e.preventDefault(); box.focus(); }
    });
  }

  var browserKo = (navigator.language || 'en').toLowerCase().indexOf('ko') === 0;
  applyLang(read(LANG) || (browserKo ? 'ko' : 'en'));
  applyTheme(read(THEME) || 'auto');

  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-set-lang], [data-set-theme]');
    if (!el) return;
    if (el.dataset.setLang) { applyLang(el.dataset.setLang); write(LANG, el.dataset.setLang); }
    else { applyTheme(el.dataset.setTheme); write(THEME, el.dataset.setTheme); }
  });
})();
"""


# --------------------------------------------------------------------------- #

def sitemap(has_page):
    """Every page, so the lab pages are indexed and not just the front page."""
    urls = ["%s/" % SITE] + sorted(
        "%s/%s/" % (SITE, p) for p in has_page)
    body = "\n".join("  <url><loc>%s</loc></url>" % html.escape(u) for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "%s\n</urlset>\n" % body)


def labs_json(root, has_page):
    """docs/labs.json via tools/labs_json.py, with each published lab's page URL."""
    spec = importlib.util.spec_from_file_location("labs_json", root / "tools" / "labs_json.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text, _ = mod.build(root, lambda p: "%s/%s/" % (SITE, p) if p in has_page else None)
    return text


# --------------------------------------------------------------------------- #

def build(root):
    releases = parse_releases(root / "local" / "releases" / "README.md")
    areas = parse_areas(root / "README.md")

    wanted = [("local/releases/%s" % v, v, (b if b != "—" else None))
              for v, _, _, b, _, _ in releases]
    wanted += [(p, label, None) for _, _, items in areas for label, p, _, _ in items]

    # First pass: which labs have a README, and what each one calls itself.
    # The prev/next links need both, and they need them before rendering.
    pages = [(p, lab_title(root, p, label), v) for p, label, v in wanted]
    pages = [(p, t, v) for p, t, v in pages if t is not None]
    has_page = {p for p, _, _ in pages}
    kw = {p: keywords(root, p) for p in has_page}

    files = {}
    for i, (repo_path, title, verified) in enumerate(pages):
        prev = (pages[i - 1][0], pages[i - 1][1]) if i else None
        nxt = (pages[i + 1][0], pages[i + 1][1]) if i + 1 < len(pages) else None
        files["%s/index.html" % repo_path] = lab_page(
            root, repo_path, title, verified, prev, nxt)

    for old, target in parse_moved(root / "MOVED.md"):
        assert old not in has_page, "MOVED.md lists %s, which still has a lab page" % old
        if not target.startswith("http"):
            assert target in has_page, "MOVED.md target %s has no lab page" % target
        files["%s/index.html" % old] = redirect_page(old, target)

    files["index.html"] = index_page(releases, areas, has_page, kw)
    files["assets/site.css"] = CSS
    files["assets/site.js"] = JS
    files["sitemap.xml"] = sitemap(has_page)
    files["labs.json"] = labs_json(root, has_page)    # reads the committed one, so before rmtree
    files["robots.txt"] = "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % SITE
    files[".nojekyll"] = ""      # Jekyll would otherwise skip some paths
    return files, len(releases), sum(len(a[2]) for a in areas), len(has_page)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed site is out of date")
    args = ap.parse_args()

    root = pathlib.Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True).stdout.strip())
    docs = root / "docs"

    files, n_rel, n_area, n_pages = build(root)
    summary = ("%d releases, %d other labs, %d lab pages, %d files"
               % (n_rel, n_area, n_pages, len(files)))

    if args.check:
        on_disk = {str(p.relative_to(docs)) for p in docs.rglob("*") if p.is_file()} \
            if docs.exists() else set()
        if on_disk != set(files):
            print("docs/ holds a different set of files than the build produces")
            for label, diff in (("on disk only", on_disk - set(files)),
                                ("missing", set(files) - on_disk)):
                if diff:
                    print("  %s: %s" % (label, ", ".join(sorted(diff)[:5])))
            print("run: python3 .github/scripts/build_site.py")
            return 1
        for rel, content in files.items():
            if (docs / rel).read_text() != content:
                print("docs/%s is out of date; run:" % rel)
                print("    python3 .github/scripts/build_site.py")
                return 1
        print("OK: docs/ matches %s" % summary)
        return 0

    if docs.exists():
        shutil.rmtree(docs)
    for rel, content in files.items():
        out = docs / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
    print("wrote docs/: %s" % summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
