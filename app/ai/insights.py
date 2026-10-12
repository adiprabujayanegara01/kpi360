"""Pipeline AI Insight: ambil data -> agregasi -> anonimisasi -> prompt -> LLM -> validasi JSON -> simpan.
Bila LLM tidak tersedia/tidak valid, dipakai ringkasan berbasis aturan (fallback)."""
import hashlib
import hmac
import json
from flask import current_app
from ..extensions import db
from ..models import AiInsight, PerformanceScore
from ..analytics import queries as aq
from . import llm_client
from .early_warning import evaluate_employee, max_level, top_influencers, LEVEL_ORDER
from .prediction import predict_employee

TRENDS = ("increasing", "decreasing", "stable")
RISKS = ("LOW", "MEDIUM", "HIGH")

SYSTEM_PROMPT = (
    "Anda analis kinerja SDM. Anda HANYA boleh memakai data pada pesan pengguna. "
    "Jangan mengarang angka, jangan menebak atribut pribadi, dan jangan memberi keputusan karier "
    "(promosi/mutasi/PHK). Tulis dalam Bahasa Indonesia yang singkat dan sopan. "
    "Balas HANYA dengan satu objek JSON valid (tanpa markdown) dengan kunci: "
    '"summary" (string, maks 2 kalimat), "trend" (increasing|decreasing|stable), "strengths" (array string), '
    '"weaknesses" (array string), "recommendations" (array 2-4 string, konkret & dapat ditindaklanjuti), '
    '"risk_level" (LOW|MEDIUM|HIGH), "evidence" (array objek {"kpi": nama KPI persis dari data, "achievement": angka persis dari data}). '
    "Sertakan evidence minimal untuk setiap kelemahan dan kekuatan yang Anda sebut."
)


def ref_for(employee_id):
    key = current_app.config["SECRET_KEY"].encode()
    return "EMP-" + hmac.new(key, str(employee_id).encode(), hashlib.sha256).hexdigest()[:6].upper()


def _trend(scores):
    if len(scores) < 2:
        return "stable"
    w = scores[-3:]
    d = w[-1] - w[0]
    return "increasing" if d >= 2 else "decreasing" if d <= -2 else "stable"


def build_context(emp, period):
    hist = aq.employee_history(emp.id, period.start_date)[-6:]
    kpis = aq.kpi_breakdown(emp.id, period.id)
    score = PerformanceScore.query.filter_by(employee_id=emp.id, period_id=period.id).first()
    alerts = evaluate_employee(emp.id, period) if score else []
    pred = predict_employee(emp.id)
    return {"score": float(score.total_score) if score else None, "category": score.category if score else None,
            "history": [{"period": h["period"], "score": h["score"]} for h in hist],
            "kpis": kpis, "alerts": alerts, "prediction": pred, "period": period.name}


def to_payload(ctx, emp_id):
    """Data minimal & anonim untuk LLM: tanpa nama/jabatan/atribut sensitif."""
    return {"employee_ref": ref_for(emp_id), "period": ctx["period"], "score": ctx["score"], "category": ctx["category"],
            "history": ctx["history"],
            "kpis": [{"kpi": k["kpi"], "category": k["category"], "weight": k["weight"], "target": k["target"],
                      "realization": k["realization"], "achievement": round(k["achievement"], 1), "counted": not k["incomplete"]}
                     for k in ctx["kpis"]],
            "rule_alerts": [{"rule": a["rule"], "level": a["level"], "message": a["message"]} for a in ctx["alerts"]],
            "forecast": ({k: ctx["prediction"][k] for k in ("predicted_score", "low", "high", "risk_level")}
                         if ctx["prediction"].get("available") else None)}


def validate_insight(obj, payload):
    """Kembalikan daftar kesalahan (kosong = valid). Angka evidence dicocokkan dengan database."""
    errs = []
    if not isinstance(obj, dict):
        return ["bukan objek"]
    if not isinstance(obj.get("summary"), str) or not obj["summary"].strip() or len(obj["summary"]) > 700:
        errs.append("summary")
    if obj.get("trend") not in TRENDS:
        errs.append("trend")
    if obj.get("risk_level") not in RISKS:
        errs.append("risk_level")
    for k in ("strengths", "weaknesses", "recommendations"):
        v = obj.get(k)
        if not isinstance(v, list) or len(v) > 6 or not all(isinstance(x, str) and x.strip() for x in v):
            errs.append(k)
    if isinstance(obj.get("recommendations"), list) and not obj["recommendations"]:
        errs.append("recommendations kosong")
    ev = obj.get("evidence")
    actual = {k["kpi"].lower(): k["achievement"] for k in payload["kpis"]}
    if not isinstance(ev, list):
        errs.append("evidence")
    else:
        for e in ev:
            try:
                name, val = str(e["kpi"]).lower(), float(e["achievement"])
            except (KeyError, TypeError, ValueError):
                errs.append("evidence item")
                continue
            if name not in actual or abs(actual[name] - val) > 0.6:
                errs.append(f"evidence tidak cocok database: {e.get('kpi')}")
    return errs


