from datetime import date, datetime
from decimal import Decimal
from flask import has_request_context, request
from flask_login import current_user
from ..extensions import db
from ..models import AuditLog


def jsonable(v):
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def audit(action, entity_type=None, entity_id=None, old=None, new=None, user=None, username=None):
    """Catat aksi penting. Tidak melakukan commit (ikut transaksi pemanggil)."""
    uid, uname, ip, ua = None, username, None, None
    if has_request_context():
        ip = request.remote_addr
        ua = (request.user_agent.string or "")[:255]
        u = user or (current_user if current_user.is_authenticated else None)
    else:
        u = user
    if u is not None:
        uid, uname = u.id, u.username
    db.session.add(AuditLog(user_id=uid, username=uname or "SYSTEM", action=action, entity_type=entity_type,
                            entity_id=str(entity_id) if entity_id is not None else None,
                            old_value=jsonable(old), new_value=jsonable(new), ip_address=ip, user_agent=ua))
