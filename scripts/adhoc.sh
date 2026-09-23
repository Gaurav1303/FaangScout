#!/usr/bin/env bash
# Temporary: where a Google job page puts its own title and qualifications.
python - <<'PY'
import re, httpx
url = "https://www.google.com/about/careers/applications/jobs/results/83079118513414854-senior-staff-software-engineer-youtube-create"
html = httpx.get(url, follow_redirects=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
for pat in [r"Senior Staff Software Engineer, YouTube Create", r"inimum qualifications"]:
    for m in list(re.finditer(pat, html))[:4]:
        print(f"[{pat}] at {m.start()}:", re.sub(r"\s+", " ", html[max(0, m.start() - 160): m.end() + 260]))
PY
set -x
faangscout --config scout.yaml --companies Google --hours 720 --include-undated --markdown --max-rows 200 | grep -E "Senior Staff|Manager|^## "
