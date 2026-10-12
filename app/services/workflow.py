"""Logika bisnis siklus KPI: periode, assignment, realisasi, verifikasi, perhitungan skor.
Aturan bisnis BR-01..BR-10 ditegakkan di sini (bukan di view)."""
from decimal import Decimal as D
from sqlalchemy import func
from ..extensions import db
from ..models import (EvaluationPeriod, KpiAssignment, KpiRealization, KpiVerification, PerformanceScore,
                      Employee, PositionKpiTemplate, KpiTemplate, EvidenceFile, User, utcnow,
                      ADMIN, SUPERVISOR)
from ..kpi_engine import (KpiError, achievement, compute_score, categorize, parse_realization,
                          validate_target, to_decimal, HIGHER, EXACT)
from ..security.audit import audit
from .common import BusinessError, notify, notify_admins, category_tuples, today

EDITABLE = ("DRAFT", "REVISION_REQUIRED", "REJECTED")
COUNTED = ("APPROVED", "CALCULATED")


def _kpi_err(e):
    return BusinessError("VALIDATION_ERROR", str(e), 422)


# --------------------------------------------------------------------------- bobot / assignment
def weight_problems(period_id):
    rows = (db.session.query(KpiAssignment.employee_id, func.sum(KpiAssignment.weight))
            .filter_by(period_id=period_id).group_by(KpiAssignment.employee_id).all())
    out = []
    for emp_id, total in rows:
        if D(total) != D(100):
            e = db.session.get(Employee, emp_id)
            out.append({"employee_id": emp_id, "employee": e.name, "total_weight": float(total)})
    return out


def _new_assignment(period, emp, t, target, weight, actor):
    a = KpiAssignment(employee_id=emp.id, period_id=period.id, kpi_template_id=t.id, target=target,
                      weight=weight, assigned_by=actor.id if actor else None)
    a.snapshot_from(t)
    db.session.add(a)
    return a


def bulk_assign(period, actor, position_id=None, department_id=None):
    """Isi otomatis dari template jabatan (BR-01 divalidasi saat publish)."""
    if period.status != "DRAFT":
        raise BusinessError("PERIOD_LOCKED", "Penugasan massal hanya pada periode DRAFT.")
    q = Employee.query.filter(Employee.status.in_(["Active", "Probation"]))
    if position_id:
        q = q.filter_by(position_id=int(position_id))
    if department_id:
        q = q.filter_by(department_id=int(department_id))
    created = skipped = 0
    for emp in q.all():
        links = PositionKpiTemplate.query.filter_by(position_id=emp.position_id).all()
        for l in links:
            t = l.template
            if t.status != "ACTIVE":
                continue
            if KpiAssignment.query.filter_by(employee_id=emp.id, period_id=period.id, kpi_template_id=t.id).first():
                skipped += 1
                continue
            target = l.default_target if l.default_target is not None else (t.default_target if t.default_target is not None else 1)
            _new_assignment(period, emp, t, target, l.default_weight, actor)
            created += 1
    audit("BULK_ASSIGN", "evaluation_periods", period.id, new={"created": created, "skipped": skipped}, user=actor)
    db.session.commit()
    return {"created": created, "skipped": skipped}


def copy_assignments(period, source, actor):
    if period.status != "DRAFT":
        raise BusinessError("PERIOD_LOCKED", "Salin penugasan hanya pada periode DRAFT.")
    created = 0
    for a in KpiAssignment.query.filter_by(period_id=source.id).all():
        if a.employee.status not in ("Active", "Probation"):
            continue
        if KpiAssignment.query.filter_by(employee_id=a.employee_id, period_id=period.id,
                                         kpi_template_id=a.kpi_template_id).first():
            continue
        _new_assignment(period, a.employee, a.template, a.target, a.weight, actor)
        created += 1
    audit("COPY_ASSIGNMENTS", "evaluation_periods", period.id, new={"from": source.id, "created": created}, user=actor)
    db.session.commit()
    return {"created": created}


def add_assignment(period, employee, template, target, weight, actor):
    if period.status != "DRAFT":
        raise BusinessError("PERIOD_LOCKED", "Menambah KPI hanya pada periode DRAFT.")
    try:
        target = validate_target(template.measurement_type, target)
    except KpiError as e:
        raise _kpi_err(e)
    if KpiAssignment.query.filter_by(employee_id=employee.id, period_id=period.id, kpi_template_id=template.id).first():
        raise BusinessError("DUPLICATE", "KPI tersebut sudah ditugaskan.")
    a = _new_assignment(period, employee, template, target, to_decimal(weight), actor)
    audit("ADD_ASSIGNMENT", "kpi_assignments", None, new={"employee": employee.id, "kpi": template.kpi_code}, user=actor)
    db.session.commit()
    return a


