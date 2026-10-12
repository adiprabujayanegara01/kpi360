"""Data master: CRUD generik (departemen, jabatan, kategori KPI, template KPI) + karyawan + template per jabatan."""
from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import (Department, Position, KpiCategory, KpiTemplate, PositionKpiTemplate, Employee, User, ScoreCategory,
                      EMP_STATUSES, ADMIN, SUPERVISOR, EMPLOYEE, ROLES)
from ..kpi_engine import KPI_TYPES, validate_target, KpiError, to_decimal
from ..security.rbac import roles_required, team_ids
from ..security.audit import audit
from ..security.passwords import password_problem
from ..services.common import BusinessError

bp = Blueprint("master", __name__)

TYPE_OPTS = [("HIGHER", "Higher is Better"), ("LOWER", "Lower is Better"), ("EXACT", "Exact Target"), ("BOOLEAN", "Boolean (Ya/Tidak)")]
STATUS_OPTS = [("ACTIVE", "Aktif"), ("INACTIVE", "Nonaktif")]


def _cats():
    return [(c.id, c.name) for c in KpiCategory.query.order_by(KpiCategory.name)]


def _employees():
    return [("", "- tidak ada -")] + [(e.id, f"{e.name} ({e.employee_number})") for e in Employee.query.order_by(Employee.name)]


def _depts():
    return [("", "- pilih -")] + [(d.id, d.name) for d in Department.query.filter_by(status="ACTIVE").order_by(Department.name)]


def _positions():
    return [("", "- pilih -")] + [(p.id, p.name) for p in Position.query.filter_by(status="ACTIVE").order_by(Position.name)]


def _kpi_validate(obj):
    try:
        if obj.default_target is not None:
            validate_target(obj.measurement_type, obj.default_target)
    except KpiError as e:
        return str(e)
    if obj.default_weight is not None and not (0 <= obj.default_weight <= 100):
        return "Bobot default harus 0-100."
    if obj.max_achievement is None or obj.max_achievement < 100 or obj.max_achievement > 200:
        return "Batas maksimum achievement harus 100-200."


ENTITIES = {
    "departments": dict(model=Department, title="Departemen", columns=[("code", "Kode"), ("name", "Nama"), ("status", "Status")],
                        fields=[dict(name="code", label="Kode", type="text", required=True), dict(name="name", label="Nama", type="text", required=True),
                                dict(name="head_employee_id", label="Kepala departemen", type="select", options=_employees, nullable=True),
                                dict(name="status", label="Status", type="select", options=STATUS_OPTS)]),
    "positions": dict(model=Position, title="Jabatan", columns=[("code", "Kode"), ("name", "Nama"), ("level", "Level"), ("status", "Status")],
                      fields=[dict(name="code", label="Kode", type="text", required=True), dict(name="name", label="Nama", type="text", required=True),
                              dict(name="level", label="Level", type="text"), dict(name="status", label="Status", type="select", options=STATUS_OPTS)],
                      extra_actions=[("Template KPI", "master.position_kpis")]),
    "kpi-categories": dict(model=KpiCategory, title="Kategori KPI", columns=[("name", "Nama"), ("description", "Deskripsi")],
                           fields=[dict(name="name", label="Nama", type="text", required=True), dict(name="description", label="Deskripsi", type="text")]),
    "score-categories": dict(model=ScoreCategory, title="Kategori performa", columns=[("name", "Nama"), ("min_score", "Min"), ("max_score", "Maks"), ("color", "Warna")],
                             fields=[dict(name="name", label="Nama", type="text", required=True), dict(name="min_score", label="Skor minimum", type="number", required=True),
                                     dict(name="max_score", label="Skor maksimum", type="number", required=True), dict(name="color", label="Warna (hex)", type="text")],
                             validate=lambda o: "Skor minimum tidak boleh melebihi maksimum." if o.min_score > o.max_score else None),
    "kpis": dict(model=KpiTemplate, title="Template KPI",
                 columns=[("kpi_code", "Kode"), ("name", "Nama KPI"), ("category.name", "Kategori"), ("measurement_type", "Tipe"), ("unit", "Satuan"),
                          ("default_target", "Target default"), ("max_achievement", "Cap"), ("status", "Status")],
                 fields=[dict(name="kpi_code", label="Kode KPI", type="text", required=True), dict(name="name", label="Nama KPI", type="text", required=True),
                         dict(name="description", label="Deskripsi", type="textarea"),
                         dict(name="category_id", label="Kategori", type="select", options=_cats, required=True),
                         dict(name="measurement_type", label="Tipe pengukuran", type="select", options=TYPE_OPTS, required=True),
                         dict(name="unit", label="Satuan", type="text", help="mis. tiket, jam, %, skor"),
                         dict(name="default_target", label="Target default", type="number", nullable=True),
                         dict(name="default_weight", label="Bobot default (%)", type="number", nullable=True),
                         dict(name="max_achievement", label="Cap achievement (%)", type="number", default=100, help="100 = tidak ada bonus; mis. 120 bila kelebihan target dihargai."),
                         dict(name="lower_penalty", label="Penalti Lower (target 0)", type="number", default=100, help="Dipakai bila target = 0 dan realisasi > 0."),
                         dict(name="allow_negative", label="Boleh realisasi negatif (KPI finansial)", type="checkbox"),
                         dict(name="evidence_required", label="Bukti wajib", type="checkbox"),
                         dict(name="status", label="Status", type="select", options=STATUS_OPTS)],
                 validate=_kpi_validate),
}


