#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if grep -q "PASTE_YOUR_OPENAI_API_KEY_HERE" .env 2>/dev/null; then
  echo "Please edit .env and add your OpenAI API key first."
  exit 1
fi

python -m pip install -r requirements.txt
python main.py --pdf quotes/sample_supplier_quote.pdf
