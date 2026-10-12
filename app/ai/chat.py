"""AI Chat Assistant aman: LLM hanya memanggil fungsi terparameter (function calling), tidak menulis SQL.
Setiap fungsi menerapkan cakupan RBAC pengguna yang login. Tanpa LLM, router aturan dipakai."""
import re
from ..extensions import db
from ..models import EvaluationPeriod, Department, PerformanceScore, EarlyWarning, Employee, ADMIN, SUPERVISOR
from ..analytics import queries as aq
from ..security.rbac import visible_scores
from ..services.common import pick_period
from . import llm_client
from .insights import ref_for

TOOLS = [
    {"name": "get_top_performers", "description": "Daftar karyawan dengan skor tertinggi/terendah pada satu periode (dalam cakupan pengguna).",
     "parameters": {"type": "object", "properties": {"department": {"type": "string", "description": "Nama atau kode departemen (opsional)"},
                                                     "period": {"type": "string", "description": "Nama periode, kosong = terbaru"},
                                                     "limit": {"type": "integer"}, "order": {"type": "string", "enum": ["top", "bottom"]}}}},
    {"name": "get_kpi_underperformance", "description": "KPI yang paling banyak tidak tercapai (achievement < 100%).",
     "parameters": {"type": "object", "properties": {"department": {"type": "string"}, "period": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "get_department_averages", "description": "Rata-rata skor per departemen pada satu periode.",
     "parameters": {"type": "object", "properties": {"period": {"type": "string"}}}},
    {"name": "explain_trend", "description": "Jelaskan perubahan rata-rata skor dibanding periode sebelumnya dan KPI penyebab terbesar.",
     "parameters": {"type": "object", "properties": {"department": {"type": "string"}, "period": {"type": "string"}}}},
    {"name": "get_my_summary", "description": "Ringkasan skor dan KPI milik pengguna yang sedang login.",
     "parameters": {"type": "object", "properties": {"period": {"type": "string"}}}},
    {"name": "get_alerts", "description": "Peringatan dini (early warning) dalam cakupan pengguna.",
     "parameters": {"type": "object", "properties": {"period": {"type": "string"}}}},
]

SYSTEM = ("Anda asisten data kinerja KPI360. Jawab HANYA berdasarkan hasil fungsi yang dipanggil; jangan mengarang angka. "
          "Gunakan Bahasa Indonesia, ringkas. Karyawan dirujuk dengan kode EMP-XXXXXX apa adanya. "
          "Sebutkan periode dan filter yang dipakai. Jika pertanyaan di luar data kinerja, tolak dengan sopan. "
          "Jangan memberi keputusan promosi/mutasi/PHK.")

REFUSAL = ("Maaf, saya hanya dapat menjawab pertanyaan tentang data kinerja dalam cakupan akses Anda "
           "(skor, KPI, tren, peringatan dini). Coba misalnya: “Siapa performer tertinggi di departemen IT?” "
           "atau “KPI apa yang paling banyak tidak tercapai?”.")


class Ctx:
    def __init__(self, user, anonymize):
        self.user, self.anonymize, self.names, self.sources = user, anonymize, {}, []

    def who(self, emp_id, name):
        if self.anonymize:
            ref = ref_for(emp_id)
            self.names[ref] = name
            return ref
        return name


def _period(txt):
    if txt:
        t = txt.strip().lower()
        for p in EvaluationPeriod.query.all():
            if p.name.lower() == t or t in p.name.lower():
                return p
    return pick_period()


def _dept(txt):
    if not txt:
        return None
    t = txt.strip().lower()
    for d in Department.query.all():
        if t in (d.code.lower(), d.name.lower()) or t in d.name.lower() or d.code.lower() == t:
            return d
    return None


def _scope_note(user):
    return {"ADMIN": "seluruh organisasi", "SUPERVISOR": "tim Anda", "EMPLOYEE": "data pribadi Anda"}[user.role]


def execute_tool(name, args, ctx):
    u, args = ctx.user, args or {}
    p = _period(args.get("period"))
    if not p:
        return {"error": "Belum ada periode."}
    d = _dept(args.get("department"))
    if args.get("department") and not d:
        return {"error": f"Departemen '{args['department']}' tidak ditemukan."}
    src = {"period": p.name, "department": d.name if d else "semua", "cakupan": _scope_note(u)}
    ctx.sources.append(src)
    if name == "get_top_performers":
        df = aq.ranking(aq.score_rows(u, p.id, d.id if d else None))
        if df.empty:
            return {"source": src, "rows": [], "note": "Tidak ada data pada cakupan Anda."}
        lim = max(1, min(int(args.get("limit") or 3), 10))
        df = df.iloc[::-1] if args.get("order") == "bottom" else df
        rows = [{"employee": ctx.who(r.employee_id, r.employee), "score": r.score, "category": r.category,
                 "department": r.department} for r in df.head(lim).itertuples()]
        return {"source": src, "rows": rows}
    if name == "get_kpi_underperformance":
        rows = aq.kpi_stats(u, p.id, d.id if d else None)[: max(1, min(int(args.get("limit") or 5), 10))]
        return {"source": src, "rows": [{"kpi": r["kpi"], "unmet": r["unmet"], "of": r["n"], "unmet_pct": r["unmet_pct"],
                                         "avg_achievement": r["avg"]} for r in rows]}
    if name == "get_department_averages":
        return {"source": src, "rows": aq.dept_averages(aq.score_rows(u, p.id))}
    if name == "get_my_summary":
        if not u.employee:
            return {"error": "Akun Anda tidak terhubung ke data karyawan."}
        s = PerformanceScore.query.filter_by(employee_id=u.employee.id, period_id=p.id).first()
        if not s:
            return {"source": src, "note": "Belum ada skor pada periode ini."}
        kp = sorted(aq.kpi_breakdown(u.employee.id, p.id), key=lambda k: k["achievement"])
        return {"source": src, "score": float(s.total_score), "category": s.category,
                "weakest_kpis": [{"kpi": k["kpi"], "achievement": round(k["achievement"], 1)} for k in kp[:3]]}
    if name == "get_alerts":
        q = EarlyWarning.query.filter_by(period_id=p.id)
        ids = [s.employee_id for s in visible_scores(u).filter(PerformanceScore.period_id == p.id).all()]
        rows = [w for w in q.all() if w.employee_id in set(ids)]
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        rows.sort(key=lambda w: order[w.level])
        return {"source": src, "rows": [{"employee": ctx.who(w.employee_id, w.employee.name), "rule": w.rule,
                                         "level": w.level, "message": w.message} for w in rows[:10]]}
    if name == "explain_trend":
        prev = (EvaluationPeriod.query.join(PerformanceScore, PerformanceScore.period_id == EvaluationPeriod.id)
                .filter(EvaluationPeriod.start_date < p.start_date).order_by(EvaluationPeriod.start_date.desc()).first())
        if not prev:
            return {"source": src, "note": "Tidak ada periode sebelumnya untuk dibandingkan."}
        cur = aq.score_rows(u, p.id, d.id if d else None)
        old = aq.score_rows(u, prev.id, d.id if d else None)
        if cur.empty or old.empty:
            return {"source": src, "note": "Data belum cukup untuk perbandingan."}
        kd = aq.kpi_avg_by_period(u, [p.id, prev.id], d.id if d else None)
        a = kd[kd.period_id == p.id].set_index("kpi")["avg"]
        b = kd[kd.period_id == prev.id].set_index("kpi")["avg"]
        diff = (a - b).dropna().sort_values()
        src2 = {**src, "dibanding": prev.name}
        ctx.sources.append(src2)
        return {"source": src2, "avg_now": round(float(cur.score.mean()), 2), "avg_before": round(float(old.score.mean()), 2),
                "change": round(float(cur.score.mean() - old.score.mean()), 2),
                "kpi_changes_largest_drop": [{"kpi": k, "change": round(float(v), 1)} for k, v in diff.head(3).items() if v < 0],
                "kpi_changes_largest_gain": [{"kpi": k, "change": round(float(v), 1)} for k, v in diff.tail(2).items() if v > 0]}
    return {"error": "Fungsi tidak dikenal."}


# ----------------------------------------------------------------------- fallback berbasis aturan
def _fmt_src(s):
    return f"Sumber: periode {s['period']}, departemen {s['department']}, cakupan {s['cakupan']}."


def _route(q):
    t = q.lower()
    args = {}
    for d in Department.query.all():
        if d.code.lower() in re.findall(r"[a-z]+", t) or d.name.lower() in t:
            args["department"] = d.code
    for p in EvaluationPeriod.query.all():
        if p.name.lower() in t:
            args["period"] = p.name
    has = lambda *w: any(x in t for x in w)  # noqa: E731
    if has("saya", "milik saya", "punya saya", "my score", "nilai saya", "skor saya"):
        return "get_my_summary", args
    if has("mengapa", "kenapa", "penyebab", "turun", "menurun", "naik"):
        return "explain_trend", args
    if has("tidak tercapai", "gagal", "kpi apa", "kpi mana", "terendah kpi", "underperform"):
        return "get_kpi_underperformance", args
    if has("peringatan", "early warning", "risiko", "perlu perhatian", "bermasalah"):
        return "get_alerts", args
    if has("rata-rata", "rata rata", "per departemen", "average"):
        return "get_department_averages", args
    if has("tertinggi", "terbaik", "top", "performer", "juara", "ranking", "peringkat"):
        return "get_top_performers", args
    if has("terendah", "terburuk", "paling rendah", "low performer", "pendampingan"):
        args["order"] = "bottom"
        return "get_top_performers", args
    return None, args


def _render(name, res, ctx):
    if "error" in res:
        return res["error"]
    s = res.get("source", {})
    lines = []
    if res.get("note"):
        lines.append(res["note"])
    if name == "get_top_performers":
        for i, r in enumerate(res["rows"], 1):
            lines.append(f"{i}. {r['employee']} - skor {r['score']:.2f} ({r['category']}, {r['department'] or '-'})")
    elif name == "get_kpi_underperformance":
        for r in res["rows"]:
            lines.append(f"• {r['kpi']}: {r['unmet']} dari {r['of']} tidak tercapai ({r['unmet_pct']}%), rata-rata achievement {r['avg_achievement']}%")
    elif name == "get_department_averages":
        for r in res["rows"]:
            lines.append(f"• {r['department'] or '-'}: {r['avg']:.2f} ({r['n']} karyawan)")
    elif name == "get_my_summary" and "score" in res:
        lines.append(f"Skor Anda {res['score']:.2f} ({res['category']}).")
        lines += [f"• KPI terlemah: {k['kpi']} ({k['achievement']}%)" for k in res["weakest_kpis"]]
    elif name == "get_alerts":
        lines += [f"• [{r['level']}] {r['employee']}: {r['message']} ({r['rule']})" for r in res["rows"]] or ["Tidak ada peringatan dini dalam cakupan Anda."]
    elif name == "explain_trend" and "change" in res:
        ch = res["change"]
        lines.append(f"Rata-rata skor {'naik' if ch > 0 else 'turun' if ch < 0 else 'tetap'} {abs(ch):.2f} poin ({res['avg_before']:.2f} → {res['avg_now']:.2f}) dibanding {s.get('dibanding')}.")
        if res["kpi_changes_largest_drop"]:
            lines.append("KPI dengan penurunan terbesar: " + "; ".join(f"{k['kpi']} ({k['change']:+.1f} poin)" for k in res["kpi_changes_largest_drop"]) + ".")
        if res["kpi_changes_largest_gain"]:
            lines.append("KPI yang membaik: " + "; ".join(f"{k['kpi']} ({k['change']:+.1f} poin)" for k in res["kpi_changes_largest_gain"]) + ".")
    if s:
        lines.append(_fmt_src(s))
    return "\n".join(lines)


def answer(user, question):
    """Kembalikan dict {answer, sources, mode}. Semua data lewat execute_tool (terbatas RBAC)."""
    question = (question or "").strip()[:500]
    if not question:
        return {"answer": "Silakan ketik pertanyaan.", "sources": [], "mode": "none"}
    anonymize = llm_client.is_enabled() and llm_client.is_external() and \
        __import__("flask").current_app.config.get("LLM_ANONYMIZE", True)
    ctx = Ctx(user, anonymize)
    if llm_client.is_enabled():
        try:
            text = llm_client.run_tool_chat(SYSTEM + f" Pengguna berperan {user.role} dengan cakupan {_scope_note(user)}.",
                                            question, TOOLS, lambda n, a: execute_tool(n, a, ctx))
            for ref, nm in ctx.names.items():
                text = text.replace(ref, nm)
            return {"answer": text or REFUSAL, "sources": ctx.sources, "mode": "llm"}
        except llm_client.LLMUnavailable:
            ctx = Ctx(user, False)
    name, args = _route(question)
    if not name:
        return {"answer": REFUSAL, "sources": [], "mode": "rules"}
    res = execute_tool(name, args, ctx)
    return {"answer": _render(name, res, ctx), "sources": ctx.sources, "mode": "rules"}
