#!/usr/bin/env bash
# Temporary: Google's experience now comes from each job page.
set -x
faangscout --config scout.yaml --companies Google --hours 720 --include-undated --markdown --max-rows 200
