#!/usr/bin/env python3
"""
freeze_day.py — đóng băng một ngày của báo cáo DXL / freeze one day of the DXL SEO report.

Copies index.html to archive/YYYY-MM-DD.html so that day's report survives unchanged, then
registers the day in data/index.json so the date picker can open it.

Why this is a script and not a `cp`:

  archive/*.html live one folder below the root, so a plain copy of index.html breaks there —
  the picker's fetch('data/index.json') resolves to archive/data/index.json and 404s, LATEST is
  wrong for that day, and every relative link (targets, header tag, footer) loses its '../'.

  So the copy is transformed: the whole <section id="sec-filter"> is removed, relative links are
  rewritten to point up one level, and the amber "this snapshot is frozen" banner is inserted
  right after <body>.

Refuses to run when data/YYYY-MM-DD.json is missing (the JSON is the source of truth, the HTML is
a view of it) and refuses to overwrite an existing archive file without --force, because a frozen
day is closed.

Usage
-----
  python3 freeze_day.py --repo /tmp/newrepo --date 2026-07-30 \
      --label-vi "Thêm bộ chọn ngày" --label-en "Date picker added" --dry-run

  # then, once the summary reads right:
  python3 freeze_day.py --repo /tmp/newrepo --date 2026-07-30 \
      --label-vi "Thêm bộ chọn ngày" --label-en "Date picker added"
"""

import argparse
import json
import os
import re
import sys

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

BANNER_STYLE = ("background:#fdf4e3;border-bottom:1px solid #f0dcb4;color:#b7791f;"
                "font:600 12.5px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
                "Helvetica,Arial,sans-serif;padding:9px 130px 9px 16px;text-align:center")
LINK_STYLE = "color:#b7791f;font-weight:800"

# href="history.html" -> href="../history.html", and so on. Order matters: the archive/ rule
# strips the folder because the copy already lives inside it.
LINK_REWRITES = [
    ('href="archive/', 'href="'),
    ('href="index.html"', 'href="../index.html"'),
    ('href="history.html"', 'href="../history.html"'),
    ('href="history.html?', 'href="../history.html?'),
    ('href="data/', 'href="../data/'),
    ('href="weekly/', 'href="../weekly/'),
]

GUARD = "if (!document.getElementById('sec-filter')) return;"


def fail(msg):
    sys.stderr.write("freeze_day: " + msg + "\n")
    sys.exit(1)


def dmy(iso):
    y, m, d = iso.split("-")
    return "%s/%s/%s" % (d, m, y)


def d_mon_y(iso):
    y, m, d = iso.split("-")
    return "%d %s %s" % (int(d), MONTHS_EN[int(m) - 1], y)


def banner_html(iso):
    return (
        '<div data-archive-banner style="' + BANNER_STYLE + '">'
        '<span class="vi">\U0001f5c2 Bản lưu ng\xe0y ' + dmy(iso) +
        ' — b\xe1o c\xe1o n\xe0y đ\xe3 đ\xf3ng băng, kh\xf4ng cập nhật nữa. '
        '<a href="../index.html" style="' + LINK_STYLE + '">Xem b\xe1o c\xe1o mới nhất</a> \xb7 '
        '<a href="../history.html" style="' + LINK_STYLE + '">Lịch sử dữ liệu theo ng\xe0y</a>'
        '</span>'
        '<span class="en">\U0001f5c2 Archived snapshot of ' + d_mon_y(iso) +
        ' — this report is frozen and no longer updated. '
        '<a href="../index.html" style="' + LINK_STYLE + '">Open the latest report</a> \xb7 '
        '<a href="../history.html" style="' + LINK_STYLE + '">Daily data history</a>'
        '</span></div>'
    )


def strip_section(html, section_id):
    """Remove <section ... id="<section_id>" ...> ... </section>, nesting-aware.

    Returns (new_html, removed_char_count). Raises if the section is absent, because a copy that
    silently kept the picker is exactly the bug this script exists to prevent.
    """
    m = re.search(r'<section[^>]*\bid="%s"' % re.escape(section_id), html)
    if not m:
        raise ValueError('no <section id="%s"> found in the source' % section_id)
    start = m.start()

    # walk forward, counting section opens/closes, to find the matching close tag
    depth = 0
    pos = start
    tag = re.compile(r'</?section\b', re.I)
    while True:
        t = tag.search(html, pos)
        if not t:
            raise ValueError('unbalanced <section> after id="%s"' % section_id)
        if html[t.start():t.start() + 2] == "</":
            depth -= 1
            if depth == 0:
                end = html.index(">", t.start()) + 1
                break
        else:
            depth += 1
        pos = t.end()

    # swallow the surrounding blank line and a preceding HTML comment on its own line
    lead = html.rfind("\n", 0, start)
    if lead != -1 and html[lead + 1:start].strip() == "":
        start = lead + 1
    cm = re.search(r'(?:^|\n)([ \t]*<!--[^\n]*-->[ \t]*\n)\s*$', html[:start])
    if cm:
        start = start - len(cm.group(1))
    while end < len(html) and html[end] == "\n":
        end += 1

    return html[:start] + html[end:], end - start


def rewrite_links(html):
    hits = []
    for old, new in LINK_REWRITES:
        n = html.count(old)
        if n:
            html = html.replace(old, new)
            hits.append((old, new, n))
    return html, hits


def leftover_root_links(html):
    """Relative links that still point at the repo root from inside archive/."""
    bad = []
    for m in re.finditer(r'href="([^"#][^"]*)"', html):
        u = m.group(1)
        if u.startswith(("http", "//", "mailto:", "../", "#")):
            continue
        # skip hrefs that are built in JS ( href="' + targetFor(...) ) — those live inside the
        # picker code, which the guard disables in a frozen copy
        if "'" in u or "+" in u or "<" in u:
            continue
        bad.append(u)
    return sorted(set(bad))


