"""Model database KPI360 (SQLAlchemy). Tabel mengikuti dokumen perancangan bab 9."""
from datetime import datetime, timezone
from flask_login import UserMixin
from sqlalchemy import event
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

ADMIN, SUPERVISOR, EMPLOYEE = "ADMIN", "SUPERVISOR", "EMPLOYEE"
ROLES = (ADMIN, SUPERVISOR, EMPLOYEE)
EMP_STATUSES = ("Active", "Inactive", "Probation", "Resigned")
PERIOD_STATUSES = ("DRAFT", "PUBLISHED", "RUNNING", "CLOSED", "FINALIZED")
REAL_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "REVISION_REQUIRED", "CALCULATED")

PK = db.BigInteger().with_variant(db.Integer, "sqlite")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(db.Model):
    __abstract__ = True
    id = db.Column(PK, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class User(UserMixin, Base):
    __tablename__ = "users"
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=EMPLOYEE)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    last_login_at = db.Column(db.DateTime)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    employee = db.relationship("Employee", back_populates="user", uselist=False)
    __table_args__ = (db.CheckConstraint("role in ('ADMIN','SUPERVISOR','EMPLOYEE')", name="ck_user_role"),)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_active(self):
        return self.status == "ACTIVE"

    @property
    def is_admin(self):
        return self.role == ADMIN

    @property
    def display_name(self):
        return self.employee.name if self.employee else self.username


class Department(Base):
    __tablename__ = "departments"
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    head_employee_id = db.Column(db.ForeignKey("employees.id", use_alter=True, name="fk_dept_head"))
    status = db.Column(db.String(20), default="ACTIVE", nullable=False)


class Position(Base):
    __tablename__ = "positions"
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    level = db.Column(db.String(40))
    status = db.Column(db.String(20), default="ACTIVE", nullable=False)


class Employee(Base):
    __tablename__ = "employees"
    employee_number = db.Column(db.String(30), unique=True, nullable=False)
    user_id = db.Column(db.ForeignKey("users.id"), unique=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160))
    phone = db.Column(db.String(40))
    department_id = db.Column(db.ForeignKey("departments.id"))
    position_id = db.Column(db.ForeignKey("positions.id"))
    supervisor_id = db.Column(db.ForeignKey("employees.id"))
    join_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="Active", nullable=False)
    user = db.relationship("User", back_populates="employee")
    department = db.relationship("Department", foreign_keys=[department_id])
    position = db.relationship("Position")
    supervisor = db.relationship("Employee", remote_side="Employee.id", backref="team_members")
    __table_args__ = (db.Index("ix_emp_sup_dept", "supervisor_id", "department_id"),)


class KpiCategory(Base):
    __tablename__ = "kpi_categories"
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))


class KpiTemplate(Base):
    __tablename__ = "kpi_templates"
    kpi_code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    category_id = db.Column(db.ForeignKey("kpi_categories.id"))
    measurement_type = db.Column(db.String(10), nullable=False, default="HIGHER")
    unit = db.Column(db.String(30))
    default_target = db.Column(db.Numeric(18, 4))
    default_weight = db.Column(db.Numeric(6, 2))
    max_achievement = db.Column(db.Numeric(6, 2), default=100, nullable=False)
    lower_penalty = db.Column(db.Numeric(8, 2), default=100, nullable=False)
    allow_negative = db.Column(db.Boolean, default=False, nullable=False)
    evidence_required = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(20), default="ACTIVE", nullable=False)
    category = db.relationship("KpiCategory")
    __table_args__ = (db.CheckConstraint("measurement_type in ('HIGHER','LOWER','EXACT','BOOLEAN')", name="ck_kpi_type"),
                      db.CheckConstraint("default_weight is null or (default_weight >= 0 and default_weight <= 100)", name="ck_kpi_weight"))


class PositionKpiTemplate(Base):
    __tablename__ = "position_kpi_templates"
    position_id = db.Column(db.ForeignKey("positions.id"), nullable=False)
    kpi_template_id = db.Column(db.ForeignKey("kpi_templates.id"), nullable=False)
    default_weight = db.Column(db.Numeric(6, 2), nullable=False)
    default_target = db.Column(db.Numeric(18, 4))
    position = db.relationship("Position", backref="kpi_links")
    template = db.relationship("KpiTemplate")
    __table_args__ = (db.UniqueConstraint("position_id", "kpi_template_id", name="uq_pos_kpi"),)


