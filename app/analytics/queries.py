"""Agregasi data kinerja dengan Pandas. Semua fungsi publik menghormati cakupan peran (visible_scores)."""
import pandas as pd
from ..extensions import db
from ..models import (PerformanceScore, Employee, EvaluationPeriod, Department, Position, KpiAssignment,
                      KpiRealization, ScoreCategory)
from ..security.rbac import visible_scores, scope_employee_ids

COLS = ["score_id", "employee_id", "employee", "emp_number", "period_id", "period", "start_date", "score", "category",
        "status", "incomplete", "department_id", "department", "position_id", "position", "supervisor_id"]


def _df(rows):
    df = pd.DataFrame(rows, columns=COLS)
    if not df.empty:
        df["score"] = df["score"].astype(float)
    return df


def _rows(q):
    return (q.join(Employee, Employee.id == PerformanceScore.employee_id)
            .join(EvaluationPeriod, EvaluationPeriod.id == PerformanceScore.period_id)
            .outerjoin(Department, Department.id == PerformanceScore.department_id_snapshot)
            .outerjoin(Position, Position.id == PerformanceScore.position_id_snapshot)
            .with_entities(PerformanceScore.id, Employee.id, Employee.name, Employee.employee_number,
                           EvaluationPeriod.id, EvaluationPeriod.name, EvaluationPeriod.start_date,
                           PerformanceScore.total_score, PerformanceScore.category, PerformanceScore.status,
                           PerformanceScore.incomplete_count, PerformanceScore.department_id_snapshot, Department.name,
                           PerformanceScore.position_id_snapshot, Position.name,
                           PerformanceScore.supervisor_id_snapshot).all())


def score_rows(user, period_id=None, department_id=None, position_id=None, category=None, min_score=None,
               max_score=None, employee_id=None):
    q = visible_scores(user)
    if period_id:
        q = q.filter(PerformanceScore.period_id == int(period_id))
    if department_id:
        q = q.filter(PerformanceScore.department_id_snapshot == int(department_id))
    if position_id:
        q = q.filter(PerformanceScore.position_id_snapshot == int(position_id))
    if category:
        q = q.filter(PerformanceScore.category == category)
    if min_score not in (None, ""):
        q = q.filter(PerformanceScore.total_score >= float(min_score))
    if max_score not in (None, ""):
        q = q.filter(PerformanceScore.total_score <= float(max_score))
    if employee_id:
        q = q.filter(PerformanceScore.employee_id == int(employee_id))
    return _df(_rows(q))


def all_scores_df():
    """Tanpa filter peran - hanya untuk agregat internal (mis. rata-rata departemen)."""
    return _df(_rows(PerformanceScore.query))


def ranking(df):
    if df.empty:
        return df.assign(rank=[])
    out = df.sort_values(["score", "employee"], ascending=[False, True]).reset_index(drop=True)
    out["rank"] = out["score"].rank(method="min", ascending=False).astype(int)
    return out


def dept_averages(df):
    if df.empty:
        return []
    g = df.groupby("department", dropna=False)["score"].agg(["mean", "count"]).reset_index()
    g["mean"] = g["mean"].round(2)
    return g.sort_values("mean", ascending=False).rename(columns={"mean": "avg", "count": "n"}).to_dict("records")


def distribution(df):
    cats = ScoreCategory.query.order_by(ScoreCategory.min_score.desc()).all()
    counts = df["category"].value_counts().to_dict() if not df.empty else {}
    return [{"category": c.name, "count": int(counts.get(c.name, 0)), "color": c.color} for c in cats]


def trend_by_period(df):
    if df.empty:
        return []
    g = df.groupby(["period_id", "period", "start_date"])["score"].mean().reset_index().sort_values("start_date")
    return [{"period": r.period, "avg": round(r.score, 2)} for r in g.itertuples()]


def summary_cards(df):
    if df.empty:
        return {"count": 0, "avg": None, "top": None, "low": None}
    r = ranking(df)
    return {"count": int(len(df)), "avg": round(float(df["score"].mean()), 2),
            "top": r.iloc[0].to_dict(), "low": r.iloc[-1].to_dict()}


