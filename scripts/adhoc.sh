#!/usr/bin/env bash
# Temporary: Apple's location markup on engineering rows, then the full
# listing for the newly covered companies.
python - <<'PY'
import re, httpx
c = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 FaangScout"})
html = c.get("https://jobs.apple.com/en-in/search",
             params={"search": "software engineer", "sort": "newest", "location": "india-INDC", "page": 1}).text
for m in list(re.finditer(r'id="search-location-search-job-title-[^"]*"', html))[2:5]:
    print("apple loc:", html[m.start():m.start() + 700])
PY
set -x
NEW=(PhonePe Rippling Nutanix Intuit Apple ShareChat Postman Kotak "DP World" JPMorgan)
faangscout --config scout.yaml --companies "${NEW[@]}" --hours 720 --include-undated \
  --markdown --max-rows 400
