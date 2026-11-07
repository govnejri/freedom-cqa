"""LLM-слой: кэш, повторы транспорта, разбор, стоимость, маскирование. Только провайдер fake и заглушки."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from cqa.llm import gateway
from cqa.llm.base import CallContext, ChatMessage, LLMRequest, LLMResponse, Profile, Usage
from cqa.llm.fake import BAD_QUOTE, FakeProvider
from cqa.llm.jsonparse import parse
from cqa.llm.pricing import cost
from cqa.llm.redact import install, redact

SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "quote": {"type": "string"}}, "required": ["id", "quote"],
            "additionalProperties": False}},
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["Да", "Нет"]},
    },
    "required": ["evidence", "reasoning", "verdict"],
    "additionalProperties": False,
}
TRANSCRIPT = (
    "<transcript>\n— оператор —\n"
    "m1 | 00:00 | КЛИЕНТ | Где посмотреть тариф?\n"
    "m2 | 00:05 | ОПЕРАТОР | Здравствуйте! Меня зовут Алина. Тариф указан в разделе «Профиль».\n"
    "</transcript>\nДоказательства — реплики ролей: ОПЕРАТОР.\n"
)


def fake_profile(**kw) -> Profile:
    base = dict(name="fake", provider="fake", model="fake", max_concurrency=16, rpm=100000,
                price={"input": 1.0, "cached_input": 0.1, "output": 2.0})
    base.update(kw)
    return Profile(**base)


def make_req(extra: str = "") -> LLMRequest:
    return LLMRequest(
        messages=[ChatMessage(role="system", content="Ты судья."),
                  ChatMessage(role="user", content=TRANSCRIPT + extra)],
        json_schema=SCHEMA, property_ordering=["evidence", "reasoning", "verdict"],
    )


class CountingProvider:
    """Обёртка над провайдером: считает вызовы."""

    def __init__(self, inner):
        self.inner, self.calls, self.requests = inner, 0, []

    async def complete(self, req, profile):
        self.calls += 1
        self.requests.append(req)
        return await self.inner.complete(req, profile)


class ScriptedProvider:
    """Отдаёт заранее заданные ответы/исключения по очереди."""

    def __init__(self, script):
        self.script, self.calls, self.requests = list(script), 0, []

    async def complete(self, req, profile):
        self.calls += 1
        self.requests.append(req)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def no_sleep(monkeypatch):
    async def _nosleep(_s):
        return None
    monkeypatch.setattr(gateway, "_sleep", _nosleep)


def _ok(text='{"evidence": [], "reasoning": "r", "verdict": "Да"}', **kw) -> LLMResponse:
    return LLMResponse(text=text, usage=Usage(prompt_tokens=100, completion_tokens=10), finish_reason="STOP", **kw)


# ---------- jsonparse / pricing / redact ----------

def test_jsonparse_variants():
    assert parse('{"a": 1}') == {"a": 1}
    assert parse('```json\n{"a": 2}\n```') == {"a": 2}
    assert parse('Вот ответ: {"a": {"b": 3}} — готово') == {"a": {"b": 3}}
    assert parse('[1, 2]') is None
    assert parse('{"a": ') is None
    assert parse("") is None


def test_pricing_formula():
    u = Usage(prompt_tokens=1_000_000, cached_tokens=400_000, completion_tokens=100_000, reasoning_tokens=50_000)
    # (1M − 0.4M)·2 + 0.4M·0.1 + (0.1M + 0.05M)·10 = 1.2 + 0.04 + 1.5
    assert cost(u, {"input": 2.0, "cached_input": 0.1, "output": 10.0}) == pytest.approx(2.74)
    assert cost(u, {"input": None, "cached_input": None, "output": None}) is None
    assert cost(u, {"input": 2.0, "cached_input": None, "output": 10.0}) is None      # есть кэш, цены нет
    assert cost(Usage(prompt_tokens=10), {"input": 1.0, "cached_input": None, "output": 1.0}) == pytest.approx(1e-5)


def test_redact_masks_sig_and_keys():
    url = "https://x.powerplatform.com/api?api-version=1&sp=%2Ftriggers&sv=1.0&" + "sig" + "=AbC123-_xyz&group_id=DEV"
    out = redact(url)
    assert "AbC123" not in out and "sig=***" in out and "group_id=DEV" in out
    fake_aiza = "AIza" + "SyA1234567890abcdefghijk"
    assert fake_aiza not in redact("key " + fake_aiza + " end")
    assert "secretvalue" not in redact("?key=secretvalue&x=1")
    fake_aq = "AQ." + "Fake" * 8  # поддельный ключ собирается в рантайме: хук и git не видят ключей
    assert fake_aq not in redact("token " + fake_aq)


def test_redact_filter_on_logs(caplog):
    install()
    caplog.set_level(logging.INFO)
    logging.getLogger("cqa.some.module").info("fetch %s", "https://h/p?a=1&" + "sig" + "=TOPSECRET123&b=2")
    assert "TOPSECRET123" not in caplog.text
    assert "sig=***" in caplog.text
    assert logging.getLogger("httpx").level == logging.WARNING


# ---------- кэш и журнал ----------

def test_cache_hit_second_call(tmp_db, monkeypatch):
    prov = CountingProvider(FakeProvider())
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    p = fake_profile()
    ctx = CallContext(purpose="judge", uid="T:1", criterion_id="01", run_id=5, evaluation_id=6, criterion_result_id=7)
    r1 = asyncio.run(gateway.complete(make_req(), p, ctx, tmp_db))
    r2 = asyncio.run(gateway.complete(make_req(), p, ctx, tmp_db))
    assert prov.calls == 1
    assert r1.status == "ok" and not r1.cache_hit and r1.cost_usd and r1.cost_usd > 0
    assert r2.cache_hit and r2.cost_usd == 0.0 and r2.parsed == r1.parsed and r2.text == r1.text
    rows = tmp_db.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
    assert [r["cache_hit"] for r in rows] == [0, 1]
    assert rows[1]["cost_usd"] == pytest.approx(r1.cost_usd)        # в журнале — исходная стоимость
    assert all((r["run_id"], r["evaluation_id"], r["criterion_result_id"], r["uid"], r["criterion_id"])
               == (5, 6, 7, "T:1", "01") for r in rows)
    assert r2.call_id == rows[1]["id"]
    assert tmp_db.execute("SELECT hits FROM llm_cache").fetchone()["hits"] == 1


def test_no_con_means_no_cache(monkeypatch):
    prov = CountingProvider(FakeProvider())
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext()))
    r = asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext()))
    assert prov.calls == 2 and not r.cache_hit and r.call_id is None


def test_no_cache_ctx_skips_read_but_writes(tmp_db, monkeypatch):
    prov = CountingProvider(FakeProvider())
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    p = fake_profile()
    asyncio.run(gateway.complete(make_req(), p, CallContext(no_cache=True), tmp_db))
    r = asyncio.run(gateway.complete(make_req(), p, CallContext(no_cache=True), tmp_db))
    assert prov.calls == 2 and not r.cache_hit
    r3 = asyncio.run(gateway.complete(make_req(), p, CallContext(), tmp_db))
    assert r3.cache_hit and prov.calls == 2


def test_cache_key_depends_on_model_and_profile_defaults():
    req = make_req()
    a = gateway.cache_key(gateway.effective_request(req, fake_profile()), fake_profile())
    b = gateway.cache_key(gateway.effective_request(req, fake_profile(model="fake2")), fake_profile(model="fake2"))
    c = gateway.cache_key(gateway.effective_request(req, fake_profile(max_output_tokens=100)), fake_profile())
    assert len({a, b, c}) == 3
    assert a == gateway.cache_key(gateway.effective_request(make_req(), fake_profile()), fake_profile())


# ---------- повторы транспорта ----------

def test_transport_retry_5xx_then_ok(tmp_db, monkeypatch, no_sleep):
    prov = ScriptedProvider([gateway.TransportError("boom", code=503), gateway.TransportError("timeout"), _ok()])
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    r = asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext(), tmp_db))
    assert r.status == "ok" and r.parsed["verdict"] == "Да" and prov.calls == 3
    statuses = [row["status"] for row in tmp_db.execute("SELECT status FROM llm_calls ORDER BY id")]
    assert statuses == ["http_error", "http_error", "ok"]


def test_transport_gives_up_after_max_attempts(tmp_db, monkeypatch, no_sleep):
    prov = ScriptedProvider([gateway.TransportError("boom", code=500)] * 10)
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    r = asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext(), tmp_db))
    assert r.status == "http_error" and prov.calls == gateway.MAX_ATTEMPTS
    assert tmp_db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == gateway.MAX_ATTEMPTS
    assert tmp_db.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 0


def test_non_retryable_http_error_returns_at_once(monkeypatch, no_sleep):
    prov = ScriptedProvider([gateway.TransportError("bad request", code=400)])
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    r = asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext()))
    assert r.status == "http_error" and prov.calls == 1


def test_429_pauses_profile(monkeypatch, no_sleep):
    from cqa.core.ratelimit import limiter_for

    prov = ScriptedProvider([gateway.TransportError("rate", code=429, retry_after=0.2), _ok()])
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    p = fake_profile(name="fake-429")
    import time
    t0 = time.monotonic()
    r = asyncio.run(gateway.complete(make_req(), p, CallContext()))
    assert r.status == "ok" and prov.calls == 2
    assert time.monotonic() - t0 >= 0.18                     # ждали паузу профиля из Retry-After
    assert limiter_for(p).active == 0


def test_truncated_gemini_retries_with_double_tokens(tmp_db, monkeypatch):
    prov = ScriptedProvider([
        LLMResponse(text='{"evidence": [', status="truncated", finish_reason="MAX_TOKENS"),
        _ok(),
    ])
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    p = Profile(name="g-test", provider="gemini", model="gemini-x", max_output_tokens=1000, price={})
    r = asyncio.run(gateway.complete(make_req(), p, CallContext(), tmp_db))
    assert r.status == "ok" and prov.calls == 2
    assert [q.max_output_tokens for q in prov.requests] == [1000, 2000]
    assert r.cost_usd is None                                 # цена неизвестна
    rows = tmp_db.execute("SELECT status FROM llm_calls ORDER BY id").fetchall()
    assert [x["status"] for x in rows] == ["truncated", "ok"]
    # кэш — по исходному запросу
    r2 = asyncio.run(gateway.complete(make_req(), p, CallContext(), tmp_db))
    assert r2.cache_hit


def test_truncated_non_gemini_is_returned(monkeypatch):
    prov = ScriptedProvider([LLMResponse(text="{", status="truncated", finish_reason="length")])
    monkeypatch.setattr(gateway, "provider_for", lambda p: prov)
    r = asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext()))
    assert r.status == "truncated" and prov.calls == 1


# ---------- fake ----------

def test_fake_bad_json_marker_gives_invalid_json(tmp_db):
    r = asyncio.run(gateway.complete(make_req("[[FAKE:bad_json]]"), fake_profile(), CallContext(), tmp_db))
    assert r.status == "invalid_json" and r.parsed is None
    assert tmp_db.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 0


def test_fake_is_deterministic_and_quotes_transcript():
    p = fake_profile()
    r1 = asyncio.run(gateway.complete(make_req(), p, CallContext()))
    r2 = asyncio.run(gateway.complete(make_req(), p, CallContext()))
    assert r1.text == r2.text
    assert list(r1.parsed) == ["evidence", "reasoning", "verdict"]
    ev = r1.parsed["evidence"][0]
    assert ev["id"] == "m2" and ev["quote"] in TRANSCRIPT       # роль ОПЕРАТОР, цитата дословная
    bad = asyncio.run(gateway.complete(make_req("[[FAKE:bad_quote]]"), p, CallContext()))
    assert bad.parsed["evidence"][0]["quote"] == BAD_QUOTE


def test_fake_without_schema_returns_text():
    req = LLMRequest(messages=[ChatMessage(role="user", content="привет")])
    r = asyncio.run(gateway.complete(req, fake_profile(), CallContext()))
    assert r.status == "ok" and r.text == "OK"


def test_request_json_in_log_has_messages(tmp_db):
    asyncio.run(gateway.complete(make_req(), fake_profile(), CallContext(purpose="ping"), tmp_db))
    row = tmp_db.execute("SELECT request_json, purpose FROM llm_calls").fetchone()
    data = json.loads(row["request_json"])
    assert data["messages"][1]["content"].startswith("<transcript>") and row["purpose"] == "ping"


# ---------- сборка запросов провайдеров (без сети) ----------

def test_gemini_request_building():
    from cqa.llm.gemini_native import build_config, build_contents

    req = LLMRequest(messages=[ChatMessage(role="system", content="S"), ChatMessage(role="user", content="A"),
                               ChatMessage(role="user", content="B"), ChatMessage(role="assistant", content="{}"),
                               ChatMessage(role="user", content="R")],
                     json_schema=SCHEMA, property_ordering=["evidence", "reasoning", "verdict"],
                     thinking_level="low", max_output_tokens=100)
    system, contents = build_contents(req)
    assert system == "S"
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert [p.text for p in contents[0].parts] == ["A", "B"]                # общий префикс — части одного Content
    cfg = build_config(req, Profile(name="g", provider="gemini", model="m"), system)
    assert cfg.system_instruction == "S" and cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema["propertyOrdering"] == ["evidence", "reasoning", "verdict"]
    assert cfg.response_json_schema["properties"]["evidence"]["items"]["propertyOrdering"] == ["id", "quote"]
    assert "propertyOrdering" not in SCHEMA                                   # исходная схема не тронута
    assert cfg.thinking_config.thinking_level is not None and cfg.max_output_tokens == 100
    cfg2 = build_config(req.model_copy(update={"thinking_level": None}), Profile(name="g", provider="gemini", model="m"), None)
    assert cfg2.thinking_config is None and cfg2.system_instruction is None


def test_openai_compat_request_building():
    from cqa.llm.openai_compat import build_kwargs

    req = make_req()
    p = Profile(name="l", provider="openai_compat", model="gemma4", json_mode="json_schema")
    kw = build_kwargs(req, p)
    assert kw["response_format"]["type"] == "json_schema" and kw["response_format"]["json_schema"]["strict"] is True
    assert kw["messages"][0] == {"role": "system", "content": "Ты судья."}
    kw = build_kwargs(req, p.model_copy(update={"json_mode": "json_object"}))
    assert kw["response_format"] == {"type": "json_object"}
    assert '"verdict"' in kw["messages"][0]["content"]                       # схема текстом в system
