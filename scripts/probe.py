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
  shape        a JSON response's structure: keys, types, list lengths -
               for finding pagination and total-count fields
"""

from __future__ import annotations

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
            {"fingerprint": probe_fingerprint, "shape": probe_shape}.get(kind, probe_raw)(client, target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "scripts/probe_targets.yaml"))
