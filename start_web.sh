#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
