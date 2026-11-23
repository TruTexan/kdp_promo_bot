@echo off
REM Windows alternative to GitHub Actions. Task Scheduler runs this once a day.
cd /d "%~dp0"
python growth.py daily --live >> out\daily.log 2>&1
