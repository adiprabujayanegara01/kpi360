"""Siklus penilaian: periode, assignment, realisasi (employee), verifikasi (supervisor), evaluasi/feedback."""
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import (EvaluationPeriod, KpiAssignment, KpiRealization, Employee, Department, Position, KpiTemplate, Feedback,
                      PerformanceScore, ADMIN, SUPERVISOR, EMPLOYEE)
from ..security.rbac import roles_required, team_ids, can_access_employee, require_employee_access
from ..security.audit import audit
from ..security.uploads import save_evidence, UploadError
from ..services import workflow as wf
from ..services.common import BusinessError, notify, today

bp = Blueprint("cycle", __name__)
PERIOD_TYPES = [("MONTHLY", "Bulanan"), ("QUARTERLY", "Kuartalan"), ("SEMESTER", "Semester"), ("YEARLY", "Tahunan"), ("CUSTOM", "Kustom")]


# ----------------------------------------------------------------- periode
def _period_fields():
    return [dict(name="name", label="Nama periode", type="text", required=True, help="mis. Q1 2027"),
            dict(name="period_type", label="Jenis", type="select", options=PERIOD_TYPES),
            dict(name="start_date", label="Tanggal mulai", type="date", required=True),
            dict(name="end_date", label="Tanggal selesai", type="date", required=True),
            dict(name="input_deadline", label="Batas input realisasi", type="date", required=True)]


@bp.route("/periods")
@roles_required(ADMIN)
def periods():
    rows = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    stats = {p.id: dict(assign=KpiAssignment.query.filter_by(period_id=p.id).count(),
                        emps=db.session.query(KpiAssignment.employee_id).filter_by(period_id=p.id).distinct().count(),
                        pending=KpiRealization.query.join(KpiAssignment).filter(KpiAssignment.period_id == p.id, KpiRealization.status == "SUBMITTED").count())
             for p in rows}
    return render_template("periods.html", periods=rows, stats=stats)


@bp.route("/periods/create", methods=["GET", "POST"])
@bp.route("/periods/<int:id>/edit", methods=["GET", "POST"], endpoint="period_edit")
@roles_required(ADMIN)
def period_form(id=None):
    from .master import _parse
    p = db.get_or_404(EvaluationPeriod, id) if id else None
    if p and p.status != "DRAFT":
        raise BusinessError("PERIOD_LOCKED", "Hanya periode DRAFT yang dapat diubah.")
    fields = _period_fields()
    if request.method == "POST":
        try:
            new = p or EvaluationPeriod()
            for f in fields:
                setattr(new, f["name"], _parse(f, request.form.get(f["name"])))
            if new.end_date < new.start_date:
                raise ValueError("Tanggal selesai tidak boleh sebelum tanggal mulai.")
            if not (new.start_date <= new.input_deadline):
                raise ValueError("Batas input harus setelah tanggal mulai.")
            db.session.add(new)
            db.session.flush()
            audit("UPDATE_PERIOD" if p else "CREATE_PERIOD", "evaluation_periods", new.id, new={"name": new.name})
            db.session.commit()
            flash("Periode disimpan.", "success")
            return redirect(url_for("cycle.periods"))
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "danger")
    vals = {f["name"]: (request.form.get(f["name"]) if request.method == "POST" else (getattr(p, f["name"]) if p else None)) for f in fields}
    if not p and request.method == "GET":
        vals["period_type"] = "MONTHLY"
    return render_template("form.html", title="Ubah Periode" if p else "Buat Periode", fields=fields, values=vals, back=url_for("cycle.periods"))


@bp.post("/periods/<int:id>/<action>")
@roles_required(ADMIN)
def period_action(id, action):
    p = db.get_or_404(EvaluationPeriod, id)
    fn = {"publish": wf.publish_period, "start": wf.start_period, "close": wf.close_period}.get(action)
    msg = {"publish": "Periode dipublish; karyawan dinotifikasi.", "start": "Periode berjalan (RUNNING).", "close": "Periode ditutup."}
    if fn:
        fn(p, current_user)
        flash(msg[action], "success")
    elif action == "calculate":
        r = wf.calculate_period(p, current_user)
        flash(f"Skor dihitung untuk {r['employees']} karyawan; {r['incomplete']} KPI incomplete.", "success")
    elif action == "finalize":
        n = wf.finalize_period(p, current_user, force=request.form.get("force") == "1")
        flash(f"Periode difinalisasi; {n} skor terkunci.", "success")
    else:
        abort(404)
    return redirect(url_for("cycle.periods"))


