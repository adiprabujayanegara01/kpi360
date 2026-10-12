"""Early warning berbasis aturan eksplisit (EW-01..EW-06). Ambang dapat diatur HR (tabel settings)."""
from ..extensions import db
from ..models import (PerformanceScore, EvaluationPeriod, KpiAssignment, KpiRealization, KpiVerification,
                      EarlyWarning, Employee, User, ADMIN)
from ..services.common import all_settings, notify

LEVEL_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
RULE_TEXT = {
    "EW-01": ("HIGH", "Skor turun 3 periode berturut-turut"),
    "EW-02": ("HIGH", "Skor turun {drop:.1f} poin dibanding periode sebelumnya"),
    "EW-03": ("HIGH", "Skor {score:.1f} berada di bawah {low:g}"),
    "EW-04": ("MEDIUM", "KPI '{kpi}' di bawah {pct:g}% selama 2 periode berturut-turut"),
    "EW-05": ("MEDIUM", "Skor turun 2 periode berturut-turut"),
    "EW-06": ("LOW", "Realisasi diinput mepet deadline atau ditolak berulang"),
}


def max_level(levels):
    levels = [l for l in levels if l]
    return max(levels, key=lambda l: LEVEL_ORDER[l]) if levels else "LOW"


def evaluate_series(scores, kpi_hist=None, rejects=0, late=False, cfg=None):
    """Fungsi murni. scores kronologis (terakhir = periode berjalan). Mengembalikan daftar alert."""
    cfg = cfg or all_settings()
    drop_pts, low, kpi_pct, rej_n = (float(cfg["ew_drop_points"]), float(cfg["ew_low_score"]),
                                     float(cfg["ew_kpi_low_pct"]), int(float(cfg["ew_reject_count"])))
    alerts = []

    def add(rule, **kw):
        level, tpl = RULE_TEXT[rule]
        alerts.append({"rule": rule, "level": level, "message": tpl.format(**kw)})

    s = [float(x) for x in scores]
    down3 = len(s) >= 4 and s[-1] < s[-2] < s[-3] < s[-4]
    down2 = len(s) >= 3 and s[-1] < s[-2] < s[-3]
    if down3:
        add("EW-01")
    if len(s) >= 2 and s[-2] - s[-1] >= drop_pts:
        add("EW-02", drop=s[-2] - s[-1])
    if s and s[-1] < low:
        add("EW-03", score=s[-1], low=low)
    for kpi, hist in (kpi_hist or {}).items():
        if len(hist) >= 2 and hist[-1] < kpi_pct and hist[-2] < kpi_pct:
            add("EW-04", kpi=kpi, pct=kpi_pct)
    if down2 and not down3:
        add("EW-05")
    if rejects >= rej_n or late:
        add("EW-06")
    return alerts


def _gather(employee_id, period):
    periods = (EvaluationPeriod.query.join(PerformanceScore, PerformanceScore.period_id == EvaluationPeriod.id)
               .filter(PerformanceScore.employee_id == employee_id, EvaluationPeriod.start_date <= period.start_date)
               .order_by(EvaluationPeriod.start_date.desc()).limit(6).all())[::-1]
    scores = []
    for p in periods:
        scores.append(float(PerformanceScore.query.filter_by(employee_id=employee_id, period_id=p.id).first().total_score))
    kpi_hist = {}
    for p in periods:
        for a in KpiAssignment.query.filter_by(employee_id=employee_id, period_id=p.id).all():
            r = a.realization
            ach = float(r.achievement) if r and r.status in ("APPROVED", "CALCULATED") and r.achievement is not None else 0.0
            kpi_hist.setdefault(a.kpi_name_snapshot, []).append(ach)
    rejects = (KpiVerification.query.join(KpiRealization).join(KpiAssignment, KpiAssignment.id == KpiRealization.assignment_id)
               .filter(KpiAssignment.employee_id == employee_id, KpiAssignment.period_id == period.id,
                       KpiVerification.decision.in_(["REJECTED", "REVISION_REQUIRED"])).count())
    asg = KpiAssignment.query.filter_by(employee_id=employee_id, period_id=period.id).all()
    late_n = sum(1 for a in asg if a.realization and a.realization.submitted_at
                 and a.realization.submitted_at.date() >= period.input_deadline)
    late = bool(asg) and late_n * 2 >= len(asg)
    return scores, kpi_hist, rejects, late


def top_influencers(employee_id, period, limit=3):
    """KPI yang paling menurunkan skor: selisih kontribusi berbobot dibanding periode sebelumnya."""
    prev = (EvaluationPeriod.query.join(PerformanceScore, PerformanceScore.period_id == EvaluationPeriod.id)
            .filter(PerformanceScore.employee_id == employee_id, EvaluationPeriod.start_date < period.start_date)
            .order_by(EvaluationPeriod.start_date.desc()).first())
    if not prev:
        return []

    def contrib(pid):
        d = {}
        for a in KpiAssignment.query.filter_by(employee_id=employee_id, period_id=pid).all():
            r = a.realization
            d[a.kpi_name_snapshot] = float(r.weighted_score) if r and r.status in ("APPROVED", "CALCULATED") and r.weighted_score is not None else 0.0
        return d
    cur, old = contrib(period.id), contrib(prev.id)
    deltas = [(k, round(cur.get(k, 0) - old.get(k, 0), 2)) for k in cur if k in old]
    deltas.sort(key=lambda x: x[1])
    return [{"kpi": k, "delta": d} for k, d in deltas[:limit] if d < 0]


def evaluate_employee(employee_id, period):
    scores, kpi_hist, rejects, late = _gather(employee_id, period)
    return evaluate_series(scores, kpi_hist, rejects, late)


def run_for_period(period):
    """Evaluasi semua karyawan bersekor pada periode; simpan & notifikasi peringatan baru (MEDIUM/HIGH)."""
    n_new = 0
    for s in PerformanceScore.query.filter_by(period_id=period.id).all():
        alerts = evaluate_employee(s.employee_id, period)
        existing = {w.rule: w for w in EarlyWarning.query.filter_by(employee_id=s.employee_id, period_id=period.id)}
        keep = set()
        for al in alerts:
            keep.add(al["rule"])
            if al["rule"] in existing:
                existing[al["rule"]].message, existing[al["rule"]].level = al["message"], al["level"]
                continue
            db.session.add(EarlyWarning(employee_id=s.employee_id, period_id=period.id, rule=al["rule"],
                                        level=al["level"], message=al["message"]))
            n_new += 1
            if al["level"] in ("MEDIUM", "HIGH"):
                emp = s.employee
                inf = top_influencers(s.employee_id, period)
                extra = (" KPI paling berpengaruh: " + ", ".join(f"{i['kpi']} ({i['delta']:+.1f})" for i in inf)) if inf else ""
                sup = db.session.get(Employee, s.supervisor_id_snapshot) if s.supervisor_id_snapshot else None
                body = f"{emp.name}: {al['message']}.{extra}"
                if sup:
                    notify(sup.user_id, "EARLY_WARNING", "Performa anggota tim menurun", body, f"/performance/{emp.id}")
                for u in User.query.filter_by(role=ADMIN, status="ACTIVE").all():
                    notify(u.id, "EARLY_WARNING", "Performance alert", body, f"/performance/{emp.id}")
        for rule, w in existing.items():
            if rule not in keep:
                db.session.delete(w)
    db.session.commit()
    return n_new
