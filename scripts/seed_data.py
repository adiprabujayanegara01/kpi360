"""Buat tabel + seed data. Pemakaian:
   python scripts/seed_data.py                 # data dasar + data demo fiktif
   python scripts/seed_data.py --base-only     # hanya kategori, pengaturan, admin
   python scripts/seed_data.py --employees 120 --months 12
   python scripts/seed_data.py --reset         # hapus database SQLite lalu seed ulang
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.seed import seed_base, seed_demo, reset_db, ADMIN_PASSWORD, DEMO_PASSWORD  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--employees", type=int, default=64)
    ap.add_argument("--months", type=int, default=9)
    ap.add_argument("--base-only", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--if-empty", action="store_true", help="lewati bila sudah ada data")
    a = ap.parse_args()
    app = create_app()
    with app.app_context():
        if a.reset:
            reset_db()
        db.create_all()
        t = time.time()
        if a.base_only:
            seed_base()
            print("Data dasar dibuat. Login: admin /", ADMIN_PASSWORD)
        else:
            r = seed_demo(a.employees, a.months)
            if r.get("skipped"):
                print("Database sudah berisi data; dilewati (gunakan --reset untuk mengulang).")
                return
            print(f"Data demo FIKTIF dibuat: {r} dalam {time.time() - t:.1f} detik")
            print(f"Akun: admin / {ADMIN_PASSWORD} | supervisor: spv.it, spv.fin, ... | karyawan: emp002, emp003, ... | password demo: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