def rule_based(ctx):
    kp = sorted(ctx["kpis"], key=lambda k: k["achievement"])
    strong = [k for k in kp if k["achievement"] >= 95 and not k["incomplete"]][::-1][:3]
    weak = [k for k in kp if k["achievement"] < 85][:3]
    scores = [h["score"] for h in ctx["history"]]
    trend = _trend(scores)
    risk = max_level([a["level"] for a in ctx["alerts"]] + [ctx["prediction"].get("risk_level") if ctx["prediction"].get("available") else None])
    if ctx["alerts"] and risk == "LOW":
        risk = "LOW"
    t_txt = {"increasing": "cenderung naik", "decreasing": "cenderung turun", "stable": "relatif stabil"}[trend]
    sc = ctx["score"]
    summary = f"Skor {ctx['period']} adalah {sc:.1f} ({ctx['category']}), tren {t_txt}"
    if len(scores) >= 2:
        summary += f" ({scores[-2]:.1f} → {scores[-1]:.1f})"
    summary += "."
    if weak:
        summary += f" Perhatian utama: {weak[0]['kpi']} ({weak[0]['achievement']:.0f}%)."
    recs = []
    for k in weak[:2]:
        recs.append(f"Susun rencana peningkatan untuk {k['kpi']} dan tinjau progresnya secara berkala.")
    inc = [k for k in ctx["kpis"] if k["incomplete"]]
    if inc:
        recs.append(f"Lengkapi realisasi untuk {len(inc)} KPI yang belum disetujui agar skor mencerminkan kinerja sebenarnya.")
    if trend == "decreasing":
        recs.append("Jadwalkan sesi 1:1 dengan atasan untuk membahas hambatan dalam beberapa periode terakhir.")
    if strong:
        recs.append(f"Pertahankan capaian pada {strong[0]['kpi']} dan bagikan praktik baiknya ke rekan satu tim.")
    if not recs:
        recs.append("Pertahankan konsistensi dan tetapkan target pengembangan baru untuk periode berikutnya.")
    return {"summary": summary, "trend": trend,
            "strengths": [f"{k['kpi']} tercapai {k['achievement']:.0f}%" for k in strong] or ["Belum ada KPI dengan pencapaian ≥95%"],
            "weaknesses": [f"{k['kpi']} baru {k['achievement']:.0f}% dari target" for k in weak] or ["Tidak ada KPI di bawah 85%"],
            "recommendations": recs[:4], "risk_level": risk,
            "evidence": [{"kpi": k["kpi"], "achievement": round(k["achievement"], 1)} for k in (weak + strong)]}


def _llm_insight(payload):
    user = "Data kinerja (anonim):\n" + json.dumps(payload, ensure_ascii=False, default=str)
    last = None
    for _ in range(2):  # coba ulang sekali
        try:
            obj = llm_client.parse_json(llm_client.complete_text(SYSTEM_PROMPT, user, max_tokens=900, temperature=0.1))
        except (ValueError, llm_client.LLMUnavailable) as e:
            last = str(e)
            continue
        errs = validate_insight(obj, payload)
        if not errs:
            return obj
        last = "; ".join(errs)
    raise llm_client.LLMUnavailable("Keluaran LLM tidak valid: " + str(last))


def generate_insight(actor, emp, period, force=False):
    """Hasilkan & simpan insight. Cache per (karyawan, periode) kecuali force."""
    if not force:
        ex = (AiInsight.query.filter_by(employee_id=emp.id, period_id=period.id, type="PERFORMANCE")
              .order_by(AiInsight.id.desc()).first())
        if ex:
            return ex, True
    ctx = build_context(emp, period)
    if ctx["score"] is None:
        raise ValueError("Karyawan belum memiliki skor pada periode ini.")
    payload = to_payload(ctx, emp.id)
    rules = rule_based(ctx)
    source, model, obj = "rules", "rule-based", rules
    if llm_client.is_enabled():
        try:
            obj = _llm_insight(payload)
            source, model = "llm", llm_client.model_name()
            # pengaman: risiko tidak boleh lebih rendah dari penilaian aturan eksplisit
            if LEVEL_ORDER[rules["risk_level"]] > LEVEL_ORDER[obj["risk_level"]]:
                obj["risk_level"] = rules["risk_level"]
        except llm_client.LLMUnavailable as e:
            current_app.logger.warning("LLM fallback ke aturan: %s", e)
    ins = AiInsight(employee_id=emp.id, period_id=period.id, type="PERFORMANCE", summary=obj["summary"],
                    trend=obj["trend"], strengths=obj["strengths"], weaknesses=obj["weaknesses"],
                    recommendations=obj["recommendations"], evidence=obj["evidence"], risk_level=obj["risk_level"],
                    source=source, model_name=model, prompt_version=current_app.config["PROMPT_VERSION"],
                    generated_by=actor.id if actor else None)
    db.session.add(ins)
    from ..security.audit import audit
    audit("AI_INSIGHT", "ai_insights", None, new={"employee_id": emp.id, "period_id": period.id, "source": source}, user=actor)
    db.session.commit()
    return ins, False


def insight_dict(ins):
    return {"id": ins.id, "employee_id": ins.employee_id, "period": ins.period.name, "summary": ins.summary,
            "trend": ins.trend, "strengths": ins.strengths, "weaknesses": ins.weaknesses,
            "recommendations": ins.recommendations, "risk_level": ins.risk_level, "evidence": ins.evidence,
            "source": ins.source, "model": ins.model_name, "prompt_version": ins.prompt_version,
            "label": "Dihasilkan AI - perlu ditinjau manusia" if ins.source == "llm" else "Ringkasan otomatis berbasis aturan - perlu ditinjau manusia",
            "created_at": ins.created_at.isoformat(), "quality_rating": ins.quality_rating}
