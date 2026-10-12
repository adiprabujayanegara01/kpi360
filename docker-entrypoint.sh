#!/bin/sh
set -e
# tunggu database siap lalu migrasi skema (Alembic)
python - <<'PY'
import time, sys
from sqlalchemy import create_engine, text
from app.config import Config
for i in range(30):
    try:
        create_engine(Config.SQLALCHEMY_DATABASE_URI).connect().execute(text("select 1")); break
    except Exception as e:
        print("menunggu database...", e.__class__.__name__); time.sleep(2)
else:
    sys.exit("database tidak tersedia")
PY
flask db upgrade
if [ "${SEED_DEMO:-0}" = "1" ]; then python scripts/seed_data.py --if-empty; else python scripts/seed_data.py --base-only; fi
exec "$@"
