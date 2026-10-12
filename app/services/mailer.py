import smtplib
import threading
from email.message import EmailMessage
from flask import current_app


def send_email(to, subject, body):
    """Email opsional: aktif bila MAIL_SERVER diisi. Dikirim di thread agar tidak memblokir request."""
    cfg = current_app.config
    if not cfg.get("MAIL_SERVER") or not to or cfg.get("TESTING"):
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_SENDER"], to, subject
    msg.set_content(body)

    def _go():
        try:
            with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as s:
                s.starttls()
                if cfg["MAIL_USERNAME"]:
                    s.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
                s.send_message(msg)
        except Exception:  # pragma: no cover
            current_app.logger.exception("Gagal mengirim email")
    threading.Thread(target=_go, daemon=True).start()
    return True
