from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file, current_app
from flask_login import current_user, login_required
import os
from ..extensions import db
from ..models import (Employee, EvaluationPeriod, PerformanceScore, Feedback, EarlyWarning, AiInsight, Department, Position,
                      ScoreCategory, KpiCategory, ReportJob, ADMIN, SUPERVISOR, EMPLOYEE)
from ..analytics import queries as aq
from ..security.rbac import roles_required, require_employee_access, can_access_employee
from ..security.ratelimit import rate_limit
from ..services import workflow as wf
from ..services import reports as rp
from ..services.common import pick_period, BusinessError

bp = Blueprint("perf", __name__)


@bp.route("/performance")
@login_required
def performance():
    if current_user.role == EMPLOYEE:
        return redirect(url_for("perf.performance_detail", employee_id=current_user.employee.id))
    periods = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    period = pick_period(request.args.get("period_id"))
    a = request.args
    df = aq.score_rows(current_user, period.id if period else -1, a.get("department_id"), a.get("position_id"), a.get("category"),
                       a.get("min_score"), a.get("max_score"))
    if current_user.role == SUPERVISOR and not df.empty:
        df = df[df.supervisor_id == current_user.employee.id]
    r = aq.ranking(df)
    return render_template("performance.html", period=period, periods=periods, rows=r.to_dict("records"), args=a,
                           depts=Department.query.all(), poss=Position.query.all(),
                           cats=ScoreCategory.query.order_by(ScoreCategory.min_score.desc()).all(), cards=aq.summary_cards(df))


@bp.route("/performance/<int:employee_id>")
@login_required
def performance_detail(employee_id):
    require_employee_access(employee_id)
    emp = db.get_or_404(Employee, employee_id)
    hist = aq.employee_history(emp.id)
    pid = request.args.get("period_id")
    period = db.session.get(EvaluationPeriod, int(pid)) if pid else (db.session.get(EvaluationPeriod, hist[-1]["period_id"]) if hist else None)
    score = PerformanceScore.query.filter_by(employee_id=emp.id, period_id=period.id).first() if period else None
    kpis = aq.kpi_breakdown(emp.id, period.id) if period else []
    insight = (AiInsight.query.filter_by(employee_id=emp.id, period_id=period.id).order_by(AiInsight.id.desc()).first() if period else None)
    pred = None
    if current_user.role in (ADMIN, SUPERVISOR) and hist and emp.id != getattr(current_user.employee, "id", None):
        from ..ai.prediction import predict_employee
        pred = predict_employee(emp.id)
    return render_template("performance_detail.html", emp=emp, hist=hist, period=period, score=score, kpis=kpis,
                           radar=aq.category_radar(emp.id, period.id) if period else [],
                           feedback=Feedback.query.filter_by(employee_id=emp.id).order_by(Feedback.id.desc()).limit(5).all(),
                           warnings=EarlyWarning.query.filter_by(employee_id=emp.id, period_id=period.id).all() if period else [],
                           insight=insight, pred=pred, periods=EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all())


@bp.post("/performance/score/<int:score_id>/adjust")
@roles_required(ADMIN)
def adjust(score_id):
    s = db.get_or_404(PerformanceScore, score_id)
    wf.adjust_score(current_user, s, request.form.get("new_total"), request.form.get("reason"))
    flash("Adjustment tersimpan dan tercatat di audit log.", "success")
    return redirect(url_for("perf.performance_detail", employee_id=s.employee_id, period_id=s.period_id))


@bp.route("/team-performance")
@roles_required(SUPERVISOR, ADMIN)
def team_performance():
    periods = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    period = pick_period(request.args.get("period_id"))
    df = aq.score_rows(current_user, period.id if period else -1)
    if current_user.role == SUPERVISOR and not df.empty:
        df = df[df.supervisor_id == current_user.employee.id]
    r = aq.ranking(df)
    warn = {}
    if period:
        for w in EarlyWarning.query.filter_by(period_id=period.id).all():
            warn.setdefault(w.employee_id, []).append(w)
    return render_template("team_performance.html", period=period, periods=periods, rows=r.to_dict("records"), warn=warn,
                           kpis=aq.kpi_stats(current_user, period.id)[:8] if period else [], cards=aq.summary_cards(df))


@bp.route("/analytics")
@roles_required(ADMIN, SUPERVISOR)
def analytics():
    periods = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    period = pick_period(request.args.get("period_id"))
    dept = request.args.get("department_id") or None
    kcat = request.args.get("kpi_category") or None
    df = aq.score_rows(current_user, period.id if period else -1, dept)
    full = aq.score_rows(current_user, department_id=dept)
    return render_template("analytics.html", period=period, periods=periods, args=request.args, depts=Department.query.all(),
                           kcats=KpiCategory.query.all(), dept=aq.dept_averages(df), dist=aq.distribution(df), trend=aq.trend_by_period(full),
                           kpis=aq.kpi_stats(current_user, period.id, dept, kcat) if period else [], cards=aq.summary_cards(df))


@bp.route("/reports", methods=["GET", "POST"])
@login_required
@rate_limit("reports", 10, 60)
def reports():
    if request.method == "POST":
        f = request.form
        params = {"period_id": f.get("period_id")}
        for k in ("employee_id", "department_id"):
            if f.get(k):
                params[k] = f[k]
        job = rp.create_job(current_user, f.get("report_type"), f.get("fmt"), params)
        flash("Laporan sedang dibuat" if job.status == "PENDING" else "Laporan siap diunduh.", "success")
        return redirect(url_for("perf.reports"))
    jobs = ReportJob.query.filter_by(user_id=current_user.id).order_by(ReportJob.id.desc()).limit(15).all()
    emps = []
    if current_user.role == ADMIN:
        emps = Employee.query.order_by(Employee.name).all()
    elif current_user.role == SUPERVISOR:
        from ..security.rbac import team_ids
        emps = Employee.query.filter(Employee.id.in_(team_ids(current_user) + [current_user.employee.id])).order_by(Employee.name).all()
    return render_template("reports.html", jobs=jobs, periods=EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all(),
                           emps=emps, depts=Department.query.all(), formats=rp.FORMATS)


@bp.route("/reports/<int:jid>/download")
@login_required
def report_download(jid):
    job = db.get_or_404(ReportJob, jid)
    if job.user_id != current_user.id or job.status != "DONE":
        abort(404)
    path = os.path.join(current_app.config["REPORT_FOLDER"], job.file_path)
    return send_file(path, as_attachment=True, download_name=f"kpi360-{job.report_type}-{job.id}.{job.fmt}")
