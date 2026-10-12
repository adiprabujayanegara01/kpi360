from datetime import date, timedelta
from decimal import Decimal as D
import io
import pytest
from app.extensions import db
from app.models import (KpiAssignment, KpiRealization, PerformanceScore, AuditLog, Notification, EvaluationPeriod, EarlyWarning)
from app.services import workflow as wf
from app.services.common import BusinessError
from tests.conftest import login

PDF = b"%PDF-1.4 test"


def _assign(org, emp_key):
    return {a.template.kpi_code: a for a in KpiAssignment.query.filter_by(employee_id=org[emp_key][1].id, period_id=org["period"].id)}


def _run(org, emp_key, vals, evidence=True):
    u = org[emp_key][0]
    a = _assign(org, emp_key)
    for code, v in vals.items():
        r = wf.save_realization(u, a[code].id, v, "x")
        if code == "K3" and evidence:
            wf.attach_evidence(u, r, dict(original_name="b.pdf", stored_path="x.pdf", mime_type="application/pdf", size_bytes=3, checksum="c"))
        wf.submit_realization(u, r.id)


def test_bulk_assign_weights_and_publish_guard(org):
    admin, per = org["admin"], org["period"]
    assert KpiAssignment.query.filter_by(period_id=per.id).count() == 12  # 4 karyawan x 3 KPI
    a = KpiAssignment.query.filter_by(period_id=per.id).first()
    a.weight = 40; db.session.commit()  # total 90% -> BR-01
    with pytest.raises(BusinessError) as e:
        wf.publish_period(per, admin)
    assert e.value.code == "WEIGHT_INVALID" and e.value.status == 422
    a.weight = 50; db.session.commit()
    wf.publish_period(per, admin)
    assert per.status == "PUBLISHED"
    with pytest.raises(BusinessError):
        wf.publish_period(per, admin)  # state machine


def test_full_cycle_and_rules(org):
    admin, per = org["admin"], org["period"]
    spv, e1, e2 = org["spv"][0], org["e1"], org["e2"]
    wf.publish_period(per, admin)
    # BR-03: belum RUNNING
    with pytest.raises(BusinessError) as e:
        wf.save_realization(e1[0], _assign(org, "e1")["K1"].id, 80, "")
    assert e.value.code == "PERIOD_NOT_RUNNING"
    wf.start_period(per, admin)
    # validasi input
    with pytest.raises(BusinessError):
        wf.save_realization(e1[0], _assign(org, "e1")["K1"].id, -5, "")
    # IDOR: employee lain tidak boleh mengisi KPI orang lain
    with pytest.raises(BusinessError) as e:
        wf.save_realization(e2[0], _assign(org, "e1")["K1"].id, 50, "")
    assert e.value.status == 403
    # BR-10 bukti wajib
    r = wf.save_realization(e1[0], _assign(org, "e1")["K3"].id, "Ya", "")
    with pytest.raises(BusinessError) as e:
        wf.submit_realization(e1[0], r.id)
    assert e.value.code == "EVIDENCE_REQUIRED"
    _run(org, "e1", {"K1": 90, "K2": 4, "K3": "Ya"})
    rs = {r.assignment.template.kpi_code: r for r in KpiRealization.query.all()}
    # BR-06: tolak wajib komentar
    with pytest.raises(BusinessError) as e:
        wf.review_realization(spv, rs["K1"].id, "REJECTED", "")
    assert e.value.code == "COMMENT_REQUIRED"
    # BR-05: supervisor lain / diri sendiri
    with pytest.raises(BusinessError) as e:
        wf.review_realization(org["other"][0], rs["K1"].id, "APPROVED", "")
    assert e.value.status == 403
    # tolak -> revisi -> kirim ulang
    wf.review_realization(spv, rs["K1"].id, "REJECTED", "Data kurang")
    assert rs["K1"].status == "REJECTED"
    wf.save_realization(e1[0], _assign(org, "e1")["K1"].id, 100, "diperbaiki")
    wf.submit_realization(e1[0], rs["K1"].id)
    assert rs["K1"].version == 2
    for code in ("K1", "K2", "K3"):
        wf.review_realization(spv, rs[code].id, "APPROVED", "ok")
    s = PerformanceScore.query.filter_by(employee_id=e1[1].id, period_id=per.id).first()
    # K1=100%*50 + K2: 2/4=50%*30=15 + K3=100%*20 -> 85.00
    assert s.total_score == D("85.00") and s.category == "Baik" and s.status == "PROVISIONAL"
    # BR-04: e2 tidak punya realisasi disetujui -> skor 0 & incomplete
    wf.close_period(per, admin)
    with pytest.raises(BusinessError):
        wf.save_realization(e2[0], _assign(org, "e2")["K1"].id, 50, "")  # periode CLOSED
    res = wf.calculate_period(per, admin)
    assert res["incomplete"] >= 3
    s2 = PerformanceScore.query.filter_by(employee_id=e2[1].id, period_id=per.id).first()
    assert s2.total_score == 0 and s2.incomplete_count == 3 and s2.status == "CALCULATED"
    # BR-08: snapshot tersimpan di realisasi
    assert rs["K1"].target_snapshot == D(100) and rs["K1"].weight_snapshot == 50
    wf.finalize_period(per, admin)
    assert per.status == "FINALIZED" and PerformanceScore.query.filter_by(status="FINALIZED").count() == 4
    # BR-07: terkunci; koreksi lewat adjustment beralasan
    with pytest.raises(BusinessError):
        wf.recompute_employee(e1[1].id, per.id)
    with pytest.raises(BusinessError):
        wf.adjust_score(admin, s, 90, "")
    wf.adjust_score(admin, s, 90, "Koreksi data bukti")
    assert s.total_score == 90 and s.category == "Sangat Baik"
    acts = {a.action for a in AuditLog.query.all()}
    assert {"PUBLISH_PERIOD", "VERIFY_APPROVED", "VERIFY_REJECTED", "ADJUST_SCORE", "FINALIZE_PERIOD"} <= acts
    assert Notification.query.filter_by(user_id=e1[0].id, type="SCORE_READY").count() == 1