def remove_assignment(period, assignment, actor):
    if period.status != "DRAFT":
        raise BusinessError("PERIOD_LOCKED", "Menghapus KPI hanya pada periode DRAFT.")
    audit("REMOVE_ASSIGNMENT", "kpi_assignments", assignment.id, old={"kpi": assignment.kpi_name_snapshot}, user=actor)
    db.session.delete(assignment)
    db.session.commit()


def save_employee_assignments(period, employee, rows, reason, actor):
    """rows: [{id, target, weight}]. DRAFT bebas; PUBLISHED/RUNNING wajib alasan & total 100 (BR-02)."""
    if period.status in ("CLOSED", "FINALIZED"):
        raise BusinessError("PERIOD_LOCKED", "Periode sudah ditutup; gunakan prosedur adjustment.")
    if period.status != "DRAFT" and not (reason or "").strip():
        raise BusinessError("REASON_REQUIRED", "Perubahan setelah PUBLISHED wajib disertai alasan tertulis (BR-02).", 422)
    old, new, objs = [], [], []
    for r in rows:
        a = db.session.get(KpiAssignment, int(r["id"]))
        if not a or a.employee_id != employee.id or a.period_id != period.id:
            raise BusinessError("NOT_FOUND", "Assignment tidak ditemukan.", 404)
        try:
            tgt = validate_target(a.measurement_type_snapshot, r["target"])
            w = to_decimal(r["weight"], "Bobot")
            if not (0 <= w <= 100):
                raise KpiError("Bobot harus 0-100.")
        except KpiError as e:
            raise _kpi_err(e)
        old.append({"kpi": a.kpi_name_snapshot, "target": a.target, "weight": a.weight})
        new.append({"kpi": a.kpi_name_snapshot, "target": tgt, "weight": w})
        objs.append((a, tgt, w))
    total = sum((w for _, _, w in objs), D(0))
    all_total = (db.session.query(func.sum(KpiAssignment.weight))
                 .filter_by(employee_id=employee.id, period_id=period.id).scalar() or 0)
    edited_old = sum((a.weight for a, _, _ in objs), D(0))
    final_total = D(all_total) - edited_old + total
    if period.status != "DRAFT" and final_total != D(100):
        raise BusinessError("WEIGHT_INVALID", f"Total bobot harus 100%, hasil perubahan: {final_total}%.", 422)
    for a, tgt, w in objs:
        a.target, a.weight = tgt, w
    audit("UPDATE_ASSIGNMENTS", "kpi_assignments", f"{employee.id}/{period.id}", old=old,
          new={"items": new, "reason": reason}, user=actor)
    if period.status != "DRAFT":
        notify(employee.user_id, "ASSIGNMENT_CHANGED", "Target/bobot KPI Anda diperbarui",
               f"Periode {period.name}. Alasan: {reason}", "/my-kpi")
    db.session.commit()
    return final_total


# --------------------------------------------------------------------------- state machine periode
def publish_period(period, actor):
    if period.status != "DRAFT":
        raise BusinessError("INVALID_STATE", f"Periode berstatus {period.status}, hanya DRAFT yang dapat dipublish.")
    if KpiAssignment.query.filter_by(period_id=period.id).count() == 0:
        raise BusinessError("NO_ASSIGNMENT", "Belum ada KPI yang ditugaskan pada periode ini.", 422)
    problems = weight_problems(period.id)
    if problems:
        raise BusinessError("WEIGHT_INVALID", f"Total bobot belum 100% untuk {len(problems)} karyawan (BR-01).", 422,
                            {"employees": problems})
    for a in KpiAssignment.query.filter_by(period_id=period.id).all():  # refresh snapshot dari template
        a.snapshot_from(a.template)
    period.status = "PUBLISHED"
    ids = {a.employee.user_id for a in KpiAssignment.query.filter_by(period_id=period.id).all()}
    for uid in ids:
        notify(uid, "KPI_ASSIGNED", f"KPI baru untuk {period.name}", "Target dan bobot KPI Anda sudah ditetapkan.", "/my-kpi")
    audit("PUBLISH_PERIOD", "evaluation_periods", period.id, old={"status": "DRAFT"}, new={"status": "PUBLISHED"}, user=actor)
    db.session.commit()


