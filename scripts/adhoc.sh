#!/usr/bin/env bash
# Temporary: first live check of the Kula (Slice, Acko) and Recruiterflow (CoinSwitch) boards.
set -x
faangscout --check --companies Slice Acko CoinSwitch
faangscout --config scout.yaml --companies Slice Acko CoinSwitch --hours 720 --include-undated --markdown --max-rows 50
