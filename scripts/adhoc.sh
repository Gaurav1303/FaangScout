#!/usr/bin/env bash
# Temporary: first live run of the boards added for the ~35-40 LPA list.
set -x
NEW=(Meesho Zscaler PayPal Razorpay CRED Groww Tekion Autodesk Expedia Twilio Harness Zeta Confluent
     Visa Atlassian "Goldman Sachs" "Palo Alto Networks" Swiggy Google Databricks)
faangscout --check --companies "${NEW[@]}"
faangscout --config scout.yaml --companies "${NEW[@]}" --hours 720 --include-undated \
  --markdown --max-rows 400