def start_period(period, actor):
    if period.status != "PUBLISHED":
        raise BusinessError("INVALID_STATE", "Hanya periode PUBLISHED yang dapat dimulai (RUNNING).")
    period.status = "RUNNING"
    audit("START_PERIOD", "evaluation_periods", period.id, old={"status": "PUBLISHED"}, new={"status": "RUNNING"}, user=actor)
    db.session.commit()


def close_period(period, actor):
    if period.status != "RUNNING":
        raise BusinessError("INVALID_STATE", "Hanya periode RUNNING yang dapat ditutup.")
    period.status = "CLOSED"
    sup_ids = {a.employee.supervisor.user_id for a in KpiAssignment.query.filter_by(period_id=period.id).all()
               if a.employee.supervisor}
    for uid in sup_ids:
        notify(uid, "PERIOD_CLOSED", f"Periode {period.name} ditutup", "Selesaikan verifikasi yang tersisa.", "/verifications")
    audit("CLOSE_PERIOD", "evaluation_periods", period.id, old={"status": "RUNNING"}, new={"status": "CLOSED"}, user=actor)
    db.session.commit()


def incomplete_report(period):
    """Daftar KPI incomplete (belum ada realisasi yang disetujui) - BR-04."""
    out = []
    for a in KpiAssignment.query.filter_by(period_id=period.id).all():
        r = a.realization
        if not r or r.status not in COUNTED:
            out.append({"employee": a.employee.name, "kpi": a.kpi_name_snapshot, "status": r.status if r else "BELUM DIINPUT"})
    return out


# --------------------------------------------------------------------------- realisasi
def _own_assignment(user, assignment_id):
    a = db.session.get(KpiAssignment, int(assignment_id))
    if not a:
        raise BusinessError("NOT_FOUND", "KPI tidak ditemukan.", 404)
    if not user.employee or a.employee_id != user.employee.id:
        raise BusinessError("FORBIDDEN", "Anda hanya dapat mengisi KPI milik sendiri.", 403)
    return a


def save_realization(user, assignment_id, value, description):
    a = _own_assignment(user, assignment_id)
    p = a.period
    if p.status != "RUNNING":
        raise BusinessError("PERIOD_NOT_RUNNING", "Realisasi hanya dapat diinput saat periode RUNNING (BR-03).")
    if today() > p.input_deadline:
        raise BusinessError("DEADLINE_PASSED", "Batas waktu input realisasi sudah lewat (BR-03).")
    existing = a.realization
    if existing is not None and existing.status not in EDITABLE:
        raise BusinessError("INVALID_STATE", f"Realisasi berstatus {existing.status} dan tidak dapat diubah.")
    try:
        parsed = parse_realization(value, a.measurement_type_snapshot, a.allow_negative_snapshot)
    except KpiError as e:
        raise _kpi_err(e)
    r = existing or KpiRealization(assignment=a, status="DRAFT")
    r.realization_value = parsed
    r.description = (description or "").strip()
    db.session.add(r)
    db.session.commit()
    return r


def attach_evidence(user, realization, meta):
    a = realization.assignment
    _own_assignment(user, a.id)
    if realization.status not in EDITABLE:
        raise BusinessError("INVALID_STATE", "Bukti hanya dapat ditambah saat Draft/Revisi.")
    db.session.add(EvidenceFile(realization_id=realization.id, **meta))
    audit("UPLOAD_EVIDENCE", "kpi_realizations", realization.id, new={"file": meta["original_name"]}, user=user)
    db.session.commit()


def submit_realization(user, realization_id):
    r = db.session.get(KpiRealization, int(realization_id))
    if not r:
        raise BusinessError("NOT_FOUND", "Realisasi tidak ditemukan.", 404)
    a = _own_assignment(user, r.assignment_id)
    p = a.period
    if p.status != "RUNNING" or today() > p.input_deadline:
        raise BusinessError("DEADLINE_PASSED", "Periode tidak RUNNING atau deadline lewat (BR-03).")
    if r.status not in EDITABLE:
        raise BusinessError("INVALID_STATE", f"Realisasi berstatus {r.status}.")
    if r.realization_value is None:
        raise BusinessError("VALIDATION_ERROR", "Isi nilai realisasi terlebih dahulu.", 422)
    if a.evidence_required_snapshot and not r.evidence:
        raise BusinessError("EVIDENCE_REQUIRED", "KPI ini mewajibkan bukti pencapaian (BR-10).", 422)
    r.status, r.submitted_at, r.version = "SUBMITTED", utcnow(), r.version + 1
    sup = user.employee.supervisor
    if sup:
        notify(sup.user_id, "VERIFY_PENDING", f"Realisasi menunggu verifikasi: {user.employee.name}",
               a.kpi_name_snapshot, "/verifications")
    audit("SUBMIT_REALIZATION", "kpi_realizations", r.id, new={"value": r.realization_value, "version": r.version}, user=user)
    db.session.commit()
    return r


