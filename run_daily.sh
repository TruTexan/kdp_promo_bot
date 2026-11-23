#!/usr/bin/env bash
# Local alternative to GitHub Actions. cron:  0 7 * * *  /path/to/run_daily.sh
cd "$(dirname "$0")" && python3 growth.py daily --live >> out/daily.log 2>&1
