"""Prediksi skor periode berikutnya.
Tahap 1 (baseline): regresi linear sederhana pada skor historis (min. 4 periode).
Tahap 2 (ML): GradientBoosting dengan fitur lag/tren/kelengkapan; dipakai HANYA bila MAE
hold-out (periode terakhir) lebih baik daripada baseline."""
import numpy as np
from ..extensions import db
from ..models import PerformanceScore, EvaluationPeriod, KpiAssignment, KpiRealization
from ..kpi_engine import categorize
from ..services.common import category_tuples

MIN_PERIODS = 4
_cache = {}


def baseline_predict(scores, window=4):
    s = np.array(scores[-window:], dtype=float)
    x = np.arange(len(s))
    slope, intercept = np.polyfit(x, s, 1)
    return float(np.clip(slope * len(s) + intercept, 0, 100))


def _risk(pred, last):
    drop = last - pred
    if pred < 60 or drop >= 10:
        return "HIGH"
    if pred < 70 or drop >= 5:
        return "MEDIUM"
    return "LOW"


def _features(lags, inc1=0, minach1=100.0):
    l1, l2, l3 = lags[-1], lags[-2], lags[-3]
    return [l1, l2, l3, (l1 - l3) / 2, float(np.mean(lags[-3:])), float(np.std(lags[-3:])), inc1, minach1]


def _series_by_employee():
    rows = (db.session.query(PerformanceScore.employee_id, EvaluationPeriod.id, EvaluationPeriod.start_date,
                             PerformanceScore.total_score, PerformanceScore.incomplete_count)
            .join(EvaluationPeriod, EvaluationPeriod.id == PerformanceScore.period_id)
            .order_by(EvaluationPeriod.start_date).all())
    minach = {(e, p): float(m) if m is not None else 0.0 for e, p, m in
              db.session.query(KpiAssignment.employee_id, KpiAssignment.period_id, db.func.min(KpiRealization.achievement))
              .outerjoin(KpiRealization, KpiRealization.assignment_id == KpiAssignment.id)
              .group_by(KpiAssignment.employee_id, KpiAssignment.period_id).all()}
    by = {}
    for e, pid, sd, tot, inc in rows:
        by.setdefault(e, []).append((sd, pid, float(tot), inc, minach.get((e, pid), 100.0)))
    return by


def _train(by):
    """Kembalikan (model|None, info). Hold-out = periode terakhir yang dipakai sebagai target."""
    X, y, when = [], [], []
    for e, seq in by.items():
        for t in range(3, len(seq)):
            lags = [x[2] for x in seq[:t]]
            X.append(_features(lags, seq[t - 1][3], seq[t - 1][4]))
            y.append(seq[t][2])
            when.append(seq[t][0])
    info = {"rows": len(y), "mae_ml": None, "mae_baseline": None}
    if len(y) < 60:
        return None, info
    X, y, when = np.array(X), np.array(y), np.array(when)
    last = when.max()
    test = when == last
    if test.sum() < 8 or (~test).sum() < 40:
        return None, info
    from sklearn.ensemble import GradientBoostingRegressor
    m = GradientBoostingRegressor(n_estimators=120, max_depth=3, learning_rate=0.06, subsample=0.9, random_state=7)
    m.fit(X[~test], y[~test])
    mae_ml = float(np.mean(np.abs(m.predict(X[test]) - y[test])))
    base = np.array([float(np.clip(np.polyval(np.polyfit(range(3), [r[2], r[1], r[0]], 1), 3), 0, 100)) for r in X[test]])
    mae_b = float(np.mean(np.abs(base - y[test])))
    info.update(mae_ml=round(mae_ml, 2), mae_baseline=round(mae_b, 2))
    if mae_ml < mae_b * 0.98:
        m.fit(X, y)  # latih ulang pada seluruh data
        return m, info
    return None, info


def predict_employee(employee_id):
    """Prediksi periode berikutnya untuk satu karyawan. None bila data < 4 periode."""
    by = _series_by_employee()
    seq = by.get(employee_id, [])
    if len(seq) < MIN_PERIODS:
        return {"available": False, "reason": f"Data historis kurang (minimal {MIN_PERIODS} periode, ada {len(seq)})."}
    scores = [x[2] for x in seq]
    key = (len(seq), sum(len(v) for v in by.values()), max(x[1] for v in by.values() for x in v))
    if key not in _cache:
        _cache.clear()
        _cache[key] = _train(by)
    model, info = _cache[key]
    base = baseline_predict(scores)
    method, pred = "baseline_linear", base
    if model is not None:
        pred = float(np.clip(model.predict(np.array([_features(scores, seq[-1][3], seq[-1][4])]))[0], 0, 100))
        method = "gradient_boosting"
    # ketidakpastian: MAE one-step-ahead pada riwayat sendiri / MAE model organisasi
    errs = [abs(baseline_predict(scores[:i]) - scores[i]) for i in range(MIN_PERIODS, len(scores))] if len(scores) > MIN_PERIODS else []
    mae = info["mae_ml"] if method == "gradient_boosting" else (np.mean(errs) if errs else (info["mae_baseline"] or 4.0))
    margin = max(1.0, 1.5 * float(mae))
    low, high = max(0.0, pred - margin), min(100.0, pred + margin)
    return {"available": True, "predicted_score": round(pred, 2), "low": round(low, 2), "high": round(high, 2),
            "category": categorize(round(pred, 2), category_tuples()), "risk_level": _risk(pred, scores[-1]),
            "method": method, "last_score": scores[-1], "n_periods": len(scores),
            "model_info": {**info, "note": "ML dipakai hanya bila lebih akurat dari baseline pada hold-out."}}