def _get(obj, path):
    for part in path.split("."):
        obj = getattr(obj, part, None) if obj is not None else None
    return obj


def _parse(field, raw):
    t = field["type"]
    if t == "checkbox":
        return raw is not None
    raw = (raw or "").strip()
    if raw == "":
        if field.get("required"):
            raise ValueError(f"{field['label']} wajib diisi.")
        return None if field.get("nullable") or t in ("number", "date", "select") else ""
    if t == "number":
        try:
            return to_decimal(raw)
        except KpiError:
            raise ValueError(f"{field['label']} harus berupa angka.")
    if t == "select":
        return int(raw) if raw.lstrip("-").isdigit() else raw
    if t == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"{field['label']} tidak valid.")
    return raw


def _fields_resolved(cfg):
    out = []
    for f in cfg["fields"]:
        f = dict(f)
        if callable(f.get("options")):
            f["options"] = f["options"]()
        out.append(f)
    return out


def make_crud(key, cfg):
    Model = cfg["model"]

    def listing():
        rows = Model.query.order_by(Model.id).all()
        table = [{"id": r.id, "cells": [_get(r, c) for c, _ in cfg["columns"]]} for r in rows]
        return render_template("list.html", title=cfg["title"], key=key, columns=[l for _, l in cfg["columns"]], rows=table,
                               extra=cfg.get("extra_actions", []), can_create=True, can_delete=True)

    def save(obj=None):
        creating = obj is None
        obj = obj or Model()
        fields = _fields_resolved(cfg)
        if request.method == "POST":
            old = {f["name"]: getattr(obj, f["name"], None) for f in fields} if not creating else None
            try:
                for f in fields:
                    setattr(obj, f["name"], _parse(f, request.form.get(f["name"])))
                if cfg.get("validate") and (msg := cfg["validate"](obj)):
                    raise ValueError(msg)
                db.session.add(obj)
                db.session.flush()
                audit(("CREATE_" if creating else "UPDATE_") + Model.__tablename__.upper(), Model.__tablename__, obj.id,
                      old=old, new={f["name"]: getattr(obj, f["name"]) for f in fields})
                db.session.commit()
                flash("Data disimpan.", "success")
                return redirect(url_for(f"master.{key}_list"))
            except ValueError as e:
                db.session.rollback()
                flash(str(e), "danger")
            except IntegrityError:
                db.session.rollback()
                flash("Data duplikat atau tidak valid (kode/nama sudah dipakai).", "danger")
        vals = {f["name"]: (request.form.get(f["name"]) if request.method == "POST" else
                            (getattr(obj, f["name"], None) if not creating else f.get("default"))) for f in fields}
        return render_template("form.html", title=("Tambah " if creating else "Ubah ") + cfg["title"], fields=fields, values=vals,
                               back=url_for(f"master.{key}_list"))

    def create():
        return save()

    def edit(id):
        return save(db.get_or_404(Model, id))

    def delete(id):
        obj = db.get_or_404(Model, id)
        try:
            if hasattr(obj, "status"):
                old = obj.status
                obj.status = "INACTIVE"
                audit("DEACTIVATE_" + Model.__tablename__.upper(), Model.__tablename__, id, old={"status": old}, new={"status": "INACTIVE"})
                flash("Data dinonaktifkan (soft delete, histori tetap utuh).", "success")
            else:
                audit("DELETE_" + Model.__tablename__.upper(), Model.__tablename__, id, old={"name": getattr(obj, "name", None)})
                db.session.delete(obj)
                flash("Data dihapus.", "success")
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Data masih dipakai sehingga tidak dapat dihapus.", "danger")
        return redirect(url_for(f"master.{key}_list"))

    for name, fn, rule, methods in (("list", listing, f"/{key}", ["GET"]), ("create", create, f"/{key}/create", ["GET", "POST"]),
                                    ("edit", edit, f"/{key}/<int:id>", ["GET", "POST"]), ("delete", delete, f"/{key}/<int:id>/delete", ["POST"])):
        bp.add_url_rule(rule, f"{key}_{name}", roles_required(ADMIN)(fn), methods=methods)


