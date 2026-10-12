"""Abstraksi provider LLM: none | anthropic | openai (kompatibel OpenAI, termasuk LLM lokal).
Semua kegagalan jaringan/format dibungkus LLMUnavailable agar pemanggil dapat fallback ke aturan."""
import json
import requests
from flask import current_app


class LLMUnavailable(Exception):
    pass


def provider():
    return current_app.config.get("LLM_PROVIDER", "none")


def is_enabled():
    p = provider()
    if p == "anthropic":
        return bool(current_app.config.get("ANTHROPIC_API_KEY"))
    return p == "openai"


def is_external():
    """True bila data meninggalkan infrastruktur sendiri (pakai anonimisasi)."""
    if provider() == "anthropic":
        return True
    base = current_app.config.get("OPENAI_BASE_URL", "")
    return provider() == "openai" and not any(h in base for h in ("localhost", "127.0.0.1", "host.docker.internal"))


def model_name():
    return current_app.config["LLM_MODEL"] if is_enabled() else "rule-based"


def _post(url, headers, body):
    try:
        r = requests.post(url, headers=headers, json=body, timeout=current_app.config["LLM_TIMEOUT"])
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise LLMUnavailable(str(e)[:200])


def _anthropic(system, messages, max_tokens, temperature, tools=None):
    body = {"model": current_app.config["LLM_MODEL"], "max_tokens": max_tokens, "temperature": temperature,
            "system": system, "messages": messages}
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    return _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": current_app.config["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                  "content-type": "application/json"}, body)


def _openai(system, messages, max_tokens, temperature, tools=None):
    cfg = current_app.config
    body = {"model": cfg["LLM_MODEL"], "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "system", "content": system}] + messages}
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    headers = {"content-type": "application/json"}
    if cfg.get("OPENAI_API_KEY"):
        headers["authorization"] = "Bearer " + cfg["OPENAI_API_KEY"]
    return _post(cfg["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions", headers, body)


def complete_text(system, user, max_tokens=900, temperature=0.2):
    if not is_enabled():
        raise LLMUnavailable("LLM tidak dikonfigurasi")
    if provider() == "anthropic":
        data = _anthropic(system, [{"role": "user", "content": user}], max_tokens, temperature)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    data = _openai(system, [{"role": "user", "content": user}], max_tokens, temperature)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMUnavailable("Respons LLM tidak dikenali")


def parse_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1:
        raise ValueError("tidak ada JSON")
    return json.loads(t[a:b + 1])


def run_tool_chat(system, question, tools, executor, max_steps=5, max_tokens=900):
    """Loop function-calling. executor(name, args) -> dict. LLM tidak pernah menulis SQL bebas."""
    if not is_enabled():
        raise LLMUnavailable("LLM tidak dikonfigurasi")
    if provider() == "anthropic":
        msgs = [{"role": "user", "content": question}]
        for _ in range(max_steps):
            data = _anthropic(system, msgs, max_tokens, 0.1, tools)
            blocks = data.get("content", [])
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            if not calls:
                return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            msgs.append({"role": "assistant", "content": blocks})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(executor(c["name"], c.get("input", {})), default=str)}
                for c in calls]})
        raise LLMUnavailable("Terlalu banyak langkah tool")
    msgs = [{"role": "user", "content": question}]
    for _ in range(max_steps):
        data = _openai(system, msgs, max_tokens, 0.1, tools)
        try:
            m = data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise LLMUnavailable("Respons LLM tidak dikenali")
        calls = m.get("tool_calls") or []
        if not calls:
            return (m.get("content") or "").strip()
        msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": calls})
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            msgs.append({"role": "tool", "tool_call_id": c["id"],
                         "content": json.dumps(executor(c["function"]["name"], args), default=str)})
    raise LLMUnavailable("Terlalu banyak langkah tool")
