"""Rate limiter in-memory sederhana (per proses). Untuk multi-instance, ganti dengan Redis."""
import time
from collections import defaultdict, deque
from functools import wraps
from flask import request, jsonify, flash, redirect, url_for
from flask_login import current_user

_hits = defaultdict(deque)


def allow(bucket, key, limit, window):
    now = time.time()
    dq = _hits[(bucket, key)]
    while dq and dq[0] < now - window:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


def reset():
    _hits.clear()


def rate_limit(bucket, limit, window=60):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            key = current_user.id if current_user.is_authenticated else request.remote_addr
            if not allow(bucket, key, limit, window):
                if request.path.startswith("/api/"):
                    return jsonify(error={"code": "RATE_LIMITED", "message": "Terlalu banyak permintaan. Coba lagi sebentar."}), 429
                flash("Terlalu banyak permintaan. Coba lagi sebentar.", "warning")
                return redirect(request.referrer or url_for("main.dashboard"))
            return fn(*a, **kw)
        return wrapper
    return deco
