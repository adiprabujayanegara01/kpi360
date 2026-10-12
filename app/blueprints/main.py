import os
from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort, send_from_directory,
                   current_app)
from flask_login import current_user, login_required
from ..extensions import db
from ..models import (Employee, Department, EvaluationPeriod, KpiAssignment, KpiRealization, PerformanceScore, Feedback,
                      EarlyWarning, Notification, AuditLog, ScoreCategory, AiInsight, EvidenceFile, ADMIN, SUPERVISOR, EMPLOYEE)
from ..analytics import queries as aq
from ..security.rbac import roles_required, team_ids, can_access_employee
from ..security.audit import audit
from ..services.common import (pick_period, all_settings, set_setting, DEFAULT_SETTINGS, latest_scored_period, BusinessError)

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return redirect(url_for("main.dashboard") if current_user.is_authenticated else url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    period = pick_period(request.args.get("period_id"))
    periods = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    ctx = dict(period=period, periods=periods)
    if current_user.role == ADMIN:
        df = aq.score_rows(current_user, period.id) if period else aq.score_rows(current_user, -1)
        full = aq.score_rows(current_user)
        cards = aq.summary_cards(df)
        ew = (EarlyWarning.query.filter(EarlyWarning.period_id == period.id, EarlyWarning.level.in_(["HIGH", "MEDIUM"]))
              .order_by(EarlyWarning.level).limit(8).all()) if period else []
        pending = KpiRealization.query.filter_by(status="SUBMITTED").count()
        open_periods = EvaluationPeriod.query.filter(EvaluationPeriod.status.in_(["DRAFT", "PUBLISHED", "RUNNING", "CLOSED"])).all()
        return render_template("dashboard_hr.html", **ctx, cards=cards,
                               n_emp=Employee.query.filter(Employee.status.in_(["Active", "Probation"])).count(),
                               n_dept=Department.query.filter_by(status="ACTIVE").count(),
                               dept=aq.dept_averages(df), dist=aq.distribution(df), trend=aq.trend_by_period(full),
                               kpis=aq.kpi_stats(current_user, period.id)[:8] if period else [], warnings=ew,
                               pending=pending, open_periods=open_periods)
    if current_user.role == SUPERVISOR:
        df = aq.score_rows(current_user, period.id) if period else aq.score_rows(current_user, -1)
        df = df[df.supervisor_id == current_user.employee.id] if not df.empty else df
        r = aq.ranking(df)
        queue = (KpiRealization.query.join(KpiAssignment).join(Employee, Employee.id == KpiAssignment.employee_id)
                 .filter(KpiRealization.status == "SUBMITTED", Employee.supervisor_id == current_user.employee.id).count())
        ids = team_ids(current_user)
        ew = (EarlyWarning.query.filter(EarlyWarning.period_id == period.id, EarlyWarning.employee_id.in_(ids or [-1]))
              .order_by(EarlyWarning.level).limit(8).all()) if period else []
        return render_template("dashboard_sup.html", **ctx, cards=aq.summary_cards(df), n_team=len(ids), ranking=r.to_dict("records"),
                               kpis=aq.kpi_stats(current_user, period.id)[:6] if period else [], queue=queue, warnings=ew,
                               trend=aq.trend_by_period(aq.score_rows(current_user)))
    # EMPLOYEE
    emp = current_user.employee
    hist = aq.employee_history(emp.id) if emp else []
    cur = next((h for h in hist if period and h["period_id"] == period.id), hist[-1] if hist else None)
    running = EvaluationPeriod.query.filter_by(status="RUNNING").order_by(EvaluationPeriod.start_date.desc()).first()
    status_counts, kpi_done, kpi_total = {}, 0, 0
    sp = running or (db.session.get(EvaluationPeriod, cur["period_id"]) if cur else None)
    if emp and sp:
        for a in KpiAssignment.query.filter_by(employee_id=emp.id, period_id=sp.id).all():
            st = a.realization.status if a.realization else "BELUM DIINPUT"
            status_counts[st] = status_counts.get(st, 0) + 1
            kpi_total += 1
            kpi_done += st in ("APPROVED", "CALCULATED")
    recs, rec_src = [], None
    if cur:
        ins = AiInsight.query.filter_by(employee_id=emp.id, period_id=cur["period_id"]).order_by(AiInsight.id.desc()).first()
        if ins:
            recs, rec_src = ins.recommendations, ("AI" if ins.source == "llm" else "otomatis")
        else:
            try:
                from ..ai.insights import build_context, rule_based
                pp = db.session.get(EvaluationPeriod, cur["period_id"])
                recs, rec_src = rule_based(build_context(emp, pp))["recommendations"], "otomatis"
            except Exception:
                recs = []
    fb = Feedback.query.filter_by(employee_id=emp.id).order_by(Feedback.id.desc()).first() if emp else None
    kb = aq.kpi_breakdown(emp.id, cur["period_id"]) if cur else []
    avg_ach = round(sum(k["achievement"] for k in kb) / len(kb), 1) if kb else None
    return render_template("dashboard_emp.html", **ctx, cur=cur, hist=hist, status_counts=status_counts, kpi_done=kpi_done,
                           kpi_total=kpi_total, recs=recs, rec_src=rec_src, fb=fb, avg_ach=avg_ach, running=running)


@bp.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).limit(100).all()
    return render_template("notifications.html", items=items)


@bp.post("/notifications/<int:nid>/read")
@login_required
def notification_read(nid):
    n = db.session.get(Notification, nid)
    if not n or n.user_id != current_user.id:
        abort(404)
    n.is_read = True
    db.session.commit()
    return redirect(n.link or url_for("main.notifications"))


@bp.post("/notifications/read-all")
@login_required
def notification_read_all():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("main.notifications"))


@bp.route("/settings", methods=["GET", "POST"])
@roles_required(ADMIN)
def settings():
    if request.method == "POST":
        old = all_settings()
        try:
            for k in DEFAULT_SETTINGS:
                v = request.form.get(k)
                if v not in (None, ""):
                    float(v.replace(",", "."))
                    set_setting(k, v.replace(",", "."))
        except ValueError:
            raise BusinessError("VALIDATION_ERROR", "Semua pengaturan harus berupa angka.", 422)
        audit("UPDATE_SETTINGS", "settings", None, old=old, new=all_settings())
        db.session.commit()
        flash("Pengaturan disimpan.", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", s=all_settings(), cats=ScoreCategory.query.order_by(ScoreCategory.min_score.desc()).all())


@bp.route("/audit-logs")
@roles_required(ADMIN)
def audit_logs():
    q = AuditLog.query
    for f, col in (("action", AuditLog.action), ("username", AuditLog.username)):
        if request.args.get(f):
            q = q.filter(col.ilike(f"%{request.args[f]}%"))
    page = max(1, int(request.args.get("page", 1)))
    pg = q.order_by(AuditLog.id.desc()).paginate(page=page, per_page=30, error_out=False)
    return render_template("audit_logs.html", pg=pg, args=request.args)


@bp.route("/evidence/<int:eid>")
@login_required
def evidence(eid):
    ev = db.session.get(EvidenceFile, eid)
    if not ev:
        abort(404)
    owner = ev.realization.assignment.employee_id
    if not can_access_employee(current_user, owner):
        abort(403)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], ev.stored_path, as_attachment=True,
                               download_name=ev.original_name, mimetype="application/octet-stream")
