#!/usr/bin/env bash
# Temporary: first live check of PayU (SuccessFactors) and Elevate K-12 (Workable).
set -x
faangscout --check --companies PayU "Elevate K-12" Couchbase
faangscout --config scout.yaml --companies PayU "Elevate K-12" Couchbase --hours 720 --include-undated --markdown --max-rows 50
