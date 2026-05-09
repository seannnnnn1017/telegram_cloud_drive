#!/usr/bin/env bash
set -e

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill in BOT_TOKEN and CHAT_ID."
  exit 1
fi

if command -v python3 &>/dev/null; then
  PY=python3
elif command -v python &>/dev/null; then
  PY=python
elif command -v py &>/dev/null; then
  PY=py
else
  echo "ERROR: Python not found. Make sure Python 3.11+ is on PATH."
  exit 1
fi

$PY -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
