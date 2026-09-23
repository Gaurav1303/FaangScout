#!/usr/bin/env bash
# Temporary: how Apple's search pages behave (paging, India filter).
python - <<'PY'
import re, httpx
from faangscout.providers.apple import _ROW
c = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 FaangScout"})
for label, params in [
    ("world p1", {"search": "software engineer", "sort": "newest", "page": 1}),
    ("world p2", {"search": "software engineer", "sort": "newest", "page": 2}),
    ("india p1", {"search": "software engineer", "sort": "newest", "location": "india-INDC", "page": 1}),
    ("india p2", {"search": "software engineer", "sort": "newest", "location": "india-INDC", "page": 2}),
    ("india p3", {"search": "software engineer", "sort": "newest", "location": "india-INDC", "page": 3}),
    ("india relevance p1", {"search": "software engineer", "location": "india-INDC", "page": 1}),
]:
    r = c.get("https://jobs.apple.com/en-in/search", params=params)
    rows = list(_ROW.finditer(r.text))
    total = re.search(r"of\s*(?:<!-- -->)?\s*([\d,]+)\s*(?:<!-- -->)?\s*results", r.text)
    nxt = re.search(r'rel="next" href="([^"]+)"', r.text)
    h3 = r.text.count("<h3><a ")
    print(f"== {label}: {r.status_code} final={r.url} rows={len(rows)} h3={h3} total={total.group(1) if total else '?'} next={nxt.group(1) if nxt else None}")
    for m in rows[:25]:
        rest = m.group("rest")
        d = re.search(r'job-posted-date"[^>]*>(.*?)</span>', rest)
        loc = re.search(r'location-sub"[^>]*>(.*?)</span>', rest)
        print("   ", m.group("id"), "|", m.group("title")[:70], "|", d.group(1) if d else "-", "|", loc.group(1)[:40] if loc else "-")
PY
