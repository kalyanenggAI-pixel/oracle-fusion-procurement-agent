@echo off
setlocal
cd /d "%~dp0"

findstr /c:"PASTE_YOUR_OPENAI_API_KEY_HERE" .env >nul
if %errorlevel%==0 (
  echo Please open .env and paste your OpenAI API key first.
  echo File: %cd%\.env
  pause
  exit /b 1
)

echo Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

echo Running Oracle Fusion Agent in DRY_RUN mode...
python main.py --pdf quotes\sample_supplier_quote.pdf

pause