def test_supervisor_cannot_verify_own(org):
    admin, per = org["admin"], org["period"]
    wf.publish_period(per, admin); wf.start_period(per, admin)
    spv_u, spv_e = org["spv"]
    a = KpiAssignment.query.filter_by(employee_id=spv_e.id, period_id=per.id).first()
    r = wf.save_realization(spv_u, a.id, 5, "")
    r.status = "SUBMITTED"; db.session.commit()
    with pytest.raises(BusinessError) as e:
        wf.review_realization(spv_u, r.id, "APPROVED", "")
    assert "BR-05" in e.value.message


def test_assignment_change_after_publish_requires_reason_and_100(org):
    admin, per, e1 = org["admin"], org["period"], org["e1"][1]
    wf.publish_period(per, admin)
    a = _assign(org, "e1")
    rows = [{"id": a["K1"].id, "target": 120, "weight": 60}, {"id": a["K2"].id, "target": 2, "weight": 20}, {"id": a["K3"].id, "target": 1, "weight": 20}]
    with pytest.raises(BusinessError) as e:
        wf.save_employee_assignments(per, e1, rows, "", admin)
    assert e.value.code == "REASON_REQUIRED"
    rows[0]["weight"] = 70
    with pytest.raises(BusinessError) as e:
        wf.save_employee_assignments(per, e1, rows, "revisi target", admin)
    assert e.value.code == "WEIGHT_INVALID"
    rows[0]["weight"] = 60
    assert wf.save_employee_assignments(per, e1, rows, "Target naik", admin) == 100
    assert Notification.query.filter_by(user_id=org["e1"][0].id, type="ASSIGNMENT_CHANGED").count() == 1
    log = AuditLog.query.filter_by(action="UPDATE_ASSIGNMENTS").first()
    assert log.old_value and "Target naik" in str(log.new_value)


def test_audit_log_is_append_only(org):
    wf.publish_period(org["period"], org["admin"])
    log = AuditLog.query.first()
    log.action = "X"
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()
    db.session.delete(AuditLog.query.first())
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_org_snapshot_survives_supervisor_change(org):
    admin, per = org["admin"], org["period"]
    wf.publish_period(per, admin); wf.start_period(per, admin)
    e1 = org["e1"][1]
    wf.recompute_employee(e1.id, per.id)
    wf.close_period(per, admin); wf.calculate_period(per, admin)
    sid = org["spv"][1].id
    e1.supervisor_id = None; db.session.commit()  # BR-09
    s = PerformanceScore.query.filter_by(employee_id=e1.id, period_id=per.id).first()
    assert s.supervisor_id_snapshot == sid


# ---------------------------------------------------------------- HTTP / RBAC
def test_login_lockout_and_open_redirect(app, org, client):
    for _ in range(5):
        assert client.post("/login", data={"username": "emp1", "password": "salah"}).status_code == 401
    r = client.post("/login", data={"username": "emp1", "password": "Demo#2026!"})
    assert r.status_code == 401 and "terkunci" in r.get_data(as_text=True)
    c2 = app.test_client()
    r = c2.post("/login?next=//evil.com", data={"username": "spv", "password": "Demo#2026!"})
    assert r.status_code == 302 and "evil.com" not in r.headers["Location"]