def load_manifest(path):
    with open(path, encoding="utf-8") as f:
        ix = json.load(f)
    if not isinstance(ix.get("snapshots"), list):
        fail("data/index.json has no snapshots[] array")
    return ix


def upsert_snapshot(ix, date, label_vi, label_en, report_rel):
    existing = [s for s in ix["snapshots"] if s.get("date") == date]
    if existing:
        row = existing[0]
        action = "updated"
    else:
        row = {"date": date}
        ix["snapshots"].append(row)
        action = "added"
    row["data"] = "data/%s.json" % date
    row["report"] = report_rel
    if label_vi:
        row["label_vi"] = label_vi
    if label_en:
        row["label_en"] = label_en
    row.setdefault("label_vi", "")
    row.setdefault("label_en", "")
    # the picker's month clamp reads snapshots[0].date, so keep this sorted
    ix["snapshots"].sort(key=lambda s: s.get("date", ""))
    # `latest` is the day index.html currently reports on
    newest = ix["snapshots"][-1]["date"]
    ix["latest"] = max(date, newest)
    return action


def main():
    ap = argparse.ArgumentParser(description="Freeze one day of the DXL SEO report.")
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    ap.add_argument("--date", required=True, help="the scanned day, YYYY-MM-DD (Vietnam time)")
    ap.add_argument("--label-vi", default="", help="what changed that day, Vietnamese")
    ap.add_argument("--label-en", default="", help="what changed that day, English")
    ap.add_argument("--source", default="index.html", help="page to freeze (default index.html)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing archive file")
    a = ap.parse_args()

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", a.date):
        fail("--date must be YYYY-MM-DD, got %r" % a.date)

    repo = os.path.abspath(a.repo)
    src = os.path.join(repo, a.source)
    day_json = os.path.join(repo, "data", "%s.json" % a.date)
    manifest = os.path.join(repo, "data", "index.json")
    out_rel = "archive/%s.html" % a.date
    out = os.path.join(repo, out_rel)

    for p, what in ((src, "source page"), (day_json, "the day's JSON snapshot"),
                    (manifest, "the manifest")):
        if not os.path.isfile(p):
            fail("missing %s: %s" % (what, p))

    # the JSON is the source of truth; a frozen page with no data behind it is not allowed
    try:
        with open(day_json, encoding="utf-8") as f:
            snap = json.load(f)
    except ValueError as e:
        fail("data/%s.json is not valid JSON: %s" % (a.date, e))
    if snap.get("date") != a.date:
        fail('data/%s.json has date=%r — the filename and the "date" field must agree'
             % (a.date, snap.get("date")))
    if not snap.get("provenance"):
        fail("data/%s.json has no provenance string; say where the numbers came from" % a.date)

    if os.path.exists(out) and not a.force:
        fail("%s already exists. A frozen day is closed — re-freeze only with --force, and only "
             "because the source data was corrected." % out_rel)

    with open(src, encoding="utf-8") as f:
        html = f.read()

    if GUARD not in html:
        sys.stderr.write(
            "freeze_day: WARNING — %s does not contain the picker guard\n  %s\n"
            "  Add it at the top of the picker IIFE so a stripped copy cannot throw.\n"
            % (a.source, GUARD))

    before = len(html.encode("utf-8"))
    try:
        html, removed = strip_section(html, "sec-filter")
    except ValueError as e:
        fail(str(e))

    html, hits = rewrite_links(html)

    body = re.search(r"<body[^>]*>", html, re.I)
    if not body:
        fail("no <body> tag in %s" % a.source)
    if "data-archive-banner" in html:
        fail("%s already carries an archive banner — is it already a frozen copy?" % a.source)
    html = html[:body.end()] + "\n" + banner_html(a.date) + html[body.end():]

    left = leftover_root_links(html)

    ix = load_manifest(manifest)
    old_latest = ix.get("latest")
    action = upsert_snapshot(ix, a.date, a.label_vi, a.label_en, out_rel)
    ix_text = json.dumps(ix, ensure_ascii=False, indent=2) + "\n"

    print("freeze %s  (%s)" % (a.date, "dry run" if a.dry_run else "writing"))
    print("  source        %s  %d bytes" % (a.source, before))
    print("  archive       %s  %d bytes%s"
          % (out_rel, len(html.encode("utf-8")), "  [OVERWRITE]" if os.path.exists(out) else ""))
    print("  picker        stripped #sec-filter, %d chars removed" % removed)
    print("  banner        inserted after <body>: %s / %s" % (dmy(a.date), d_mon_y(a.date)))
    for old, new, n in hits:
        print('  link          %s -> %s  (x%d)' % (old, new, n))
    if not hits:
        print("  link          nothing to rewrite (no root-relative hrefs found)")
    if left:
        print("  WARNING       hrefs still pointing at the repo root: %s" % ", ".join(left))
    print("  manifest      %s snapshot %s, latest %s -> %s, %d day(s) total"
          % (action, a.date, old_latest, ix["latest"], len(ix["snapshots"])))
    print("  labels        vi=%r  en=%r" % (a.label_vi, a.label_en))
    if not a.label_vi or not a.label_en:
        print("  WARNING       both labels should say what changed that day, in both languages")

    if a.dry_run:
        print("\nnothing written. Re-run without --dry-run once the summary above reads right.")
        return

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    with open(manifest, "w", encoding="utf-8") as f:
        f.write(ix_text)
    print("\nwrote %s and data/index.json" % out_rel)
    print("next: serve the repo and run scripts/verify_day.js --date %s, then look at the "
          "screenshots before pushing." % a.date)


if __name__ == "__main__":
    main()
