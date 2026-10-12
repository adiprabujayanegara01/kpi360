@echo off
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist .env copy .env.example .env >nul
if not exist instance\kpi360.db python scripts\seed_data.py
echo Buka http://127.0.0.1:5000  (admin / Admin#2026!)
python run.py
