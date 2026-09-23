#!/usr/bin/env bash
# Temporary: every Apple India job fetched, and which filter dropped it.
faangscout --companies Apple --role "software engineer" --location India --experience 3 --hours 720 \
  --include-undated --explain --json > apple.json
python - <<'PY'
import json
d = json.load(open("apple.json"))
print("kept:", [(j["title"], j.get("posted_at")) for j in d.get("jobs", [])])
for r in d.get("rejections", [])[:80]:
    job = r.get("job", r)
    print(f"{r.get('filter')}: {job.get('title')} | {job.get('posted_at')} | {job.get('locations')} | {r.get('reason')}"[:260])
PY