def review_realization(user, realization_id, decision, comment):
    """decision: APPROVED | REJECTED | REVISION_REQUIRED."""
    r = db.session.get(KpiRealization, int(realization_id))
    if not r:
        raise BusinessError("NOT_FOUND", "Realisasi tidak ditemukan.", 404)
    a = r.assignment
    emp = a.employee
    comment = (comment or "").strip()
    override = False
    if user.role == SUPERVISOR:
        me = user.employee
        if me and emp.id == me.id:
            raise BusinessError("FORBIDDEN", "Supervisor tidak boleh memverifikasi realisasi miliknya sendiri (BR-05).", 403)
        if not me or emp.supervisor_id != me.id:
            raise BusinessError("FORBIDDEN", "Anda hanya dapat memverifikasi anggota tim Anda (BR-05).", 403)
    elif user.role == ADMIN:
        override = True
        if not comment:
            raise BusinessError("COMMENT_REQUIRED", "Override HR wajib disertai alasan.", 422)
    else:
        raise BusinessError("FORBIDDEN", "Peran Anda tidak dapat memverifikasi.", 403)
    if decision not in ("APPROVED", "REJECTED", "REVISION_REQUIRED"):
        raise BusinessError("VALIDATION_ERROR", "Keputusan tidak valid.", 422)
    if decision != "APPROVED" and not comment:
        raise BusinessError("COMMENT_REQUIRED", "Penolakan/permintaan revisi wajib disertai komentar (BR-06).", 422)
    if a.period.status == "FINALIZED":
        raise BusinessError("PERIOD_LOCKED", "Periode sudah FINALIZED.")
    if r.status != "SUBMITTED":
        raise BusinessError("INVALID_STATE", f"Hanya realisasi SUBMITTED yang dapat diverifikasi (saat ini {r.status}).")
    db.session.add(KpiVerification(realization_id=r.id, verifier_id=user.id, decision=decision,
                                   comment=comment, is_override=override))
    r.status = decision
    db.session.flush()
    if decision == "APPROVED":
        recompute_employee(emp.id, a.period_id)
    label = {"APPROVED": "disetujui", "REJECTED": "ditolak", "REVISION_REQUIRED": "perlu revisi"}[decision]
    notify(emp.user_id, "VERIFIED", f"Realisasi {a.kpi_name_snapshot} {label}", comment or None, "/my-kpi")
    audit("VERIFY_" + decision, "kpi_realizations", r.id, old={"status": "SUBMITTED"},
          new={"status": decision, "comment": comment, "override": override}, user=user)
    db.session.commit()
    return r


# --------------------------------------------------------------------------- perhitungan
def _snapshot_org(score, emp, period):
    if period.status in ("DRAFT", "PUBLISHED", "RUNNING") or score.department_id_snapshot is None:
        score.department_id_snapshot = emp.department_id
        score.position_id_snapshot = emp.position_id
        score.supervisor_id_snapshot = emp.supervisor_id


def recompute_employee(employee_id, period_id, final_status=None):
    """Hitung ulang skor satu karyawan; hanya realisasi APPROVED/CALCULATED yang dihitung (BR-04)."""
    period = db.session.get(EvaluationPeriod, period_id)
    if period.status == "FINALIZED":
        raise BusinessError("PERIOD_LOCKED", "Skor FINALIZED terkunci (BR-07).")
    emp = db.session.get(Employee, employee_id)
    assigns = KpiAssignment.query.filter_by(employee_id=employee_id, period_id=period_id).all()
    if not assigns:
        return None
    items, incomplete = [], 0
    for a in assigns:
        r = a.realization
        if r and r.status in COUNTED and r.realization_value is not None:
            try:
                ach = achievement(a.measurement_type_snapshot, a.target, r.realization_value,
                                  a.max_achievement_snapshot, a.lower_penalty_snapshot, a.allow_negative_snapshot)
            except KpiError as e:
                raise _kpi_err(e)
            r.achievement = ach
            r.weighted_score = ach * a.weight / 100
            r.target_snapshot, r.weight_snapshot = a.target, a.weight
        else:
            ach = D(0)
            incomplete += 1
            if r:
                r.achievement = r.weighted_score = None
        items.append((ach, a.weight))
    try:
        total = compute_score(items)
    except KpiError as e:
        raise _kpi_err(e)
    score = PerformanceScore.query.filter_by(employee_id=employee_id, period_id=period_id).first()
    if not score:
        score = PerformanceScore(employee_id=employee_id, period_id=period_id, total_score=total)
        db.session.add(score)
    score.total_score = total
    score.category = categorize(total, category_tuples())
    score.incomplete_count = incomplete
    score.status = final_status or ("CALCULATED" if score.status == "CALCULATED" else "PROVISIONAL")
    if final_status == "CALCULATED":
        score.calculated_at = utcnow()
    _snapshot_org(score, emp, period)
    return score


