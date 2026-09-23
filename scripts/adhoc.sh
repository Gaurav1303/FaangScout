#!/usr/bin/env bash
# Temporary: confirm Apple's link shape, then list what's open now at the
# newly covered companies (India, software engineer, 3 yrs, incl. undated).
python - <<'PY'
import re, httpx
c = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 FaangScout"})
r = c.get("https://jobs.apple.com/en-in/search",
          params={"search": "software engineer", "sort": "newest", "location": "india-INDC", "page": 1})
for m in re.findall(r'<h3><a [^>]*href="([^"]+)"', r.text)[:6]:
    print("apple href:", m)
PY
set -x
NEW=(PhonePe Rippling Nutanix Intuit Apple ShareChat Postman Kotak "DP World" JPMorgan)
faangscout --config scout.yaml --companies "${NEW[@]}" --hours 720 --include-undated \
  --markdown --max-rows 400
faangscout --companies Apple --role "software engineer" --location India --hours 720 --include-undated --explain | head -40
