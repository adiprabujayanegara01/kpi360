from datetime import timedelta
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, abort
from flask_login import login_user, logout_user, current_user, login_required
from ..extensions import db
from ..models import User, utcnow, EMPLOYEE
from ..security.audit import audit
from ..security.passwords import password_problem
from ..security.ratelimit import rate_limit
from ..services.common import notify_admins

bp = Blueprint("auth", __name__)


def _safe_next(target):
    if target and not urlparse(target).netloc and target.startswith("/") and not target.startswith("//"):
        return target
    return None


def login():
    if current_user.is_authenticated and request.method == "GET":
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        u = User.query.filter(db.func.lower(User.username) == username.lower()).first() if username else None
        cfg = current_app.config
        err = None
        if u and u.locked_until and u.locked_until > utcnow():
            err = "Akun terkunci sementara karena terlalu banyak percobaan gagal. Coba lagi nanti."
            audit("LOGIN_BLOCKED", "users", u.id, username=username)
        elif not u or not u.is_active or not u.check_password(password):
            err = "Username atau password salah."
            if u:
                u.failed_logins += 1
                if u.failed_logins >= cfg["MAX_FAILED_LOGINS"]:
                    u.locked_until = utcnow() + timedelta(minutes=cfg["LOCKOUT_MINUTES"])
                    u.failed_logins = 0
            audit("LOGIN_FAILED", "users", u.id if u else None, username=username)
        if err:
            db.session.commit()
            if request.is_json:
                return jsonify(error={"code": "AUTH_FAILED", "message": err}), 401
            flash(err, "danger")
            return render_template("login.html"), 401
        u.failed_logins, u.locked_until, u.last_login_at = 0, None, utcnow()
        login_user(u)
        audit("LOGIN", "users", u.id, user=u)
        db.session.commit()
        if request.is_json:
            return jsonify(user={"id": u.id, "username": u.username, "role": u.role})
        return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
    return render_template("login.html")


bp.add_url_rule("/login", "login", rate_limit("login", 20, 60)(login), methods=["GET", "POST"])
bp.add_url_rule("/auth/login", "login_alias", rate_limit("login", 20, 60)(login), methods=["GET", "POST"])


@bp.post("/auth/logout")
@bp.post("/logout")
@login_required
def logout():
    audit("LOGOUT", "users", current_user.id)
    db.session.commit()
    logout_user()
    if request.is_json:
        return jsonify(ok=True)
    return redirect(url_for("auth.login"))


@bp.route("/auth/forgot-password", methods=["GET", "POST"])
@rate_limit("forgot", 5, 300)
def forgot_password():
    if request.method == "POST":
        ident = (request.form.get("identifier") or "").strip()
        u = User.query.filter(db.or_(db.func.lower(User.username) == ident.lower(),
                                     db.func.lower(User.email) == ident.lower())).first()
        audit("FORGOT_PASSWORD", "users", u.id if u else None, username=ident[:64])
        if u:
            notify_admins("PASSWORD_RESET", f"Permintaan reset password: {u.username}", "Reset melalui menu Karyawan > Edit.", "/employees")
        db.session.commit()
        flash("Jika akun ditemukan, permintaan reset telah diteruskan ke HR. Anda akan dihubungi.", "info")
        return redirect(url_for("auth.login"))
    return render_template("forgot.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if not current_app.config["REGISTRATION_ENABLED"]:
        abort(404)
    if request.method == "POST":
        f = request.form
        problem = password_problem(f.get("password"))
        if problem:
            flash(problem, "danger")
        elif User.query.filter(db.or_(User.username == f.get("username"), User.email == f.get("email"))).first():
            flash("Username atau email sudah terdaftar.", "danger")
        else:
            u = User(username=f["username"].strip(), email=f["email"].strip(), role=EMPLOYEE, status="INACTIVE")
            u.set_password(f["password"])
            db.session.add(u)
            audit("REGISTER", "users", None, new={"username": u.username}, username=u.username)
            notify_admins("REGISTER", f"Pendaftaran baru: {u.username}", "Aktifkan akun dan hubungkan ke data karyawan.", "/employees")
            db.session.commit()
            flash("Pendaftaran diterima. Akun akan aktif setelah disetujui HR.", "success")
            return redirect(url_for("auth.login"))
    return render_template("register.html")
