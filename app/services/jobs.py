from datetime import timedelta
from ..models import EvaluationPeriod, KpiAssignment, KpiRealization
from ..extensions import db
from .common import notify, today
from .mailer import send_email


def deadline_reminders(days_before=3):
    """Ingatkan karyawan yang masih punya KPI belum disubmit saat deadline <= N hari."""
    n = 0
    for p in EvaluationPeriod.query.filter_by(status="RUNNING").all():
        if not (0 <= (p.input_deadline - today()).days <= days_before):
            continue
        pend = {}
        for a in KpiAssignment.query.filter_by(period_id=p.id).all():
            if not a.realization or a.realization.status in ("DRAFT", "REJECTED", "REVISION_REQUIRED"):
                pend.setdefault(a.employee, 0)
                pend[a.employee] += 1
        for emp, c in pend.items():
            body = f"{c} KPI belum disubmit. Batas input: {p.input_deadline:%d %b %Y}."
            notify(emp.user_id, "DEADLINE", f"Pengingat deadline {p.name}", body, "/my-kpi")
            send_email(emp.email, f"[KPI360] Pengingat deadline {p.name}", body)
            n += 1
    db.session.commit()
    return n
