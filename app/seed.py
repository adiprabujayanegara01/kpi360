"""Seed data: dasar (kategori, pengaturan, akun admin) dan data demo sintetis (FIKTIF)."""
import random
from datetime import date, timedelta
from decimal import Decimal as D
from .extensions import db
from .models import (User, Department, Position, Employee, KpiCategory, KpiTemplate, PositionKpiTemplate, ScoreCategory,
                     EvaluationPeriod, KpiAssignment, KpiRealization, KpiVerification, utcnow, ADMIN, SUPERVISOR, EMPLOYEE)
from .services import workflow as wf
from .services.common import DEFAULT_SETTINGS, set_setting

ADMIN_PASSWORD = "Admin#2026!"
DEMO_PASSWORD = "Demo#2026!"
MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

CATEGORIES = [("Productivity", "Volume dan output pekerjaan"), ("Quality", "Kualitas hasil kerja"), ("Efficiency", "Efisiensi waktu dan biaya"),
              ("Customer Satisfaction", "Kepuasan pelanggan atau pengguna"), ("Attendance", "Kehadiran dan kedisiplinan"),
              ("Project", "Pencapaian proyek"), ("Financial", "Target finansial"), ("Behavioral", "Perilaku kerja")]
SCORE_CATS = [("Sangat Baik", 90, 100, "#2E8B57"), ("Baik", 80, 89.99, "#1F6FB2"), ("Cukup", 70, 79.99, "#E0B000"),
              ("Kurang", 60, 69.99, "#E08A00"), ("Sangat Kurang", 0, 59.99, "#C0392B")]

