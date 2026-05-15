@echo off
setlocal
cd /d "%~dp0"

echo Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

echo Starting web UI on http://127.0.0.1:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload

pause
