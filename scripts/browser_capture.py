"""Open careers pages in a real (headless) Chromium and record what they load.

Development aid for companies whose careers page is a JavaScript app: the
job data isn't in the HTML, it arrives via requests the page makes after
loading. This records, per page:

- every JSON response (URL, method, POST body, status, shape, first bytes) -
  usually the site's own job-search API, which a provider can then call
  directly without a browser;
- job-looking links in the rendered page, as a fallback source of listings;
- whether a bot-check page ("Just a moment...", "Access denied") was shown.

It loads pages as an ordinary browser would. It does not try to get past
bot protection: a site that blocks automated visitors is reported as such.

    python scripts/browser_capture.py scripts/browser_targets.yaml
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

JOB_LINK = re.compile(r"/(?:jobs?|careers?|positions?|openings?|requisitions?|vacanc(?:y|ies)|roles?)/", re.I)
BLOCK_MARKERS = ("just a moment", "access denied", "attention required", "security check",
                 "are you a robot", "captcha", "request blocked", "verify you are human")
MAX_JSON = 15
SNIPPET = 500


def shape(value, depth: int = 0, max_depth: int = 3) -> str:
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {shape(v, depth + 1, max_depth)}" for k, v in list(value.items())[:20]) + "}"
    if isinstance(value, list):
        return f"list[{len(value)}]" + (f" of {shape(value[0], depth + 1, max_depth)}" if value else "")
    if isinstance(value, str):
        return f"str({value[:30]!r})"
    return repr(value)


def capture(page, target: dict) -> None:
    responses: list[dict] = []

    def on_response(response):
        try:
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            body = response.text()
        except Exception:  # noqa: BLE001 - redirects, aborted requests, binary bodies
            return
        request = response.request
        responses.append({
            "url": response.url, "method": request.method, "status": response.status,
            "post": request.post_data or "", "size": len(body), "body": body,
        })

    page.on("response", on_response)
    try:
        page.goto(target["url"], wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(int(target.get("wait_ms", 6000)))
        for _ in range(int(target.get("scrolls", 3))):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1200)
    except Exception as exc:  # noqa: BLE001 - report and carry on with the next target
        print(f"  LOAD ERROR {exc!r}"[:300])

    title = page.title() if not page.is_closed() else ""
    text = page.inner_text("body")[:4000].lower() if not page.is_closed() else ""
    blocked = [m for m in BLOCK_MARKERS if m in title.lower() or m in text[:1500]]
    print(f"  final={page.url}")
    print(f"  title={title[:120]!r} blocked={blocked or 'no'}")

    # Job-looking links in the rendered page.
    links = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => [e.href, (e.innerText || '').trim().replace(/\\s+/g, ' ')])"
    )
    job_links = [(h, t) for h, t in links if JOB_LINK.search(h) and t and len(t) < 160]
    seen = set()
    job_links = [x for x in job_links if not (x[0] in seen or seen.add(x[0]))]
    print(f"  job-like links: {len(job_links)}")
    for href, label in job_links[:8]:
        print(f"    - {label[:70]!r} -> {href[:160]}")

    # JSON responses, biggest first (job lists are usually the largest).
    print(f"  json responses: {len(responses)}")
    for r in sorted(responses, key=lambda r: r["size"], reverse=True)[:MAX_JSON]:
        try:
            parsed = shape(json.loads(r["body"]))
        except ValueError:
            parsed = "unparseable"
        print(f"    [{r['status']}] {r['method']} {r['url'][:220]} ({r['size']} bytes)")
        # ``full: <regex>`` prints matching requests' whole POST body (a
        # GraphQL query, say) and more of the response.
        full = bool(target.get("full")) and re.search(target["full"], r["url"] + " " + r["post"])
        if r["post"]:
            print(f"        post: {r['post'][:8000 if full else 400]}")
        print(f"        shape: {parsed[:500]}")
        snippet = re.sub(r"\s+", " ", r["body"][:4000 if full else SNIPPET])
        print(f"        body: {snippet}")


def main(path: str) -> int:
    targets = yaml.safe_load(Path(path).read_text()) or []
    with sync_playwright() as pw:
        # CHROMIUM_PATH lets a machine with a pre-installed Chromium (whose
        # version doesn't match the installed Playwright) use it directly.
        browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None)
        for target in targets:
            print(f"=== {target['name']}: {target['url']}")
            context = browser.new_context(locale="en-IN", viewport={"width": 1366, "height": 900})
            page = context.new_page()
            try:
                capture(page, target)
            finally:
                context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "scripts/browser_targets.yaml"))