# kode: (nama, kategori, tipe, satuan, target, cap, bukti_wajib, penalti)
KPIS = {
    "IT-TKT": ("Ticket Resolution", "Productivity", "HIGHER", "tiket", 100, 100, False, 100),
    "IT-RSP": ("Response Time", "Efficiency", "LOWER", "jam", 2, 100, False, 100),
    "IT-PRJ": ("Project Completion", "Project", "BOOLEAN", "ya/tidak", 1, 100, True, 100),
    "IT-CSAT": ("User Satisfaction (CSAT)", "Customer Satisfaction", "HIGHER", "skor", 4, 100, False, 100),
    "DEV-SP": ("Story Points Selesai", "Productivity", "HIGHER", "poin", 40, 100, False, 100),
    "DEV-BUG": ("Bug Rate per Rilis", "Quality", "LOWER", "bug", 3, 100, False, 100),
    "DEV-CR": ("Code Review Turnaround", "Efficiency", "LOWER", "jam", 8, 100, False, 100),
    "DEV-SPR": ("Sprint Goal Tercapai", "Project", "HIGHER", "%", 90, 100, False, 100),
    "BEH-COL": ("Kolaborasi Tim (penilaian atasan)", "Behavioral", "HIGHER", "skor", 4, 100, False, 100),
    "HR-TTH": ("Time-to-Hire", "Efficiency", "LOWER", "hari", 30, 100, False, 100),
    "HR-ACC": ("Akurasi Data Karyawan", "Quality", "HIGHER", "%", 98, 100, False, 100),
    "HR-CSAT": ("Kepuasan Layanan HR", "Customer Satisfaction", "HIGHER", "skor", 4.2, 100, False, 100),
    "ATT-01": ("Kehadiran", "Attendance", "HIGHER", "%", 95, 100, False, 100),
    "SAL-TGT": ("Pencapaian Target Penjualan", "Financial", "HIGHER", "juta Rp", 500, 120, True, 100),
    "SAL-PRS": ("Kunjungan/Prospek Baru", "Productivity", "HIGHER", "prospek", 20, 100, False, 100),
    "SAL-RET": ("Tingkat Retensi Pelanggan", "Customer Satisfaction", "HIGHER", "%", 85, 100, False, 100),
    "SAL-RPT": ("Kelengkapan Laporan", "Quality", "HIGHER", "%", 100, 100, False, 100),
    "FIN-CLS": ("Ketepatan Waktu Closing", "Efficiency", "LOWER", "hari", 5, 100, False, 100),
    "FIN-REC": ("Akurasi Rekonsiliasi", "Quality", "HIGHER", "%", 99, 100, True, 100),
    "FIN-AUD": ("Temuan Audit", "Quality", "LOWER", "temuan", 0, 100, False, 25),
    "MKT-LDS": ("Jumlah Leads", "Productivity", "HIGHER", "lead", 300, 110, False, 100),
    "MKT-CVR": ("Conversion Rate", "Financial", "HIGHER", "%", 4, 100, False, 100),
    "MKT-CMP": ("Kampanye Tepat Waktu", "Project", "BOOLEAN", "ya/tidak", 1, 100, True, 100),
    "MKT-ENG": ("Engagement Rate", "Customer Satisfaction", "HIGHER", "%", 6, 100, False, 100),
    "OPS-OUT": ("Output Produksi", "Productivity", "HIGHER", "unit", 1000, 110, False, 100),
    "OPS-DEF": ("Defect Rate", "Quality", "LOWER", "%", 2, 100, False, 100),
    "OPS-DLV": ("Ketepatan Pengiriman", "Efficiency", "HIGHER", "%", 95, 100, False, 100),
    "OPS-STK": ("Kesesuaian Stok vs Sistem", "Quality", "EXACT", "%", 100, 100, False, 100),
    "CS-TKT": ("Tiket Ditangani", "Productivity", "HIGHER", "tiket", 150, 100, False, 100),
    "CS-FRT": ("First Response Time", "Efficiency", "LOWER", "menit", 15, 100, False, 100),
    "CS-CSAT": ("CSAT Pelanggan", "Customer Satisfaction", "HIGHER", "skor", 4.3, 100, False, 100),
    "CS-SOP": ("Kepatuhan SOP", "Behavioral", "HIGHER", "skor", 4, 100, False, 100),
    "TL-TEAM": ("Pencapaian Target Tim", "Productivity", "HIGHER", "%", 90, 100, False, 100),
    "TL-DEV": ("Pengembangan Anggota Tim", "Behavioral", "HIGHER", "skor", 4, 100, False, 100),
    "TL-PRJ": ("Proyek Tim Tepat Waktu", "Project", "BOOLEAN", "ya/tidak", 1, 100, False, 100),
}
POSITIONS = {  # kode: (nama, level, [(kpi, bobot)])
    "ITS": ("IT Support", "Staff", [("IT-TKT", 30), ("IT-RSP", 20), ("IT-PRJ", 30), ("IT-CSAT", 20)]),
    "DEV": ("Software Developer", "Staff", [("DEV-SP", 25), ("DEV-BUG", 25), ("DEV-CR", 15), ("DEV-SPR", 20), ("BEH-COL", 15)]),
    "HRS": ("HR Staff", "Staff", [("HR-TTH", 30), ("HR-ACC", 25), ("HR-CSAT", 25), ("ATT-01", 20)]),
    "SLS": ("Sales Staff", "Staff", [("SAL-TGT", 45), ("SAL-PRS", 20), ("SAL-RET", 20), ("SAL-RPT", 15)]),
    "FIN": ("Finance Staff", "Staff", [("FIN-CLS", 30), ("FIN-REC", 35), ("FIN-AUD", 15), ("ATT-01", 20)]),
    "MKT": ("Marketing Staff", "Staff", [("MKT-LDS", 35), ("MKT-CVR", 25), ("MKT-CMP", 20), ("MKT-ENG", 20)]),
    "OPS": ("Operations Staff", "Staff", [("OPS-OUT", 35), ("OPS-DEF", 25), ("OPS-DLV", 25), ("OPS-STK", 15)]),
    "CSV": ("Customer Service", "Staff", [("CS-TKT", 30), ("CS-FRT", 25), ("CS-CSAT", 30), ("CS-SOP", 15)]),
    "TL": ("Team Lead", "Lead", [("TL-TEAM", 40), ("TL-DEV", 20), ("TL-PRJ", 20), ("ATT-01", 20)]),
}
DEPTS = {  # kode: (nama, [posisi staf])
    "IT": ("Teknologi Informasi", ["ITS", "DEV"]), "FIN": ("Keuangan", ["FIN"]), "HR": ("Sumber Daya Manusia", ["HRS"]),
    "MKT": ("Pemasaran", ["MKT"]), "OPS": ("Operasional", ["OPS"]), "SAL": ("Penjualan", ["SLS"]),
    "CS": ("Layanan Pelanggan", ["CSV"]), "LGL": ("Kepatuhan & Legal", ["HRS", "FIN"]),
}
FIRST = ["Adi", "Budi", "Citra", "Dewi", "Eko", "Fajar", "Gita", "Hana", "Indra", "Joko", "Kiki", "Lina", "Made", "Nina", "Omar", "Putri",
         "Rani", "Satria", "Tari", "Umar", "Vina", "Wawan", "Yuni", "Zaki", "Anton", "Bella", "Candra", "Dimas", "Elsa", "Farhan"]