class EvaluationPeriod(Base):
    __tablename__ = "evaluation_periods"
    name = db.Column(db.String(80), nullable=False)
    period_type = db.Column(db.String(20), nullable=False, default="MONTHLY")
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    input_deadline = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    __table_args__ = (db.CheckConstraint("end_date >= start_date", name="ck_period_dates"),)


class KpiAssignment(Base):
    __tablename__ = "kpi_assignments"
    employee_id = db.Column(db.ForeignKey("employees.id"), nullable=False)
    period_id = db.Column(db.ForeignKey("evaluation_periods.id"), nullable=False)
    kpi_template_id = db.Column(db.ForeignKey("kpi_templates.id"), nullable=False)
    target = db.Column(db.Numeric(18, 4), nullable=False)
    weight = db.Column(db.Numeric(6, 2), nullable=False)
    # snapshot (BR-08)
    measurement_type_snapshot = db.Column(db.String(10), nullable=False)
    max_achievement_snapshot = db.Column(db.Numeric(6, 2), nullable=False, default=100)
    lower_penalty_snapshot = db.Column(db.Numeric(8, 2), nullable=False, default=100)
    allow_negative_snapshot = db.Column(db.Boolean, nullable=False, default=False)
    evidence_required_snapshot = db.Column(db.Boolean, nullable=False, default=False)
    kpi_name_snapshot = db.Column(db.String(160))
    unit_snapshot = db.Column(db.String(30))
    category_snapshot = db.Column(db.String(80))
    assigned_by = db.Column(db.ForeignKey("users.id"))
    employee = db.relationship("Employee")
    period = db.relationship("EvaluationPeriod")
    template = db.relationship("KpiTemplate")
    realization = db.relationship("KpiRealization", back_populates="assignment", uselist=False,
                                  cascade="all, delete-orphan")
    __table_args__ = (db.UniqueConstraint("employee_id", "period_id", "kpi_template_id", name="uq_assignment"),
                      db.CheckConstraint("weight >= 0 and weight <= 100", name="ck_assign_weight"),
                      db.Index("ix_assign_period_emp", "period_id", "employee_id"))

    def snapshot_from(self, t):
        self.measurement_type_snapshot = t.measurement_type
        self.max_achievement_snapshot = t.max_achievement
        self.lower_penalty_snapshot = t.lower_penalty
        self.allow_negative_snapshot = t.allow_negative
        self.evidence_required_snapshot = t.evidence_required
        self.kpi_name_snapshot = t.name
        self.unit_snapshot = t.unit
        self.category_snapshot = t.category.name if t.category else None


class KpiRealization(Base):
    __tablename__ = "kpi_realizations"
    assignment_id = db.Column(db.ForeignKey("kpi_assignments.id"), unique=True, nullable=False)
    realization_value = db.Column(db.Numeric(18, 4))
    description = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    submitted_at = db.Column(db.DateTime)
    achievement = db.Column(db.Numeric(12, 6))
    weighted_score = db.Column(db.Numeric(12, 6))
    target_snapshot = db.Column(db.Numeric(18, 4))
    weight_snapshot = db.Column(db.Numeric(6, 2))
    version = db.Column(db.Integer, default=0, nullable=False)
    assignment = db.relationship("KpiAssignment", back_populates="realization")
    evidence = db.relationship("EvidenceFile", backref="realization", cascade="all, delete-orphan")
    verifications = db.relationship("KpiVerification", backref="realization", order_by="KpiVerification.id")
    __table_args__ = (db.Index("ix_real_status", "status"),)


class EvidenceFile(Base):
    __tablename__ = "evidence_files"
    realization_id = db.Column(db.ForeignKey("kpi_realizations.id"), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(100))
    size_bytes = db.Column(db.Integer)
    checksum = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime, default=utcnow)


class KpiVerification(Base):
    __tablename__ = "kpi_verifications"
    realization_id = db.Column(db.ForeignKey("kpi_realizations.id"), nullable=False)
    verifier_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    decision = db.Column(db.String(20), nullable=False)
    comment = db.Column(db.Text)
    decided_at = db.Column(db.DateTime, default=utcnow)
    is_override = db.Column(db.Boolean, default=False, nullable=False)
    verifier = db.relationship("User")