def test_rbac_and_idor_over_http(app, org, client):
    admin, per = org["admin"], org["period"]
    wf.publish_period(per, admin); wf.start_period(per, admin)
    wf.recompute_employee(org["e1"][1].id, per.id); wf.recompute_employee(org["e2"][1].id, per.id); wf.recompute_employee(org["other"][1].id, per.id)
    db.session.commit()
    login(client, "emp1")
    assert client.get("/audit-logs").status_code == 403
    assert client.get("/employees").status_code == 403
    assert client.get("/periods").status_code == 403
    assert client.get(f"/performance/{org['e1'][1].id}").status_code == 200
    assert client.get(f"/performance/{org['e2'][1].id}").status_code == 403  # IDOR
    assert client.get(f"/api/v1/performance/{org['e2'][1].id}").status_code == 403
    r = client.get(f"/api/v1/performance/{org['e2'][1].id}")
    assert r.get_json()["error"]["code"] == "FORBIDDEN"
    assert client.post("/api/v1/ai/performance-analysis", json={"employee_id": org["e2"][1].id}).status_code == 403
    a_other = KpiAssignment.query.filter_by(employee_id=org["e2"][1].id).first()
    assert client.get(f"/kpi-realization/{a_other.id}").status_code == 403
    assert client.get("/api/v1/employees").status_code == 403
    c2 = client.application.test_client(); login(c2, "spv")
    assert c2.get(f"/performance/{org['e1'][1].id}").status_code == 200
    assert c2.get(f"/performance/{org['other'][1].id}").status_code == 403  # bukan anggota tim
    assert c2.get("/api/v1/performance/ranking").get_json()["count"] == 2
    c3 = client.application.test_client()
    assert c3.get("/dashboard").status_code == 302  # belum login
    assert c3.get("/api/v1/me").status_code in (302, 401)


def test_security_headers_and_upload_validation(app, org, client):
    admin, per = org["admin"], org["period"]
    wf.publish_period(per, admin); wf.start_period(per, admin)
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
    login(client, "emp1")
    a = _assign(org, "e1")["K3"]
    for name, data in (("evil.exe", b"MZ"), ("fake.pdf", b"not a pdf"), ("empty.pdf", b"")):
        r = client.post(f"/kpi-realization/{a.id}", data={"value": "1", "action": "save", "evidence": (io.BytesIO(data), name)},
                        content_type="multipart/form-data", follow_redirects=True)
        assert r.status_code == 200
    assert KpiRealization.query.first().evidence == []
    client.post(f"/kpi-realization/{a.id}", data={"value": "1", "action": "save", "evidence": (io.BytesIO(PDF), "ok.pdf")},
                content_type="multipart/form-data")
    ev = KpiRealization.query.first().evidence
    assert len(ev) == 1 and ev[0].stored_path != "ok.pdf" and ev[0].stored_path.endswith(".pdf")
    assert client.get(f"/evidence/{ev[0].id}").status_code == 200
    c2 = app.test_client(); login(c2, "emp2")
    assert c2.get(f"/evidence/{ev[0].id}").status_code == 403


def test_api_workflow_end_to_end(app, org, client):
    login(client, "admin", "Admin#2026!")
    per = org["period"]
    assert client.post("/api/v1/assignments/validate-weights", json={"period_id": per.id}).get_json()["valid"] is True
    assert client.post(f"/api/v1/periods/{per.id}/publish").status_code == 200
    assert client.post(f"/api/v1/periods/{per.id}/publish").status_code == 409
    client.post(f"/api/v1/periods/{per.id}/start")
    ce = app.test_client(); login(ce, "emp1")
    a = _assign(org, "e1")["K1"]
    r = ce.post("/api/v1/realizations", json={"assignment_id": a.id, "value": "abc"})
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "VALIDATION_ERROR"
    r = ce.post("/api/v1/realizations", json={"assignment_id": a.id, "value": "75,5"})
    assert r.status_code == 201
    rid = r.get_json()["id"]
    assert ce.post(f"/api/v1/realizations/{rid}/submit").status_code == 200
    cs = app.test_client(); login(cs, "spv")
    assert cs.post(f"/api/v1/realizations/{rid}/reject", json={}).status_code == 422
    assert cs.post(f"/api/v1/realizations/{rid}/approve", json={"comment": "ok"}).get_json()["status"] == "APPROVED"
    assert ce.post(f"/api/v1/realizations/{rid}/approve").status_code == 403
    assert ce.get("/api/v1/notifications").get_json()["total"] >= 1
