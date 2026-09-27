"""Network diagnostics for the GitHub Actions runner.

Development aid, not part of the package: the machine this project is built
on can't reach career sites, but the Actions runner can. This script fetches
the targets in ``scripts/probe_targets.yaml`` and prints what came back, so
API shapes and ATS vendors can be read from the run log.

Two kinds of target:
  raw          status, final URL, content type, and the start of the body
  fingerprint  which ATS vendors a careers page references (greenhouse,
               lever, workday, eightfold, successfactors, ...), with the
               matching URL fragments - usually enough to configure a board.
               When there are none (a JavaScript app), the API-looking URLs
               in the page source instead.
  link         does a job URL we generate open a real page: status, final
               URL after redirects, and the page title
  text         a JSON field holding HTML (``field: data.jobDescription``),
               rendered to text lines the way the experience filter sees them
  items        sample entries from a JSON list (``path: results``, optional
               ``where: {status: PUBLISHED}``), printed in full, plus the
               distinct values of ``distinct`` across all entries. Takes
               ``method: POST`` and a ``json`` body for POST-only APIs
  around       text surrounding each match of ``pattern`` in the page body
  shape        a JSON response's structure: keys, types, list lengths -
               for finding pagination and total-count fields
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import yaml

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

ATS_PATTERNS = {
    "greenhouse": r"(?:boards|job-boards)(?:-api)?\.greenhouse\.io/(?:v1/boards/|embed/job_board\?for=)?[\w-]+",
    "lever": r"jobs\.lever\.co/[\w-]+",
    "ashby": r"jobs\.ashbyhq\.com/[\w-]+",
    "smartrecruiters": r"(?:careers|jobs)\.smartrecruiters\.com/[\w-]+",
    "workday": r"[\w-]+\.wd\d+\.myworkdayjobs\.com(?:/[\w-]+)*",
    "eightfold": r"[\w.-]*eightfold\.ai[\w/.-]*|/api/apply/v2/jobs|/api/pcsx/[\w/]+",
    "successfactors": r"[\w.-]*successfactors\.(?:com|eu)[\w/.-]*|career\d*\.sapsf\.\w+[\w/.-]*",
    "oracle_hcm": r"[\w.-]+\.oraclecloud\.com/hcmUI/CandidateExperience[\w/.-]*",
    "icims": r"[\w.-]+\.icims\.com[\w/.-]*",
    "jobvite": r"jobs\.jobvite\.com/[\w-]+",
    "taleo": r"[\w.-]+\.taleo\.net[\w/.-]*",
    "phenom": r"[\w.-]*phenompeople\.com[\w/.-]*|cdn\.phenompeople",
    "darwinbox": r"[\w.-]+\.darwinbox\.in[\w/.-]*",
    "keka": r"[\w.-]+\.keka\.com/careers[\w/.-]*",
    "avature": r"[\w.-]+\.avature\.net[\w/.-]*",
    "workable": r"apply\.workable\.com/[\w-]+",
    "rippling_ats": r"ats\.rippling\.com/[\w-]+",
    "zoho": r"[\w.-]+\.zohorecruit\.\w+[\w/.-]*",
    "freshteam": r"[\w.-]+\.freshteam\.com[\w/.-]*",
}


#: URLs in page source that look like data endpoints rather than assets.
API_URL = re.compile(
    r"(?:https?:)?//[\w.-]+(?:/[\w.~%-]*)*/(?:api|graphql|search|jobs?|careers?|openings|positions)"
    r"[\w./~%?=&-]*"
    r'|"/api/[\w./~%?=&-]+"',
    re.IGNORECASE,
)
_ASSET = re.compile(r"\.(?:js|css|png|jpe?g|svg|gif|webp|woff2?|ico|mp4)(?:\?|$)", re.IGNORECASE)


def api_urls(text: str, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for m in API_URL.finditer(text):
        url = m.group(0).strip('"')
        if _ASSET.search(url) or url in seen:
            continue
        seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def shape(value, depth: int = 0, max_depth: int = 3) -> str:
    """One-line structural summary of parsed JSON."""
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        inner = ", ".join(f"{k}: {shape(v, depth + 1, max_depth)}" for k, v in list(value.items())[:25])
        return "{" + inner + "}"
    if isinstance(value, list):
        return f"list[{len(value)}]" + (f" of {shape(value[0], depth + 1, max_depth)}" if value else "")
    if isinstance(value, str):
        return f"str({value[:40]!r})"
    return repr(value)


def squash(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text)[:limit]


def probe_raw(client: httpx.Client, target: dict) -> None:
    method = target.get("method", "GET").upper()
    try:
        r = client.request(method, target["url"], json=target.get("json"), headers=target.get("headers"))
    except httpx.HTTPError as exc:
        print(f"  ERROR {exc!r}")
        return
    print(f"  {r.status_code} {r.headers.get('content-type', '?')} final={r.url}")
    print(f"  body: {squash(r.text, int(target.get('limit', 1500)))}")


def probe_fingerprint(client: httpx.Client, target: dict) -> None:
    try:
        r = client.get(target["url"])
    except httpx.HTTPError as exc:
        print(f"  ERROR {exc!r}")
        return
    print(f"  {r.status_code} final={r.url} ({len(r.text)} chars)")
    found = False
    for vendor, pattern in ATS_PATTERNS.items():
        hits = sorted(set(m.group(0) for m in re.finditer(pattern, r.text, re.IGNORECASE)))
        if hits:
            found = True
            print(f"  {vendor}: {', '.join(hits[:6])}")
    if not found:
        print(f"  no ATS markers; title={squash(''.join(re.findall(r'<title>(.*?)</title>', r.text, re.S)[:1]), 120)!r}")
        for url in api_urls(r.text):
            print(f"  api? {url}")


def probe_link(client: httpx.Client, target: dict) -> None:
    try:
        r = client.get(target["url"])
    except httpx.HTTPError as exc:
        print(f"  ERROR {exc!r}")
        return
    title = squash("".join(re.findall(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)[:1]), 150)
    print(f"  {r.status_code} final={r.url}")
    print(f"  title={title!r}")


def probe_text(client: httpx.Client, target: dict) -> None:
    from faangscout.normalize import html_to_text

    try:
        r = client.get(target["url"])
        value = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  ERROR {exc!r}")
        return
    for part in target["field"].split("."):
        value = value[int(part)] if isinstance(value, list) else (value or {}).get(part)
    lines = html_to_text(value if isinstance(value, str) else "").splitlines()
    print(f"  {r.status_code}; {len(lines)} lines")
    for i, line in enumerate(lines[: int(target.get("max_lines", 120))]):
        print(f"  {i:3} | {line[:220]!r}")


def _dig(value, path: str):
    for part in path.split(".") if path else []:
        value = value[int(part)] if isinstance(value, list) else (value or {}).get(part)
    return value


def probe_items(client: httpx.Client, target: dict) -> None:
    try:
        r = client.request(target.get("method", "GET").upper(), target["url"], json=target.get("json"))
        items = _dig(r.json(), target.get("path", ""))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  ERROR {exc!r}")
        return
    items = items if isinstance(items, list) else [items]
    print(f"  {r.status_code}; {len(items)} items")
    if target.get("distinct"):
        key = target["distinct"]
        counts: dict = {}
        for it in items:
            counts[str((it or {}).get(key))] = counts.get(str((it or {}).get(key)), 0) + 1
        print(f"  distinct {key}: {counts}")
    where = target.get("where") or {}
    chosen = [it for it in items if all(str((it or {}).get(k)) == str(v) for k, v in where.items())]
    for it in chosen[: int(target.get("count", 2))]:
        print(f"  item: {squash(json.dumps(it, ensure_ascii=False), int(target.get('limit', 1500)))}")


def probe_around(client: httpx.Client, target: dict) -> None:
    try:
        r = client.get(target["url"])
    except httpx.HTTPError as exc:
        print(f"  ERROR {exc!r}")
        return
    matches = list(re.finditer(target["pattern"], r.text, re.I))
    print(f"  {r.status_code} final={r.url} ({len(r.text)} chars); {len(matches)} matches")
    ctx = int(target.get("context", 300))
    for m in matches[: int(target.get("count", 3))]:
        print(f"  ...{squash(r.text[max(0, m.start() - ctx): m.end() + ctx], 2 * ctx + 200)}...")


def probe_shape(client: httpx.Client, target: dict) -> None:
    method = target.get("method", "GET").upper()
    try:
        r = client.request(method, target["url"], json=target.get("json"))
        print(f"  {r.status_code} {r.headers.get('content-type', '?')}")
        print(f"  shape: {shape(r.json(), max_depth=int(target.get('depth', 3)))}")
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  ERROR {exc!r}; body: {squash(getattr(r, 'text', ''), 300) if 'r' in dir() else ''}")


def main(path: str) -> int:
    targets = yaml.safe_load(Path(path).read_text()) or []
    with httpx.Client(timeout=25.0, follow_redirects=True,
                      headers={"User-Agent": BROWSER_UA, "Accept": "*/*"}) as client:
        for target in targets:
            kind = target.get("kind", "raw")
            print(f"=== [{kind}] {target.get('name', '')} {target['url']}")
            {"fingerprint": probe_fingerprint, "shape": probe_shape, "link": probe_link, "text": probe_text,
             "items": probe_items, "around": probe_around}.get(kind, probe_raw)(client, target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "scripts/probe_targets.yaml"))
