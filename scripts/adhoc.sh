#!/usr/bin/env bash
# Temporary: first live run of the boards found through the browser capture.
# Lists what's open now at those companies (India, software engineer, 3 yrs),
# including boards that publish no dates. Removed once reviewed.
set -x
NEW=(PhonePe Rippling Nutanix Intuit Apple ShareChat Postman Kotak "DP World" JPMorgan)
faangscout --check --companies "${NEW[@]}"
faangscout --config scout.yaml --companies "${NEW[@]}" --hours 720 --include-undated \
  --markdown --max-rows 400 --json-out adhoc.json
