import json
import pytest
from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import (Employee, EvaluationPeriod, PerformanceScore, AiInsight, EarlyWarning, User, ReportJob, Notification)
from app.seed import seed_demo, reset_db
from app.security import ratelimit
from app.ai import insights as ins, chat, llm_client, prediction
from app.ai.early_warning import evaluate_series, max_level
from app.services.common import all_settings
from tests.conftest import login

CFG = {"ew_drop_points": "10", "ew_low_score": "60", "ew_kpi_low_pct": "50", "ew_reject_count": "2"}


# ---------------------------------------------------------------- early warning (fungsi murni)
def rules(scores, **kw):
    return {a["rule"]: a["level"] for a in evaluate_series(scores, cfg=CFG, **kw)}


def test_ew_examples_from_doc():
    r = rules([91, 83, 74])  # contoh dokumen: turun 2 periode
    assert r == {"EW-05": "MEDIUM"}
    assert rules([90, 85, 80, 75]) == {"EW-01": "HIGH"}
    assert rules([90, 78]) == {"EW-02": "HIGH"}
    assert rules([65, 59]) == {"EW-03": "HIGH"}
    assert rules([70, 59]) == {"EW-02": "HIGH", "EW-03": "HIGH"}
    assert rules([80, 80], kpi_hist={"X": [40, 45]}) == {"EW-04": "MEDIUM"}
    assert rules([80, 80], kpi_hist={"X": [40, 55]}) == {}
    assert rules([80, 81], rejects=2) == {"EW-06": "LOW"}
    assert rules([80, 81], late=True) == {"EW-06": "LOW"}
    assert rules([85, 86, 87]) == {}
    assert rules([70, 69.5]) == {}
    assert max_level(["LOW", "HIGH", "MEDIUM"]) == "HIGH"


def test_ew_thresholds_configurable():
    cfg = dict(CFG, ew_drop_points="5")
    assert {a["rule"] for a in evaluate_series([90, 84], cfg=cfg)} == {"EW-02"}


# ---------------------------------------------------------------- data demo kecil
@pytest.fixture(scope="module")
def demo():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_demo(employees=24, months=7, seed=3)
        yield app
        db.session.remove()
        reset_db()


@pytest.fixture(autouse=True)
def _rl():
    ratelimit.reset()


def test_seed_sane(demo):
    assert Employee.query.count() > 20
    assert EvaluationPeriod.query.filter_by(status="FINALIZED").count() == 7
    assert EvaluationPeriod.query.filter_by(status="RUNNING").count() == 1
    assert PerformanceScore.query.filter_by(status="FINALIZED").count() > 100
    assert EarlyWarning.query.count() > 0  # pola 'down' memicu peringatan