# --------------------------------------------------------------------- KPI level
def _kpi_rows(user, period_ids, department_id=None, kpi_category=None, employee_id=None):
    q = (db.session.query(KpiAssignment.period_id, KpiAssignment.employee_id, KpiAssignment.kpi_name_snapshot,
                          KpiAssignment.category_snapshot, KpiAssignment.weight, KpiRealization.achievement,
                          KpiRealization.weighted_score, KpiRealization.status)
         .join(PerformanceScore, db.and_(PerformanceScore.employee_id == KpiAssignment.employee_id,
                                         PerformanceScore.period_id == KpiAssignment.period_id))
         .outerjoin(KpiRealization, KpiRealization.assignment_id == KpiAssignment.id)
         .filter(KpiAssignment.period_id.in_(period_ids)))
    ids = scope_employee_ids(user)
    if ids is not None:
        from ..models import ADMIN, SUPERVISOR
        if user.role == SUPERVISOR and user.employee:
            ids = ids + [user.employee.id]
        q = q.filter(KpiAssignment.employee_id.in_(ids or [-1]))
    if department_id:
        q = q.filter(PerformanceScore.department_id_snapshot == int(department_id))
    if kpi_category:
        q = q.filter(KpiAssignment.category_snapshot == kpi_category)
    if employee_id:
        q = q.filter(KpiAssignment.employee_id == int(employee_id))
    df = pd.DataFrame(q.all(), columns=["period_id", "employee_id", "kpi", "category", "weight", "ach", "weighted", "status"])
    if df.empty:
        return df
    for c in ("weight", "ach", "weighted"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["counted"] = df["status"].isin(["APPROVED", "CALCULATED"])
    df["ach"] = df["ach"].where(df["counted"], 0.0).fillna(0.0)
    df["weighted"] = df["weighted"].where(df["counted"], 0.0).fillna(0.0)
    return df


def kpi_stats(user, period_id, department_id=None, kpi_category=None):
    """Per KPI: rata-rata achievement dan jumlah yang tidak tercapai (<100)."""
    df = _kpi_rows(user, [int(period_id)], department_id, kpi_category)
    if df.empty:
        return []
    g = df.groupby("kpi").agg(avg=("ach", "mean"), n=("ach", "size"),
                              unmet=("ach", lambda s: int((s < 100).sum())),
                              incomplete=("counted", lambda s: int((~s).sum()))).reset_index()
    g["avg"] = g["avg"].round(2)
    g["unmet_pct"] = (g["unmet"] / g["n"] * 100).round(1)
    return g.sort_values(["unmet_pct", "avg"], ascending=[False, True]).to_dict("records")


def kpi_avg_by_period(user, period_ids, department_id=None):
    df = _kpi_rows(user, period_ids, department_id)
    if df.empty:
        return df
    return df.groupby(["period_id", "kpi"]).agg(avg=("ach", "mean"), wavg=("weighted", "mean")).reset_index()


# --------------------------------------------------------------------- individu
def kpi_breakdown(employee_id, period_id):
    out = []
    for a in (KpiAssignment.query.filter_by(employee_id=employee_id, period_id=period_id)
              .order_by(KpiAssignment.id).all()):
        r = a.realization
        counted = bool(r and r.status in ("APPROVED", "CALCULATED"))
        out.append({"assignment_id": a.id, "kpi": a.kpi_name_snapshot, "category": a.category_snapshot,
                    "type": a.measurement_type_snapshot, "unit": a.unit_snapshot, "target": float(a.target),
                    "weight": float(a.weight), "realization": float(r.realization_value) if r and r.realization_value is not None else None,
                    "achievement": float(r.achievement) if counted and r.achievement is not None else 0.0,
                    "weighted": float(r.weighted_score) if counted and r.weighted_score is not None else 0.0,
                    "status": r.status if r else "BELUM DIINPUT", "incomplete": not counted})
    return out


def employee_history(employee_id, upto_start=None):
    """Riwayat skor individu + rata-rata departemen pada periode yang sama."""
    q = (PerformanceScore.query.join(EvaluationPeriod, EvaluationPeriod.id == PerformanceScore.period_id)
         .filter(PerformanceScore.employee_id == employee_id).order_by(EvaluationPeriod.start_date))
    if upto_start:
        q = q.filter(EvaluationPeriod.start_date <= upto_start)
    rows = q.all()
    if not rows:
        return []
    allf = all_scores_df()
    out = []
    for s in rows:
        peers = allf[(allf.period_id == s.period_id) & (allf.department_id == s.department_id_snapshot)]
        out.append({"period_id": s.period_id, "period": s.period.name, "start_date": s.period.start_date,
                    "score": float(s.total_score), "category": s.category, "status": s.status,
                    "dept_avg": round(float(peers["score"].mean()), 2) if not peers.empty else None,
                    "incomplete": s.incomplete_count})
    return out


def category_radar(employee_id, period_id):
    df = _kpi_rows_for_employee(employee_id, period_id)
    if df.empty:
        return []
    g = df.groupby("category")["ach"].mean().round(2)
    return [{"category": k or "Lainnya", "avg": float(v)} for k, v in g.items()]


def _kpi_rows_for_employee(employee_id, period_id):
    q = (db.session.query(KpiAssignment.category_snapshot, KpiRealization.achievement, KpiRealization.status)
         .outerjoin(KpiRealization, KpiRealization.assignment_id == KpiAssignment.id)
         .filter(KpiAssignment.employee_id == employee_id, KpiAssignment.period_id == period_id))
    df = pd.DataFrame(q.all(), columns=["category", "ach", "status"])
    if df.empty:
        return df
    df["ach"] = pd.to_numeric(df["ach"], errors="coerce")
    df["ach"] = df["ach"].where(df["status"].isin(["APPROVED", "CALCULATED"]), 0.0).fillna(0.0)
    return df