for _k, _c in ENTITIES.items():
    make_crud(_k, _c)


# ------------------------------------------------------------------ template KPI per jabatan
@bp.route("/positions/<int:id>/kpis", methods=["GET", "POST"])
@roles_required(ADMIN)
def position_kpis(id):
    pos = db.get_or_404(Position, id)
    if request.method == "POST":
        act = request.form.get("action")
        try:
            if act == "add":
                t = db.get_or_404(KpiTemplate, int(request.form["kpi_template_id"]))
                w = to_decimal(request.form["default_weight"])
                tgt = request.form.get("default_target")
                tgt = validate_target(t.measurement_type, tgt) if tgt else None
                if not (0 <= w <= 100):
                    raise KpiError("Bobot harus 0-100.")
                db.session.add(PositionKpiTemplate(position_id=pos.id, kpi_template_id=t.id, default_weight=w, default_target=tgt))
                audit("ADD_POSITION_KPI", "positions", pos.id, new={"kpi": t.kpi_code, "weight": w})
            elif act == "remove":
                l = db.get_or_404(PositionKpiTemplate, int(request.form["link_id"]))
                audit("REMOVE_POSITION_KPI", "positions", pos.id, old={"kpi": l.template.kpi_code})
                db.session.delete(l)
            db.session.commit()
        except (KpiError, KeyError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("KPI tersebut sudah ada pada jabatan ini.", "danger")
        return redirect(url_for("master.position_kpis", id=id))
    links = PositionKpiTemplate.query.filter_by(position_id=id).all()
    total = sum((l.default_weight for l in links), Decimal(0))
    used = {l.kpi_template_id for l in links}
    avail = [t for t in KpiTemplate.query.filter_by(status="ACTIVE").order_by(KpiTemplate.name) if t.id not in used]
    return render_template("position_kpis.html", pos=pos, links=links, total=total, avail=avail)


# ------------------------------------------------------------------ karyawan
def _emp_form_fields(creating):
    f = [dict(name="employee_number", label="No. karyawan", type="text", required=True), dict(name="name", label="Nama lengkap", type="text", required=True),
         dict(name="email", label="Email", type="text", required=True), dict(name="phone", label="Telepon", type="text"),
         dict(name="department_id", label="Departemen", type="select", options=_depts(), required=True),
         dict(name="position_id", label="Jabatan", type="select", options=_positions(), required=True),
         dict(name="supervisor_id", label="Atasan langsung", type="select", options=_employees(), nullable=True),
         dict(name="join_date", label="Tanggal bergabung", type="date"),
         dict(name="status", label="Status", type="select", options=[(s, s) for s in EMP_STATUSES]),
         dict(name="role", label="Peran akun", type="select", options=[(r, r.title()) for r in ROLES]),
         dict(name="username", label="Username", type="text", required=True)]
    f.append(dict(name="password", label="Password" + ("" if creating else " baru (kosongkan bila tidak diubah)"), type="password",
                  required=creating, help="Min. 8 karakter, huruf besar, huruf kecil, dan angka."))
    return f


@bp.route("/employees")
@roles_required(ADMIN, SUPERVISOR)
def employees():
    q = Employee.query
    if current_user.role == SUPERVISOR:
        q = q.filter(Employee.id.in_(team_ids(current_user) or [-1]))
    for arg, col in (("department_id", Employee.department_id), ("position_id", Employee.position_id)):
        if request.args.get(arg):
            q = q.filter(col == int(request.args[arg]))
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        q = q.filter(db.or_(Employee.name.ilike(like), Employee.employee_number.ilike(like)))
    page = max(1, int(request.args.get("page", 1)))
    pg = q.order_by(Employee.name).paginate(page=page, per_page=25, error_out=False)
    return render_template("employees.html", pg=pg, depts=Department.query.all(), poss=Position.query.all(), args=request.args)


@bp.route("/employees/create", methods=["GET", "POST"])
@bp.route("/employees/<int:id>/edit", methods=["GET", "POST"], endpoint="employee_edit")
@roles_required(ADMIN)
def employee_form(id=None):
    emp = db.get_or_404(Employee, id) if id else None
    fields = _emp_form_fields(emp is None)
    if request.method == "POST":
        f = request.form
        try:
            pw = f.get("password", "")
            if (emp is None or pw) and (msg := password_problem(pw)):
                raise ValueError(msg)
            sup = int(f["supervisor_id"]) if f.get("supervisor_id") else None
            if emp and sup == emp.id:
                raise ValueError("Atasan tidak boleh dirinya sendiri.")
            creating = emp is None
            emp = emp or Employee()
            old = None if creating else {"department_id": emp.department_id, "supervisor_id": emp.supervisor_id, "status": emp.status}
            for fl in fields:
                if fl["name"] in ("role", "username", "password"):
                    continue
                setattr(emp, fl["name"], _parse(fl, f.get(fl["name"])))
            user = emp.user or User()
            user.username, user.email, user.role = f["username"].strip(), f["email"].strip(), f["role"]
            user.status = "ACTIVE" if emp.status in ("Active", "Probation") else "INACTIVE"
            if pw:
                user.set_password(pw)
            db.session.add(user)
            db.session.flush()
            emp.user_id = user.id
            db.session.add(emp)
            db.session.flush()
            audit("CREATE_EMPLOYEE" if creating else "UPDATE_EMPLOYEE", "employees", emp.id, old=old,
                  new={"department_id": emp.department_id, "supervisor_id": emp.supervisor_id, "status": emp.status, "role": user.role})
            db.session.commit()
            flash("Karyawan disimpan.", "success")
            return redirect(url_for("master.employees"))
        except (ValueError, KeyError) as e:
            db.session.rollback()
            flash(str(e), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("No. karyawan, username, atau email sudah dipakai.", "danger")
    vals = {fl["name"]: (request.form.get(fl["name"]) if request.method == "POST" else
                         (getattr(emp, fl["name"], None) if emp else None)) for fl in fields}
    if emp and request.method == "GET":
        vals.update(role=emp.user.role if emp.user else EMPLOYEE, username=emp.user.username if emp.user else "", password="")
    elif request.method == "GET":
        vals.update(role=EMPLOYEE, status="Active")
    return render_template("form.html", title="Ubah Karyawan" if emp else "Tambah Karyawan", fields=fields, values=vals,
                           back=url_for("master.employees"))


@bp.route("/employees/<int:id>")
@roles_required(ADMIN, SUPERVISOR)
def employee_detail(id):
    from .perf import performance_detail
    return performance_detail(id)
