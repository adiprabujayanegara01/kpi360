"""REST API /api/v1 (JSON, session cookie HttpOnly + header X-CSRFToken). Format error seragam."""
import os
from flask import Blueprint, jsonify, request, send_file, current_app
from flask_login import current_user, login_required
from ..extensions import db
from ..models import (Employee, KpiTemplate, EvaluationPeriod, KpiRealization, KpiAssignment, PerformanceScore, Notification,
                      AuditLog, AiInsight, ReportJob, EarlyWarning, ADMIN, SUPERVISOR, EMPLOYEE)
from ..analytics import queries as aq
from ..security.rbac import roles_required, require_employee_access, scope_employee_ids, team_ids, can_access_employee
from ..security.ratelimit import rate_limit
from ..services import workflow as wf, reports as rp
from ..services.common import BusinessError, pick_period
from ..ai import insights as ins_svc, chat as chat_svc, llm_client
from ..ai.prediction import predict_employee
from ..ai.early_warning import evaluate_employee

bp = Blueprint("api", __name__)


@bp.before_request
@login_required
def _auth():
    pass


def paginate(q, fn, default=20):
    page, per = max(1, request.args.get("page", 1, type=int)), min(100, max(1, request.args.get("per_page", default, type=int)))
    p = q.paginate(page=page, per_page=per, error_out=False)
    return jsonify(items=[fn(x) for x in p.items], page=page, per_page=per, total=p.total, pages=p.pages)


def _period_or_404(pid):
    return db.get_or_404(EvaluationPeriod, pid)


def _body():
    return request.get_json(silent=True) or request.form or {}


@bp.get("/me")
def me():
    u = current_user
    return jsonify(id=u.id, username=u.username, role=u.role, employee_id=u.employee.id if u.employee else None, name=u.display_name)


# ---------------------------------------------------------------- master
@bp.get("/employees")
@roles_required(ADMIN, SUPERVISOR)
def employees():
    q = Employee.query
    if current_user.role == SUPERVISOR:
        q = q.filter(Employee.id.in_(team_ids(current_user) or [-1]))
    return paginate(q.order_by(Employee.name), lambda e: dict(id=e.id, number=e.employee_number, name=e.name, status=e.status,
                                                               department=e.department.name if e.department else None,
                                                               position=e.position.name if e.position else None))


@bp.get("/kpis")
@roles_required(ADMIN)
def kpis():
    return paginate(KpiTemplate.query.order_by(KpiTemplate.kpi_code), lambda k: dict(id=k.id, code=k.kpi_code, name=k.name, type=k.measurement_type,
                                                                                     unit=k.unit, cap=float(k.max_achievement), status=k.status))


@bp.get("/periods")
def periods():
    return paginate(EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()),
                    lambda p: dict(id=p.id, name=p.name, type=p.period_type, start=p.start_date.isoformat(), end=p.end_date.isoformat(),
                                   deadline=p.input_deadline.isoformat(), status=p.status))


@bp.post("/periods/<int:pid>/<action>")
@roles_required(ADMIN)
def period_action(pid, action):
    p = _period_or_404(pid)
    if action == "publish":
        wf.publish_period(p, current_user)
    elif action == "start":
        wf.start_period(p, current_user)
    elif action == "close":
        wf.close_period(p, current_user)
    elif action == "calculate":
        return jsonify(wf.calculate_period(p, current_user))
    elif action == "finalize":
        return jsonify(finalized=wf.finalize_period(p, current_user, force=bool(_body().get("force"))))
    else:
        raise BusinessError("NOT_FOUND", "Aksi tidak dikenal.", 404)
    return jsonify(id=p.id, status=p.status)


@bp.post("/assignments/validate-weights")
@roles_required(ADMIN)
def validate_weights():
    pid = (_body().get("period_id"))
    probs = wf.weight_problems(int(pid))
    return jsonify(valid=not probs, problems=probs)


# ---------------------------------------------------------------- realisasi
def _real_dict(r):
    a = r.assignment
    return dict(id=r.id, assignment_id=a.id, employee_id=a.employee_id, kpi=a.kpi_name_snapshot, target=float(a.target),
                value=float(r.realization_value) if r.realization_value is not None else None, status=r.status, version=r.version,
                achievement=float(r.achievement) if r.achievement is not None else None, description=r.description)


@bp.get("/realizations")
def realizations():
    q = KpiRealization.query.join(KpiAssignment)
    ids = scope_employee_ids(current_user)
    if ids is not None:
        q = q.filter(KpiAssignment.employee_id.in_(ids or [-1]))
    for a, col in (("period_id", KpiAssignment.period_id), ("employee_id", KpiAssignment.employee_id)):
        if request.args.get(a):
            q = q.filter(col == request.args.get(a, type=int))
    if request.args.get("status"):
        q = q.filter(KpiRealization.status == request.args["status"])
    return paginate(q.order_by(KpiRealization.id.desc()), _real_dict)