# ----------------------------------------------------------------- assignment
@bp.route("/kpi-assignments")
@roles_required(ADMIN)
def assignments():
    periods = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    pid = request.args.get("period_id") or (periods[0].id if periods else None)
    period = db.session.get(EvaluationPeriod, int(pid)) if pid else None
    rows, problems = [], []
    if period:
        q = (db.session.query(Employee, db.func.count(KpiAssignment.id), db.func.sum(KpiAssignment.weight))
             .join(KpiAssignment, KpiAssignment.employee_id == Employee.id).filter(KpiAssignment.period_id == period.id)
             .group_by(Employee.id).order_by(Employee.name))
        if request.args.get("department_id"):
            q = q.filter(Employee.department_id == int(request.args["department_id"]))
        rows = q.all()
        problems = wf.weight_problems(period.id)
    return render_template("assignments.html", periods=periods, period=period, rows=rows, problems={p["employee_id"] for p in problems},
                           depts=Department.query.all(), poss=Position.query.all(), args=request.args)


@bp.post("/assignments/bulk")
@roles_required(ADMIN)
def assignments_bulk():
    p = db.get_or_404(EvaluationPeriod, int(request.form["period_id"]))
    mode = request.form.get("mode")
    if mode == "copy":
        src = db.get_or_404(EvaluationPeriod, int(request.form["source_period_id"]))
        r = wf.copy_assignments(p, src, current_user)
        flash(f"{r['created']} penugasan disalin dari {src.name}.", "success")
    else:
        r = wf.bulk_assign(p, current_user, request.form.get("position_id") or None, request.form.get("department_id") or None)
        flash(f"{r['created']} penugasan dibuat, {r['skipped']} dilewati (sudah ada).", "success")
    return redirect(url_for("cycle.assignments", period_id=p.id))


@bp.route("/kpi-assignments/<int:period_id>/<int:employee_id>", methods=["GET", "POST"])
@roles_required(ADMIN)
def assignment_edit(period_id, employee_id):
    period = db.get_or_404(EvaluationPeriod, period_id)
    emp = db.get_or_404(Employee, employee_id)
    if request.method == "POST":
        act = request.form.get("action")
        if act == "add":
            t = db.get_or_404(KpiTemplate, int(request.form["kpi_template_id"]))
            wf.add_assignment(period, emp, t, request.form.get("target") or t.default_target or 1, request.form.get("weight") or 0, current_user)
            flash("KPI ditambahkan.", "success")
        elif act == "remove":
            a = db.get_or_404(KpiAssignment, int(request.form["assignment_id"]))
            wf.remove_assignment(period, a, current_user)
            flash("KPI dihapus dari penugasan.", "success")
        else:
            ids = request.form.getlist("id")
            rows = [{"id": i, "target": request.form.get(f"target_{i}"), "weight": request.form.get(f"weight_{i}")} for i in ids]
            total = wf.save_employee_assignments(period, emp, rows, request.form.get("reason"), current_user)
            flash(f"Tersimpan. Total bobot {total}%." + ("" if total == 100 else " Belum 100% - periode belum dapat dipublish (BR-01)."), "success" if total == 100 else "warning")
        return redirect(url_for("cycle.assignment_edit", period_id=period_id, employee_id=employee_id))
    items = KpiAssignment.query.filter_by(period_id=period_id, employee_id=employee_id).order_by(KpiAssignment.id).all()
    used = {a.kpi_template_id for a in items}
    avail = [t for t in KpiTemplate.query.filter_by(status="ACTIVE").order_by(KpiTemplate.name) if t.id not in used]
    return render_template("assignment_edit.html", period=period, emp=emp, items=items, avail=avail,
                           total=sum((a.weight for a in items), 0))


# ----------------------------------------------------------------- realisasi (employee)
@bp.route("/my-kpi")
@roles_required(EMPLOYEE, SUPERVISOR)
def my_kpi():
    emp = current_user.employee
    periods = (EvaluationPeriod.query.join(KpiAssignment, KpiAssignment.period_id == EvaluationPeriod.id)
               .filter(KpiAssignment.employee_id == emp.id).distinct().order_by(EvaluationPeriod.start_date.desc()).all())
    pid = request.args.get("period_id")
    period = db.session.get(EvaluationPeriod, int(pid)) if pid else next((p for p in periods if p.status == "RUNNING"), periods[0] if periods else None)
    items = KpiAssignment.query.filter_by(employee_id=emp.id, period_id=period.id).order_by(KpiAssignment.id).all() if period else []
    can_input = bool(period and period.status == "RUNNING" and today() <= period.input_deadline)
    return render_template("my_kpi.html", periods=periods, period=period, items=items, can_input=can_input, editable=wf.EDITABLE)


@bp.route("/kpi-realization/<int:assignment_id>", methods=["GET", "POST"])
@roles_required(EMPLOYEE, SUPERVISOR)
def realization(assignment_id):
    a = db.get_or_404(KpiAssignment, assignment_id)
    if not current_user.employee or a.employee_id != current_user.employee.id:
        abort(403)
    if request.method == "POST":
        act = request.form.get("action", "save")
        r = wf.save_realization(current_user, assignment_id, request.form.get("value"), request.form.get("description"))
        for f in request.files.getlist("evidence"):
            if f and f.filename:
                try:
                    wf.attach_evidence(current_user, r, save_evidence(f))
                except UploadError as e:
                    flash(str(e), "danger")
        if act == "submit":
            wf.submit_realization(current_user, r.id)
            flash("Realisasi dikirim ke atasan untuk diverifikasi.", "success")
            return redirect(url_for("cycle.my_kpi", period_id=a.period_id))
        flash("Draft tersimpan.", "success")
        return redirect(url_for("cycle.realization", assignment_id=assignment_id))
    return render_template("realization_form.html", a=a, r=a.realization,
                           can_edit=a.period.status == "RUNNING" and today() <= a.period.input_deadline and (not a.realization or a.realization.status in wf.EDITABLE))


