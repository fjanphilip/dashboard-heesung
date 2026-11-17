#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/dashboard"

if [ ! -x ".venv/bin/python" ]; then
    echo "[1/3] Membuat virtual environment..."
    python3 -m venv .venv
fi

echo "[2/3] Memeriksa dependensi..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt --quiet

echo "[3/3] Menjalankan server MQTT + dashboard di http://localhost:8000"
OPEN_BROWSER=1 exec .venv/bin/python -u -m uvicorn app.main:app --host 0.0.0.0 --port 8000