@bp.post("/realizations")
@roles_required(EMPLOYEE, SUPERVISOR)
def realization_create():
    b = _body()
    r = wf.save_realization(current_user, b.get("assignment_id"), b.get("value"), b.get("description"))
    return jsonify(_real_dict(r)), 201


@bp.put("/realizations/<int:rid>")
@roles_required(EMPLOYEE, SUPERVISOR)
def realization_update(rid):
    r = db.get_or_404(KpiRealization, rid)
    b = _body()
    r = wf.save_realization(current_user, r.assignment_id, b.get("value"), b.get("description"))
    return jsonify(_real_dict(r))


@bp.post("/realizations/<int:rid>/submit")
@roles_required(EMPLOYEE, SUPERVISOR)
def realization_submit(rid):
    return jsonify(_real_dict(wf.submit_realization(current_user, rid)))


@bp.post("/realizations/<int:rid>/<action>")
@roles_required(SUPERVISOR, ADMIN)
def realization_review(rid, action):
    dec = {"approve": "APPROVED", "reject": "REJECTED", "request-revision": "REVISION_REQUIRED"}.get(action)
    if not dec:
        raise BusinessError("NOT_FOUND", "Aksi tidak dikenal.", 404)
    return jsonify(_real_dict(wf.review_realization(current_user, rid, dec, _body().get("comment"))))


# ---------------------------------------------------------------- performa
def _score_dict(r):
    return dict(employee_id=int(r.employee_id), employee=r.employee, period=r.period, score=float(r.score), category=r.category,
                department=r.department, rank=int(getattr(r, "rank", 0)) or None, status=r.status)


@bp.get("/performance/ranking")
@roles_required(ADMIN, SUPERVISOR)
def perf_ranking():
    a = request.args
    pid = a.get("period_id", type=int) or (pick_period().id if pick_period() else -1)
    df = aq.score_rows(current_user, pid, a.get("department_id"), a.get("position_id"), a.get("category"), a.get("min_score"), a.get("max_score"))
    r = aq.ranking(df)
    sort = a.get("sort", "-score")
    if sort == "score":
        r = r.sort_values("score")
    return jsonify(period_id=pid, count=len(r), items=[_score_dict(x) for x in r.itertuples()],
                   warning="Perbandingan antar jabatan berbeda harus dibaca dengan hati-hati.")


@bp.get("/performance/team")
@roles_required(ADMIN, SUPERVISOR)
def perf_team():
    p = pick_period(request.args.get("period_id"))
    df = aq.score_rows(current_user, p.id if p else -1)
    if current_user.role == SUPERVISOR and not df.empty:
        df = df[df.supervisor_id == current_user.employee.id]
    r = aq.ranking(df)
    return jsonify(period=p.name if p else None, summary={k: v for k, v in aq.summary_cards(df).items() if k in ("count", "avg")},
                   items=[_score_dict(x) for x in r.itertuples()])


@bp.get("/performance/department")
@roles_required(ADMIN, SUPERVISOR)
def perf_department():
    p = pick_period(request.args.get("period_id"))
    df = aq.score_rows(current_user, p.id if p else -1)
    return jsonify(period=p.name if p else None, departments=aq.dept_averages(df), distribution=aq.distribution(df),
                   kpi_stats=aq.kpi_stats(current_user, p.id)[:10] if p else [])


@bp.get("/performance/<int:employee_id>")
def perf_employee(employee_id):
    require_employee_access(employee_id)
    hist = aq.employee_history(employee_id)
    pid = request.args.get("period_id", type=int) or (hist[-1]["period_id"] if hist else None)
    return jsonify(employee_id=employee_id, history=[{**h, "start_date": h["start_date"].isoformat()} for h in hist],
                   kpis=aq.kpi_breakdown(employee_id, pid) if pid else [])


# ---------------------------------------------------------------- laporan
@bp.post("/reports")
@rate_limit("reports", 10, 60)
def report_create():
    b = _body()
    params = {k: b.get(k) for k in ("period_id", "employee_id", "department_id") if b.get(k)}
    job = rp.create_job(current_user, b.get("report_type"), b.get("fmt"), params)
    return jsonify(id=job.id, status=job.status), 202


@bp.get("/reports/<int:jid>/download")
def report_download(jid):
    job = db.get_or_404(ReportJob, jid)
    if job.user_id != current_user.id:
        raise BusinessError("NOT_FOUND", "Laporan tidak ditemukan.", 404)
    if job.status != "DONE":
        return jsonify(id=job.id, status=job.status, error=job.error), 202
    return send_file(os.path.join(current_app.config["REPORT_FOLDER"], job.file_path), as_attachment=True,
                     download_name=f"kpi360-{job.report_type}-{job.id}.{job.fmt}")


