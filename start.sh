#!/usr/bin/env bash
# Menjalankan KPI360 secara lokal (Linux/macOS): venv -> dependensi -> seed demo (sekali) -> server
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
if [ ! -f instance/kpi360.db ]; then python scripts/seed_data.py; fi
echo "Buka http://127.0.0.1:5000  (admin / Admin#2026!)"
python run.py
