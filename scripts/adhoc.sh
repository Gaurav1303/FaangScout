#!/usr/bin/env bash
# Temporary: why Apple returns no India software roles - list its rejections.
set -x
faangscout --companies Apple --role "software engineer" --location India --hours 720 --include-undated --explain