def calculate_period(period, actor):
    if period.status != "CLOSED":
        raise BusinessError("INVALID_STATE", "Perhitungan skor dilakukan setelah periode CLOSED.")
    emp_ids = [x[0] for x in db.session.query(KpiAssignment.employee_id).filter_by(period_id=period.id).distinct()]
    n = 0
    for eid in emp_ids:
        recompute_employee(eid, period.id, final_status="CALCULATED")
        n += 1
    for r in (KpiRealization.query.join(KpiAssignment).filter(KpiAssignment.period_id == period.id,
                                                                KpiRealization.status == "APPROVED").all()):
        r.status = "CALCULATED"
    inc = incomplete_report(period)
    audit("CALCULATE_PERIOD", "evaluation_periods", period.id, new={"employees": n, "incomplete": len(inc)}, user=actor)
    db.session.commit()
    return {"employees": n, "incomplete": len(inc), "incomplete_items": inc[:50]}


def finalize_period(period, actor, force=False):
    if period.status != "CLOSED":
        raise BusinessError("INVALID_STATE", "Hanya periode CLOSED yang dapat difinalisasi.")
    pending = (KpiRealization.query.join(KpiAssignment).filter(KpiAssignment.period_id == period.id,
                                                                 KpiRealization.status == "SUBMITTED").count())
    if pending and not force:
        raise BusinessError("PENDING_VERIFICATION", f"Masih ada {pending} realisasi menunggu verifikasi.", 409)
    scores = PerformanceScore.query.filter_by(period_id=period.id).all()
    if not scores or any(s.status != "CALCULATED" for s in scores):
        raise BusinessError("NOT_CALCULATED", "Jalankan perhitungan skor terlebih dahulu.", 409)
    now = utcnow()
    for s in scores:
        s.status, s.finalized_at = "FINALIZED", now
        notify(s.employee.user_id, "SCORE_READY", f"Skor {period.name} tersedia", f"Skor Anda: {s.total_score} ({s.category})",
               f"/performance/{s.employee_id}")
    period.status = "FINALIZED"
    audit("FINALIZE_PERIOD", "evaluation_periods", period.id, old={"status": "CLOSED"}, new={"status": "FINALIZED"}, user=actor)
    notify_admins("PERIOD_DONE", f"Periode {period.name} difinalisasi", f"{len(scores)} skor terkunci.", "/performance")
    db.session.commit()
    try:  # early warning dihitung setelah skor terkunci
        from ..ai.early_warning import run_for_period
        run_for_period(period)
    except Exception:  # pragma: no cover - jangan gagalkan finalisasi
        db.session.rollback()
    return len(scores)


def adjust_score(actor, score, new_total, reason):
    """Koreksi skor FINALIZED melalui adjustment beralasan (BR-07)."""
    if actor.role != ADMIN:
        raise BusinessError("FORBIDDEN", "Hanya HR yang dapat membuat adjustment.", 403)
    if not (reason or "").strip():
        raise BusinessError("REASON_REQUIRED", "Alasan adjustment wajib diisi.", 422)
    try:
        new_total = to_decimal(new_total, "Skor")
    except KpiError as e:
        raise _kpi_err(e)
    if not (0 <= new_total <= 100):
        raise BusinessError("VALIDATION_ERROR", "Skor harus 0-100.", 422)
    old = {"total_score": score.total_score, "category": score.category}
    score.total_score = new_total
    score.category = categorize(new_total, category_tuples())
    audit("ADJUST_SCORE", "performance_scores", score.id, old=old,
          new={"total_score": new_total, "category": score.category, "reason": reason}, user=actor)
    db.session.commit()
    return score
