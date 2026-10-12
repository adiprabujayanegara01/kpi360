from functools import wraps
from flask import abort
from flask_login import current_user, login_required
from ..extensions import db
from ..models import Employee, PerformanceScore, ADMIN, SUPERVISOR, EMPLOYEE


def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def my_employee():
    return current_user.employee if current_user.is_authenticated else None


def team_ids(user):
    if not user.employee:
        return []
    return [e.id for e in Employee.query.filter_by(supervisor_id=user.employee.id).all()]


def scope_employee_ids(user):
    """None = semua (admin); selain itu daftar id karyawan yang boleh dilihat."""
    if user.role == ADMIN:
        return None
    if user.role == SUPERVISOR:
        ids = team_ids(user)
        return ids
    return [user.employee.id] if user.employee else []


def can_access_employee(user, employee_id, include_self=True):
    """Pencegahan IDOR: admin semua, supervisor timnya (+ dirinya), employee dirinya."""
    if user.role == ADMIN:
        return True
    if include_self and user.employee and user.employee.id == employee_id:
        return True
    if user.role == SUPERVISOR:
        return employee_id in team_ids(user)
    return False


def require_employee_access(employee_id, include_self=True):
    if not can_access_employee(current_user, employee_id, include_self):
        abort(403)


def visible_scores(user):
    """Query PerformanceScore sesuai cakupan peran. Supervisor memakai snapshot (BR-09)."""
    q = PerformanceScore.query
    if user.role == ADMIN:
        return q
    if not user.employee:
        return q.filter(db.false())
    if user.role == SUPERVISOR:
        return q.filter(db.or_(PerformanceScore.supervisor_id_snapshot == user.employee.id,
                               PerformanceScore.employee_id == user.employee.id))
    return q.filter(PerformanceScore.employee_id == user.employee.id)


def team_scores(user):
    """Skor anggota tim (tanpa diri sendiri) untuk supervisor; semua untuk admin."""
    q = visible_scores(user)
    if user.role == SUPERVISOR:
        q = q.filter(PerformanceScore.supervisor_id_snapshot == user.employee.id)
    return q
