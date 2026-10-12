import os
import secrets
import warnings
from datetime import timedelta
import click
from flask import Flask, g, jsonify, render_template, request, session
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.engine import Engine
from .config import Config
from .extensions import db, migrate, csrf, login_manager
from .services.common import BusinessError

warnings.filterwarnings("ignore", message=".*does \\*not\\* support Decimal.*")


@event.listens_for(Engine, "connect")
def _sqlite_fk(dbapi_con, _):
    if dbapi_con.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def create_app(config=Config):
    app = Flask(__name__)
    app.config.from_object(config)
    if not app.config["TESTING"] and app.config["SECRET_KEY"] == "dev-only-change-me":
        app.logger.warning("SECRET_KEY default dipakai - ganti di .env untuk produksi!")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["REPORT_FOLDER"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)

    from .models import User
    from . import models  # noqa

    @login_manager.user_loader
    def load_user(uid):
        u = db.session.get(User, int(uid))
        return u if u and u.is_active else None

    from .blueprints import register_blueprints
    register_blueprints(app)
    from .web_helpers import register_helpers
    register_helpers(app)

    if app.config["TESTING"]:
        @app.before_request
        def _isolate_login_cache():  # fixture menahan app context; hindari kebocoran sesi antar test client
            g.pop("_login_user", None)

    @app.before_request
    def _nonce():
        g.csp_nonce = secrets.token_urlsafe(16)
        session.permanent = True

    @app.context_processor
    def _ctx():
        unread = 0
        if current_user.is_authenticated:
            from .models import Notification
            unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        return {"csp_nonce": g.get("csp_nonce", ""), "unread_count": unread}

    @app.after_request
    def _headers(resp):
        n = g.get("csp_nonce", "")
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self' 'nonce-{n}' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if app.config["HSTS"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith("/api/") or current_user.is_authenticated:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def _err(code, message, status, fields=None):
        if request.path.startswith("/api/"):
            return jsonify(error={"code": code, "message": message, "fields": fields or {}}), status
        return render_template("error.html", status=status, message=message), status

    @app.errorhandler(BusinessError)
    def _biz(e):
        db.session.rollback()
        if not request.path.startswith("/api/"):
            from flask import flash, redirect
            flash(e.message, "danger" if e.status >= 400 else "warning")
            return redirect(request.referrer or "/dashboard")
        return _err(e.code, e.message, e.status, e.fields)

    for status, code, msg in ((400, "BAD_REQUEST", "Permintaan tidak valid."), (401, "UNAUTHORIZED", "Silakan login."),
                              (403, "FORBIDDEN", "Anda tidak memiliki akses ke sumber daya ini."),
                              (404, "NOT_FOUND", "Data atau halaman tidak ditemukan."),
                              (413, "TOO_LARGE", "Ukuran file melebihi batas 5 MB."),
                              (429, "RATE_LIMITED", "Terlalu banyak permintaan.")):
        app.register_error_handler(status, lambda e, s=status, c=code, m=msg: _err(c, m, s))

    @app.errorhandler(500)
    def _500(e):
        db.session.rollback()
        return _err("SERVER_ERROR", "Terjadi kesalahan pada server.", 500)

    @app.get("/healthz")
    def health():
        try:
            db.session.execute(db.text("select 1"))
            return jsonify(status="ok")
        except Exception:
            return jsonify(status="error"), 503

    register_cli(app)
    return app


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Buat tabel (development cepat). Produksi: gunakan `flask db upgrade`."""
        db.create_all()
        click.echo("Tabel dibuat.")

    @app.cli.command("send-reminders")
    def send_reminders():
        """Pengingat deadline input (jalankan harian via cron)."""
        from .services.jobs import deadline_reminders
        click.echo(f"{deadline_reminders()} pengingat dikirim.")

    @app.cli.command("run-early-warning")
    def run_ew():
        """Evaluasi early warning untuk semua periode ber-skor terbaru."""
        from .services.common import latest_scored_period
        from .ai.early_warning import run_for_period
        p = latest_scored_period()
        click.echo(f"{run_for_period(p) if p else 0} peringatan baru.")
