import io
from datetime import date, timedelta
import pytest
from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import User, Employee, EvaluationPeriod, KpiAssignment
from app.seed import reset_db, seed_base, DEMO_PASSWORD, ADMIN_PASSWORD
from app.security import ratelimit


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_base()
        yield app
        db.session.remove()
        reset_db()
    ratelimit.reset()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password=DEMO_PASSWORD):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture()
def org(app):
    """Organisasi kecil: HR admin, 1 supervisor, 2 karyawan, 1 periode RUNNING dengan 3 KPI (bobot 100)."""
    from app.models import Department, Position, KpiCategory, KpiTemplate, PositionKpiTemplate
    from app.services import workflow as wf
    d = Department(code="IT", name="IT"); p = Position(code="DEV", name="Dev")
    db.session.add_all([d, p]); db.session.flush()
    cat = KpiCategory.query.first()
    t1 = KpiTemplate(kpi_code="K1", name="Tiket", category_id=cat.id, measurement_type="HIGHER", unit="tiket", default_target=100)
    t2 = KpiTemplate(kpi_code="K2", name="Respon", category_id=cat.id, measurement_type="LOWER", unit="jam", default_target=2)
    t3 = KpiTemplate(kpi_code="K3", name="Proyek", category_id=cat.id, measurement_type="BOOLEAN", default_target=1, evidence_required=True)
    db.session.add_all([t1, t2, t3]); db.session.flush()
    for t, w in ((t1, 50), (t2, 30), (t3, 20)):
        db.session.add(PositionKpiTemplate(position_id=p.id, kpi_template_id=t.id, default_weight=w))

    def mk(username, role, name, sup=None):
        u = User(username=username, email=f"{username}@x.id", role=role); u.set_password(DEMO_PASSWORD)
        db.session.add(u); db.session.flush()
        e = Employee(employee_number=username.upper(), user_id=u.id, name=name, email=u.email, department_id=d.id,
                     position_id=p.id, supervisor_id=sup.id if sup else None, status="Active")
        db.session.add(e); db.session.flush()
        return u, e
    su, se = mk("spv", "SUPERVISOR", "Sari Supervisor")
    u1, e1 = mk("emp1", "EMPLOYEE", "Eka Satu", se)
    u2, e2 = mk("emp2", "EMPLOYEE", "Dani Dua", se)
    u3, e3 = mk("other", "EMPLOYEE", "Orang Lain")  # tanpa supervisor
    today = date.today()
    per = EvaluationPeriod(name="P1", start_date=today - timedelta(days=10), end_date=today + timedelta(days=20),
                           input_deadline=today + timedelta(days=5), status="DRAFT")
    db.session.add(per); db.session.commit()
    admin = User.query.filter_by(username="admin").first()
    wf.bulk_assign(per, admin)
    return dict(admin=admin, spv=(su, se), e1=(u1, e1), e2=(u2, e2), other=(u3, e3), period=per, dept=d, pos=p)
