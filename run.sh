#!/usr/bin/env bash
set -e

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill in BOT_TOKEN and CHAT_ID."
  exit 1
fi

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
