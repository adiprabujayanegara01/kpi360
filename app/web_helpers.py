from decimal import Decimal
from flask import render_template, request, flash
from markupsafe import Markup, escape
from .models import ScoreCategory

STATUS_STYLE = {
    "DRAFT": "secondary", "SUBMITTED": "warning", "APPROVED": "success", "CALCULATED": "success", "REJECTED": "danger",
    "REVISION_REQUIRED": "warning", "PUBLISHED": "info", "RUNNING": "primary", "CLOSED": "dark", "FINALIZED": "success",
    "PROVISIONAL": "secondary", "ACTIVE": "success", "INACTIVE": "secondary", "Active": "success", "Inactive": "secondary",
    "Probation": "warning", "Resigned": "dark", "LOW": "success", "MEDIUM": "warning", "HIGH": "danger",
    "BELUM DIINPUT": "secondary", "PENDING": "secondary", "DONE": "success", "FAILED": "danger",
}
STATUS_LABEL = {"REVISION_REQUIRED": "Perlu revisi", "SUBMITTED": "Menunggu verifikasi", "BELUM DIINPUT": "Belum diinput",
                "APPROVED": "Disetujui", "REJECTED": "Ditolak", "DRAFT": "Draft", "CALCULATED": "Terhitung",
                "PROVISIONAL": "Sementara", "FINALIZED": "Final"}


def register_helpers(app):
    @app.template_filter("n2")
    def n2(v, digits=2):
        if v is None or v == "":
            return "-"
        try:
            return f"{float(v):,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return v

    @app.template_filter("num")
    def num(v):
        """Angka ringkas: 100.0000 -> 100, 4.5000 -> 4,5"""
        if v is None:
            return "-"
        s = f"{float(v):.4f}".rstrip("0").rstrip(".")
        return s.replace(".", ",")

    @app.template_filter("dt")
    def dt(v, fmt="%d %b %Y"):
        return v.strftime(fmt) if v else "-"

    @app.template_filter("badge")
    def badge(status):
        style = STATUS_STYLE.get(status, "secondary")
        return Markup(f'<span class="badge text-bg-{style}">{escape(STATUS_LABEL.get(status, status))}</span>')

    @app.context_processor
    def helpers():
        colors = {c.name: c.color for c in ScoreCategory.query.all()}

        def cat_badge(name):
            if not name:
                return Markup("-")
            return Markup(f'<span class="cat" style="--c:{escape(colors.get(name, "#5F6B7A"))}">{escape(name)}</span>')
        return {"cat_badge": cat_badge, "cat_colors": colors}