# ---------------------------------------------------------------- notifikasi
@bp.get("/notifications")
def notifications():
    q = Notification.query.filter_by(user_id=current_user.id)
    if request.args.get("unread"):
        q = q.filter_by(is_read=False)
    return paginate(q.order_by(Notification.id.desc()), lambda n: dict(id=n.id, type=n.type, title=n.title, body=n.body, link=n.link,
                                                                       is_read=n.is_read, created_at=n.created_at.isoformat()))


@bp.post("/notifications/<int:nid>/read")
def notification_read(nid):
    n = db.get_or_404(Notification, nid)
    if n.user_id != current_user.id:
        raise BusinessError("NOT_FOUND", "Notifikasi tidak ditemukan.", 404)
    n.is_read = True
    db.session.commit()
    return jsonify(id=n.id, is_read=True)


# ---------------------------------------------------------------- AI
def _target(b):
    """Tentukan karyawan & periode target dan pastikan pengguna berhak (cegah IDOR)."""
    emp = db.session.get(Employee, int(b.get("employee_id") or (current_user.employee.id if current_user.employee else 0)))
    period = db.session.get(EvaluationPeriod, int(b["period_id"])) if b.get("period_id") else pick_period()
    if not emp or not period:
        raise BusinessError("NOT_FOUND", "Karyawan atau periode tidak ditemukan.", 404)
    require_employee_access(emp.id)
    return emp, period


@bp.post("/ai/performance-analysis")
@rate_limit("ai", 20, 60)
def ai_analysis():
    b = _body()
    emp, period = _target(b)
    try:
        ins, cached = ins_svc.generate_insight(current_user, emp, period, force=bool(b.get("force")))
    except ValueError as e:
        raise BusinessError("NO_DATA", str(e), 422)
    return jsonify(ins_svc.insight_dict(ins) | {"cached": cached, "employee": emp.name})


@bp.post("/ai/recommendation")
@rate_limit("ai", 20, 60)
def ai_recommendation():
    emp, period = _target(_body())
    try:
        ins, _ = ins_svc.generate_insight(current_user, emp, period)
    except ValueError as e:
        raise BusinessError("NO_DATA", str(e), 422)
    return jsonify(recommendations=ins.recommendations, risk_level=ins.risk_level, source=ins.source,
                   label="Dihasilkan AI/otomatis - perlu ditinjau manusia")


@bp.post("/ai/prediction")
@rate_limit("ai", 30, 60)
@roles_required(ADMIN, SUPERVISOR)
def ai_prediction():
    emp, period = _target(_body())
    return jsonify(employee_id=emp.id, **predict_employee(emp.id), alerts=evaluate_employee(emp.id, period))


@bp.post("/ai/chat")
@rate_limit("ai", 20, 60)
def ai_chat():
    from ..security.audit import audit
    q = (_body().get("message") or "")
    res = chat_svc.answer(current_user, q)
    audit("AI_CHAT", "ai", None, new={"q": q[:200], "mode": res["mode"]})
    db.session.commit()
    return jsonify(res)


@bp.post("/ai/insights/<int:iid>/rating")
@roles_required(ADMIN, SUPERVISOR)
def ai_rate(iid):
    ins = db.get_or_404(AiInsight, iid)
    require_employee_access(ins.employee_id, include_self=False)
    v = int(_body().get("rating", 0))
    if v not in (-1, 1):
        raise BusinessError("VALIDATION_ERROR", "rating harus 1 atau -1.", 422)
    ins.quality_rating = v
    db.session.commit()
    return jsonify(id=ins.id, quality_rating=v)


@bp.get("/ai/early-warning")
@roles_required(ADMIN, SUPERVISOR)
def ai_early_warning():
    p = pick_period(request.args.get("period_id"))
    ids = set(s.employee_id for s in aq.visible_scores(current_user).filter_by(period_id=p.id)) if p else set()
    rows = [w for w in EarlyWarning.query.filter_by(period_id=p.id).all() if w.employee_id in ids] if p else []
    return jsonify(period=p.name if p else None, items=[dict(employee_id=w.employee_id, employee=w.employee.name, rule=w.rule, level=w.level, message=w.message) for w in rows])


@bp.get("/audit-logs")
@roles_required(ADMIN)
def audit_logs():
    q = AuditLog.query
    if request.args.get("action"):
        q = q.filter(AuditLog.action == request.args["action"])
    return paginate(q.order_by(AuditLog.id.desc()), lambda a: dict(id=a.id, user=a.username, action=a.action, entity=a.entity_type, entity_id=a.entity_id,
                                                                  old=a.old_value, new=a.new_value, ip=a.ip_address, at=a.created_at.isoformat()), default=30)