def test_prediction_baseline_and_ml(demo):
    emp = Employee.query.filter(Employee.employee_number != "HR-001").first()
    p = prediction.predict_employee(emp.id)
    assert p["available"] and 0 <= p["low"] <= p["predicted_score"] <= p["high"] <= 100
    assert p["method"] in ("baseline_linear", "gradient_boosting") and p["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    admin_emp = Employee.query.filter_by(employee_number="HR-001").first()
    assert prediction.predict_employee(admin_emp.id)["available"] is False
    assert prediction.baseline_predict([60, 65, 70, 75]) == pytest.approx(80, abs=0.01)
    assert prediction.baseline_predict([90, 90, 90, 90]) == pytest.approx(90)
    assert prediction.baseline_predict([95, 98, 100, 100]) <= 100


def _target(demo):
    s = (PerformanceScore.query.join(Employee, Employee.id == PerformanceScore.employee_id)
         .join(EvaluationPeriod, EvaluationPeriod.id == PerformanceScore.period_id).filter(EvaluationPeriod.status == "FINALIZED")
         .order_by(EvaluationPeriod.start_date.desc()).first())
    return s.employee, s.period


def test_insight_rule_based_fallback_and_cache(demo):
    emp, per = _target(demo)
    admin = User.query.filter_by(username="admin").first()
    i1, cached = ins.generate_insight(admin, emp, per, force=True)
    assert not cached and i1.source == "rules" and i1.model_name == "rule-based" and i1.prompt_version
    d = ins.insight_dict(i1)
    assert d["risk_level"] in ("LOW", "MEDIUM", "HIGH") and d["trend"] in ins.TRENDS and "perlu ditinjau manusia" in d["label"]
    ctx = ins.build_context(emp, per)
    assert ins.validate_insight({k: d[k] for k in ("summary", "trend", "strengths", "weaknesses", "recommendations", "risk_level", "evidence")}, ins.to_payload(ctx, emp.id)) == []
    i2, cached = ins.generate_insight(admin, emp, per)
    assert cached and i2.id == i1.id


def test_payload_is_anonymised(demo):
    emp, per = _target(demo)
    payload = json.dumps(ins.to_payload(ins.build_context(emp, per), emp.id))
    assert emp.name not in payload and emp.employee_number not in payload and emp.email not in payload
    assert ins.ref_for(emp.id).startswith("EMP-") and ins.ref_for(emp.id) == ins.ref_for(emp.id)


def test_validate_rejects_hallucination(demo):
    emp, per = _target(demo)
    payload = ins.to_payload(ins.build_context(emp, per), emp.id)
    good = ins.rule_based(ins.build_context(emp, per))
    assert ins.validate_insight(good, payload) == []
    bad = json.loads(json.dumps(good))
    bad["evidence"] = [{"kpi": payload["kpis"][0]["kpi"], "achievement": 3.0 if payload["kpis"][0]["achievement"] > 10 else 77.7}]
    assert any("tidak cocok" in e for e in ins.validate_insight(bad, payload))
    bad2 = dict(good, evidence=[{"kpi": "KPI Karangan", "achievement": 50}])
    assert ins.validate_insight(bad2, payload)
    assert ins.validate_insight(dict(good, risk_level="EXTREME"), payload)
    assert ins.validate_insight(dict(good, trend="up"), payload)
    assert ins.validate_insight({"summary": "x"}, payload)
    assert ins.validate_insight("bukan json", payload)


def test_llm_path_valid_invalid_and_down(demo, monkeypatch):
    emp, per = _target(demo)
    admin = User.query.filter_by(username="admin").first()
    ctx = ins.build_context(emp, per)
    payload = ins.to_payload(ctx, emp.id)
    good = ins.rule_based(ctx)
    good["risk_level"] = "LOW"
    demo.config.update(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="x")
    try:
        sent = {}
        def ok(system, user, **kw):
            sent["user"] = user
            return "```json\n" + json.dumps(good) + "\n```"
        monkeypatch.setattr(llm_client, "complete_text", ok)
        i, _ = ins.generate_insight(admin, emp, per, force=True)
        assert i.source == "llm" and i.model_name == demo.config["LLM_MODEL"]
        assert emp.name not in sent["user"]  # anonim
        # LLM mengarang angka -> 2 kali percobaan lalu fallback ke aturan
        bad = dict(good, evidence=[{"kpi": payload["kpis"][0]["kpi"], "achievement": 1.23}])
        calls = []
        monkeypatch.setattr(llm_client, "complete_text", lambda *a, **k: calls.append(1) or json.dumps(bad))
        i, _ = ins.generate_insight(admin, emp, per, force=True)
        assert i.source == "rules" and len(calls) == 2
        # LLM tidak tersedia
        def boom(*a, **k): raise llm_client.LLMUnavailable("down")
        monkeypatch.setattr(llm_client, "complete_text", boom)
        i, _ = ins.generate_insight(admin, emp, per, force=True)
        assert i.source == "rules"
        # LLM menilai risiko lebih rendah dari aturan -> dinaikkan ke penilaian aturan
        rules_risk = ins.rule_based(ctx)["risk_level"]
        monkeypatch.setattr(llm_client, "complete_text", ok)
        i, _ = ins.generate_insight(admin, emp, per, force=True)
        assert ins.LEVEL_ORDER[i.risk_level] >= ins.LEVEL_ORDER[rules_risk]
    finally:
        demo.config.update(LLM_PROVIDER="none", ANTHROPIC_API_KEY="")


# ---------------------------------------------------------------- chat: cakupan RBAC
def _u(name):
    return User.query.filter_by(username=name).first()


def test_chat_scope_and_refusal(demo):
    admin, spv = _u("admin"), _u("spv.it")
    emp = _u("emp002")
    top_admin = chat.answer(admin, "Siapa performer tertinggi?")["answer"]
    assert "1." in top_admin and "Sumber" in top_admin and "seluruh organisasi" in top_admin
    r = chat.answer(spv, "Siapa performer tertinggi?")
    team_names = {e.name for e in Employee.query.filter_by(supervisor_id=spv.employee.id)} | {spv.employee.name}
    listed = [l for l in r["answer"].splitlines() if l[:2] in ("1.", "2.", "3.")]
    assert listed and all(any(n in l for n in team_names) for l in listed)
    own = chat.answer(emp, "Siapa performer tertinggi?")["answer"]
    assert emp.employee.name in own and "2." not in own  # hanya diri sendiri
    assert "Skor Anda" in chat.answer(emp, "Berapa skor saya?")["answer"]
    for q in ("Siapa presiden Indonesia?", "tulis puisi tentang bulan", "", "DROP TABLE users"):
        assert chat.answer(emp, q)["answer"]
    assert "hanya dapat menjawab" in chat.answer(emp, "tulis puisi tentang bulan")["answer"]
    assert "hanya dapat menjawab" in chat.answer(admin, "DROP TABLE users; --")["answer"]
    dept_q = chat.answer(admin, "KPI apa yang paling banyak tidak tercapai di departemen IT?")["answer"]
    assert "tidak tercapai" in dept_q and "Teknologi Informasi" in dept_q
    assert "turun" in chat.answer(admin, "Mengapa rata-rata skor menurun?")["answer"] or "naik" in chat.answer(admin, "Mengapa rata-rata skor menurun?")["answer"]
    assert "tidak ditemukan" in chat.answer(admin, "performer tertinggi di departemen Antariksa")["answer"] or "1." in chat.answer(admin, "performer tertinggi di departemen Antariksa")["answer"]


def test_chat_tool_function_cannot_cross_scope(demo):
    emp = _u("emp002")
    ctx = chat.Ctx(emp, False)
    res = chat.execute_tool("get_top_performers", {"limit": 10}, ctx)
    assert {r["employee"] for r in res["rows"]} == {emp.employee.name}
    res = chat.execute_tool("get_alerts", {}, ctx)
    assert all(r["employee"] == emp.employee.name for r in res["rows"])
    assert chat.execute_tool("drop_tables", {}, ctx)["error"]


def test_chat_llm_tool_loop_deanonymises(demo, monkeypatch):
    admin = _u("admin")
    demo.config.update(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
    seen = {}
    def fake(system, question, tools, executor, **kw):
        out = executor("get_top_performers", {"limit": 1})
        seen["row"] = out["rows"][0]["employee"]
        return f"Tertinggi adalah {seen['row']}."
    monkeypatch.setattr(llm_client, "run_tool_chat", fake)
    try:
        r = chat.answer(admin, "siapa tertinggi?")
        assert seen["row"].startswith("EMP-")  # LLM hanya melihat ID pengganti
        assert "EMP-" not in r["answer"] and r["mode"] == "llm"
        def down(*a, **k): raise llm_client.LLMUnavailable("x")
        monkeypatch.setattr(llm_client, "run_tool_chat", down)
        assert chat.answer(admin, "Siapa performer tertinggi?")["mode"] == "rules"
    finally:
        demo.config.update(LLM_PROVIDER="none", ANTHROPIC_API_KEY="")


# ---------------------------------------------------------------- HTTP AI + laporan
def test_ai_api_and_reports(demo):
    c = demo.test_client()
    login(c, "admin", "Admin#2026!")
    emp, per = _target(demo)
    r = c.post("/api/v1/ai/performance-analysis", json={"employee_id": emp.id, "period_id": per.id})
    assert r.status_code == 200 and r.get_json()["label"]
    iid = r.get_json()["id"]
    assert c.post(f"/api/v1/ai/insights/{iid}/rating", json={"rating": 1}).status_code == 200
    assert c.post(f"/api/v1/ai/insights/{iid}/rating", json={"rating": 5}).status_code == 422
    p = c.post("/api/v1/ai/prediction", json={"employee_id": emp.id, "period_id": per.id}).get_json()
    assert "available" in p and "alerts" in p
    assert c.post("/api/v1/ai/chat", json={"message": "KPI apa yang paling banyak tidak tercapai?"}).get_json()["answer"]
    for t, f in (("employee", "pdf"), ("employee", "csv"), ("department", "pdf"), ("department", "xlsx"), ("management", "pdf"), ("management", "xlsx")):
        body = {"report_type": t, "fmt": f, "period_id": per.id, "employee_id": emp.id}
        j = c.post("/api/v1/reports", json=body)
        assert j.status_code == 202, (t, f, j.get_data(as_text=True))
        d = c.get(f"/api/v1/reports/{j.get_json()['id']}/download")
        assert d.status_code == 200 and len(d.data) > 200, (t, f)
        if f == "pdf": assert d.data[:5] == b"%PDF-"
        if f == "xlsx": assert d.data[:2] == b"PK"
        if f == "csv": assert "Ticket" in d.data.decode("utf-8-sig") or "KPI" in d.data.decode("utf-8-sig")
    assert c.post("/api/v1/reports", json={"report_type": "employee", "fmt": "xlsx", "period_id": per.id, "employee_id": emp.id}).status_code == 422
    # employee: tidak boleh laporan manajemen / milik orang lain / AI prediksi
    ce = demo.test_client(); login(ce, "emp002")
    assert ce.post("/api/v1/reports", json={"report_type": "management", "fmt": "pdf", "period_id": per.id}).status_code == 403
    assert ce.post("/api/v1/reports", json={"report_type": "employee", "fmt": "pdf", "period_id": per.id, "employee_id": _u("spv.it").employee.id}).status_code == 403
    assert ce.post("/api/v1/ai/prediction", json={}).status_code == 403
    j = ce.post("/api/v1/reports", json={"report_type": "employee", "fmt": "pdf", "period_id": per.id})
    assert j.status_code == 202
    # laporan milik pengguna lain tidak dapat diunduh
    assert c.get(f"/api/v1/reports/{j.get_json()['id']}/download").status_code == 404


def test_rate_limit_ai(demo):
    c = demo.test_client(); login(c, "admin", "Admin#2026!")
    codes = [c.post("/api/v1/ai/chat", json={"message": "halo"}).status_code for _ in range(22)]
    assert codes.count(429) >= 1 and codes[0] == 200