LAST = ["Prabu", "Santoso", "Wijaya", "Lestari", "Pratama", "Hidayat", "Saputra", "Wulandari", "Nugroho", "Rahma", "Kusuma", "Permata", "Utomo", "Siregar", "Halim"]


def reset_db():
    """Hapus semua tabel. SQLite: matikan FK sementara karena ada referensi melingkar (departemen <-> karyawan)."""
    with db.engine.connect() as conn:
        sqlite = conn.dialect.name == "sqlite"
        if sqlite:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        db.metadata.drop_all(conn)
        conn.commit()
        if sqlite:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def seed_base(admin_password=ADMIN_PASSWORD):
    if not KpiCategory.query.count():
        for n, d in CATEGORIES:
            db.session.add(KpiCategory(name=n, description=d))
    if not ScoreCategory.query.count():
        for n, lo, hi, c in SCORE_CATS:
            db.session.add(ScoreCategory(name=n, min_score=lo, max_score=hi, color=c))
    for k, v in DEFAULT_SETTINGS.items():
        from .models import Setting
        if not db.session.get(Setting, k):
            set_setting(k, v)
    if not User.query.filter_by(username="admin").first():
        u = User(username="admin", email="admin@kpi360.local", role=ADMIN)
        u.set_password(admin_password)
        db.session.add(u)
        db.session.flush()
        db.session.add(Employee(employee_number="HR-001", user_id=u.id, name="Administrator HR", email=u.email, status="Active"))
    db.session.commit()


def _periods(months, today):
    y, m = today.year, today.month
    out = []
    for i in range(months, -1, -1):  # i bulan ke belakang
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        start = date(yy, mm, 1)
        end = (date(yy + (mm == 12), mm % 12 + 1, 1)) - timedelta(days=1)
        out.append((f"{MONTHS[mm - 1]} {yy}", start, end, end + timedelta(days=3)))
    return out


def _value(kind, target, ach, rnd):
    t = float(target)
    if kind == "HIGHER":
        return round(t * ach / 100, 2)
    if kind == "LOWER":
        return max(0, round((100 - ach) / 25)) if t == 0 else round(t / (max(ach, 5) / 100), 2)
    if kind == "EXACT":
        return round(t * (1 - (100 - min(ach, 100)) / 100 * (1 if rnd.random() < .5 else -1)), 2)
    return 1 if rnd.random() < min(ach, 100) / 100 + .05 else 0