class PerformanceScore(Base):
    __tablename__ = "performance_scores"
    employee_id = db.Column(db.ForeignKey("employees.id"), nullable=False)
    period_id = db.Column(db.ForeignKey("evaluation_periods.id"), nullable=False)
    total_score = db.Column(db.Numeric(7, 2), nullable=False)
    category = db.Column(db.String(40))
    status = db.Column(db.String(12), default="PROVISIONAL", nullable=False)  # PROVISIONAL/CALCULATED/FINALIZED
    incomplete_count = db.Column(db.Integer, default=0, nullable=False)
    calculated_at = db.Column(db.DateTime)
    finalized_at = db.Column(db.DateTime)
    # snapshot organisasi (BR-09)
    department_id_snapshot = db.Column(db.ForeignKey("departments.id"))
    position_id_snapshot = db.Column(db.ForeignKey("positions.id"))
    supervisor_id_snapshot = db.Column(db.ForeignKey("employees.id"))
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    period = db.relationship("EvaluationPeriod")
    department = db.relationship("Department", foreign_keys=[department_id_snapshot])
    position = db.relationship("Position", foreign_keys=[position_id_snapshot])
    __table_args__ = (db.UniqueConstraint("employee_id", "period_id", name="uq_score"),
                      db.Index("ix_score_period_total", "period_id", "total_score"))


class Feedback(Base):
    __tablename__ = "feedback"
    employee_id = db.Column(db.ForeignKey("employees.id"), nullable=False)
    period_id = db.Column(db.ForeignKey("evaluation_periods.id"), nullable=False)
    evaluator_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    development_plan = db.Column(db.Text)
    employee = db.relationship("Employee")
    period = db.relationship("EvaluationPeriod")
    evaluator = db.relationship("User")


class ScoreCategory(Base):
    __tablename__ = "score_categories"
    name = db.Column(db.String(40), nullable=False)
    min_score = db.Column(db.Numeric(6, 2), nullable=False)
    max_score = db.Column(db.Numeric(6, 2), nullable=False)
    color = db.Column(db.String(12), default="#5F6B7A")
    __table_args__ = (db.CheckConstraint("min_score <= max_score", name="ck_cat_range"),)


class AiInsight(Base):
    __tablename__ = "ai_insights"
    employee_id = db.Column(db.ForeignKey("employees.id"), nullable=False)
    period_id = db.Column(db.ForeignKey("evaluation_periods.id"), nullable=False)
    type = db.Column(db.String(30), default="PERFORMANCE", nullable=False)
    summary = db.Column(db.Text)
    trend = db.Column(db.String(20))
    strengths = db.Column(db.JSON)
    weaknesses = db.Column(db.JSON)
    recommendations = db.Column(db.JSON)
    evidence = db.Column(db.JSON)
    risk_level = db.Column(db.String(10))
    source = db.Column(db.String(10), default="rules")  # llm | rules
    model_name = db.Column(db.String(80))
    prompt_version = db.Column(db.String(40))
    generated_by = db.Column(db.ForeignKey("users.id"))
    quality_rating = db.Column(db.Integer)  # +1 / -1 dari peninjau manusia
    employee = db.relationship("Employee")
    period = db.relationship("EvaluationPeriod")


class EarlyWarning(Base):
    __tablename__ = "early_warnings"
    employee_id = db.Column(db.ForeignKey("employees.id"), nullable=False)
    period_id = db.Column(db.ForeignKey("evaluation_periods.id"), nullable=False)
    rule = db.Column(db.String(10), nullable=False)
    level = db.Column(db.String(10), nullable=False)
    message = db.Column(db.String(400))
    employee = db.relationship("Employee")
    period = db.relationship("EvaluationPeriod")
    __table_args__ = (db.UniqueConstraint("employee_id", "period_id", "rule", name="uq_warning"),)


class Notification(Base):
    __tablename__ = "notifications"
    user_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    type = db.Column(db.String(30))
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    link = db.Column(db.String(300))
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    __table_args__ = (db.Index("ix_notif_user_read", "user_id", "is_read"),)


class AuditLog(db.Model):
    """Append-only. Update/delete diblokir di level ORM."""
    __tablename__ = "audit_logs"
    id = db.Column(PK, primary_key=True)
    user_id = db.Column(db.ForeignKey("users.id"))
    username = db.Column(db.String(64))
    action = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(60))
    entity_id = db.Column(db.String(60))
    old_value = db.Column(db.JSON)
    new_value = db.Column(db.JSON)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.Index("ix_audit_created_user", "created_at", "user_id"),)


@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
def _audit_readonly(mapper, connection, target):
    raise RuntimeError("audit_logs bersifat append-only.")


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


class ReportJob(Base):
    __tablename__ = "report_jobs"
    user_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    report_type = db.Column(db.String(30), nullable=False)
    fmt = db.Column(db.String(10), nullable=False)
    params = db.Column(db.JSON)
    status = db.Column(db.String(12), default="PENDING", nullable=False)
    file_path = db.Column(db.String(500))
    error = db.Column(db.String(400))