# ----------------------------------------------------------------- verifikasi (supervisor)
@bp.route("/verifications")
@roles_required(SUPERVISOR, ADMIN)
def verifications():
    status = request.args.get("status", "SUBMITTED")
    q = (KpiRealization.query.join(KpiAssignment).join(Employee, Employee.id == KpiAssignment.employee_id)
         .filter(KpiRealization.status == status))
    if current_user.role == SUPERVISOR:
        q = q.filter(Employee.supervisor_id == current_user.employee.id)
    rows = q.order_by(KpiRealization.submitted_at).limit(200).all()
    return render_template("verifications.html", rows=rows, status=status)


@bp.route("/verifications/<int:rid>", methods=["GET", "POST"])
@roles_required(SUPERVISOR, ADMIN)
def verification_review(rid):
    r = db.get_or_404(KpiRealization, rid)
    emp = r.assignment.employee
    if current_user.role == SUPERVISOR and (emp.supervisor_id != current_user.employee.id):
        abort(403)
    if request.method == "POST":
        decision = {"approve": "APPROVED", "reject": "REJECTED", "revision": "REVISION_REQUIRED"}.get(request.form.get("decision"))
        wf.review_realization(current_user, rid, decision, request.form.get("comment"))
        flash("Keputusan verifikasi tersimpan.", "success")
        return redirect(url_for("cycle.verifications"))
    return render_template("verification_review.html", r=r, a=r.assignment, emp=emp,
                           own=current_user.employee and emp.id == current_user.employee.id)


# ----------------------------------------------------------------- evaluasi & feedback
@bp.route("/evaluations")
@login_required
def evaluations():
    if current_user.role == EMPLOYEE:
        items = Feedback.query.filter_by(employee_id=current_user.employee.id).order_by(Feedback.id.desc()).all()
        return render_template("evaluations.html", items=items, members=[], period=None, periods=[], employee_view=True)
    periods = EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all()
    pid = request.args.get("period_id")
    period = db.session.get(EvaluationPeriod, int(pid)) if pid else (periods[0] if periods else None)
    q = Employee.query.filter(Employee.status.in_(["Active", "Probation"]))
    if current_user.role == SUPERVISOR:
        q = q.filter(Employee.id.in_(team_ids(current_user) or [-1]))
    members = []
    for e in q.order_by(Employee.name).limit(300).all():
        sc = PerformanceScore.query.filter_by(employee_id=e.id, period_id=period.id).first() if period else None
        fb = Feedback.query.filter_by(employee_id=e.id, period_id=period.id).count() if period else 0
        members.append((e, sc, fb))
    return render_template("evaluations.html", items=[], members=members, period=period, periods=periods, employee_view=False)


@bp.route("/evaluations/new", methods=["GET", "POST"])
@roles_required(SUPERVISOR, ADMIN)
def evaluation_new():
    emp = db.get_or_404(Employee, int(request.values.get("employee_id", 0)))
    period = db.get_or_404(EvaluationPeriod, int(request.values.get("period_id", 0)))
    if current_user.role == SUPERVISOR and emp.id not in team_ids(current_user):
        abort(403)
    fields = [dict(name="comment", label="Evaluasi / komentar", type="textarea", required=True),
              dict(name="development_plan", label="Rencana pengembangan", type="textarea")]
    if request.method == "POST":
        if not request.form.get("comment", "").strip():
            flash("Komentar wajib diisi.", "danger")
        else:
            fb = Feedback(employee_id=emp.id, period_id=period.id, evaluator_id=current_user.id,
                          comment=request.form["comment"].strip(), development_plan=(request.form.get("development_plan") or "").strip() or None)
            db.session.add(fb)
            notify(emp.user_id, "FEEDBACK", f"Feedback baru untuk {period.name}", None, f"/performance/{emp.id}")
            audit("CREATE_FEEDBACK", "feedback", None, new={"employee_id": emp.id, "period_id": period.id})
            db.session.commit()
            flash("Feedback tersimpan.", "success")
            return redirect(url_for("cycle.evaluations", period_id=period.id))
    return render_template("form.html", title=f"Evaluasi {emp.name} - {period.name}", fields=fields,
                           values={f["name"]: request.form.get(f["name"]) for f in fields},
                           back=url_for("cycle.evaluations", period_id=period.id),
                           hidden={"employee_id": emp.id, "period_id": period.id})