def seed_demo(employees=64, months=9, seed=42, today=None):
    """Data FIKTIF: 8 departemen, ~64 karyawan, 9 periode final + 1 periode berjalan, dengan pola tren naik/stabil/turun."""
    today = today or date.today()
    rnd = random.Random(seed)
    seed_base()
    admin = User.query.filter_by(username="admin").first()
    if Employee.query.count() > 1:
        return {"skipped": True}
    cats = {c.name: c for c in KpiCategory.query.all()}
    tpl = {}
    for code, (n, cat, kind, unit, tgt, cap, ev, pen) in KPIS.items():
        t = KpiTemplate(kpi_code=code, name=n, category_id=cats[cat].id, measurement_type=kind, unit=unit, default_target=tgt,
                        max_achievement=cap, evidence_required=ev, lower_penalty=pen)
        db.session.add(t)
        tpl[code] = t
    pos = {}
    for code, (n, lvl, links) in POSITIONS.items():
        assert sum(w for _, w in links) == 100, code
        p = Position(code=code, name=n, level=lvl)
        db.session.add(p)
        pos[code] = (p, links)
    db.session.flush()
    for code, (p, links) in pos.items():
        for k, w in links:
            db.session.add(PositionKpiTemplate(position_id=p.id, kpi_template_id=tpl[k].id, default_weight=w))
    depts = {}
    for code, (n, _) in DEPTS.items():
        d = Department(code=code, name=n)
        db.session.add(d)
        depts[code] = d
    db.session.flush()

    per_dept = max(3, employees // len(DEPTS))
    n = 0
    level_of = {}
    pattern_pool = ["up"] * 25 + ["stable"] * 40 + ["down"] * 20 + ["low"] * 15
    emps = []

    def make(dept_code, pcode, role, sup):
        nonlocal n
        n += 1
        name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        uname = f"spv.{dept_code.lower()}" if role == SUPERVISOR else f"emp{n:03d}"
        u = User(username=uname, email=f"{uname}@demo.kpi360.local", role=role)
        u.set_password(DEMO_PASSWORD)
        db.session.add(u)
        db.session.flush()
        e = Employee(employee_number=f"E{n:04d}", user_id=u.id, name=name, email=u.email, department_id=depts[dept_code].id,
                     position_id=pos[pcode][0].id, supervisor_id=sup.id if sup else None,
                     join_date=today - timedelta(days=rnd.randint(200, 2500)), status="Probation" if rnd.random() < .04 else "Active")
        db.session.add(e)
        db.session.flush()
        pat = rnd.choice(pattern_pool)
        base, slope = {"up": (rnd.uniform(66, 80), rnd.uniform(1.6, 3.0)), "stable": (rnd.uniform(80, 93), rnd.uniform(-.3, .3)),
                       "down": (rnd.uniform(90, 97), -rnd.uniform(2.8, 4.8)), "low": (rnd.uniform(52, 68), rnd.uniform(-.5, .8))}[pat]
        level_of[e.id] = (base, slope, pat)
        emps.append(e)
        return e

    for dc, (_, staff_pos) in DEPTS.items():
        sup = make(dc, "TL", SUPERVISOR, None)
        depts[dc].head_employee_id = sup.id
        for i in range(per_dept - 1):
            make(dc, staff_pos[i % len(staff_pos)], EMPLOYEE, sup)
    db.session.commit()

    plist = _periods(months, today)
    verifier_for = {e.id: (e.supervisor.user_id if e.supervisor else admin.id) for e in emps}
    for idx, (name, start, end, deadline) in enumerate(plist):
        current = idx == len(plist) - 1
        p = EvaluationPeriod(name=name, period_type="MONTHLY", start_date=start, end_date=end, input_deadline=deadline, status="DRAFT")
        db.session.add(p)
        db.session.commit()
        wf.bulk_assign(p, admin)
        wf.publish_period(p, admin)
        wf.start_period(p, admin)
        for a in KpiAssignment.query.filter_by(period_id=p.id).all():
            base, slope, pat = level_of[a.employee_id]
            ach = max(25, min(104, base + slope * idx + rnd.gauss(0, 3) + rnd.gauss(0, 6)))
            kind = a.measurement_type_snapshot
            val = _value(kind, a.target, ach, rnd)
            if current:
                roll = rnd.random()
                status = ("APPROVED" if roll < .35 else "SUBMITTED" if roll < .6 else "DRAFT" if roll < .72 else
                          "REVISION_REQUIRED" if roll < .78 else None)
                if status is None:
                    continue
            else:
                status = "APPROVED"
            r = KpiRealization(assignment_id=a.id, realization_value=D(str(val)), description="Data demo (fiktif)", status=status,
                               version=1 if status != "DRAFT" else 0,
                               submitted_at=None if status == "DRAFT" else utcnow() - timedelta(days=rnd.randint(1, 6)))
            db.session.add(r)
            db.session.flush()
            if status in ("APPROVED", "REVISION_REQUIRED"):
                db.session.add(KpiVerification(realization_id=r.id, verifier_id=verifier_for[a.employee_id], decision=status,
                                               comment="Perlu melampirkan data pendukung." if status == "REVISION_REQUIRED" else "Sesuai bukti."))
        db.session.commit()
        if current:
            for e in emps:  # skor sementara agar dashboard periode berjalan terisi
                wf.recompute_employee(e.id, p.id)
            db.session.commit()
        else:
            wf.close_period(p, admin)
            wf.calculate_period(p, admin)
            wf.finalize_period(p, admin, force=True)
    return {"employees": len(emps), "periods": len(plist)}
