"""Адаптер OpenAI-совместимого API (Ollama и др.): обрезанный контекст и размер контекста модели. Без сети."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from cqa.llm import openai_compat
from cqa.llm.base import ChatMessage, LLMRequest, Profile, Usage
from cqa.llm.openai_compat import OpenAICompatProvider, context_problem, fetch_num_ctx

DIALOG = "m1 | 00:00 | КЛИЕНТ | Здравствуйте, где посмотреть мой тариф и комиссии по счёту?\n" * 200   # ~16 тыс. символов
MSGS = [{"role": "system", "content": "Ты судья."}, {"role": "user", "content": DIALOG},
        {"role": "user", "content": "Критерий: приветствие."}]
ANSWER = json.dumps({"evidence": [], "reasoning": "р", "verdict": "Да"}, ensure_ascii=False)


def test_context_problem_dropped_messages():
    chars = sum(len(m["content"]) for m in MSGS)
    # Ollama выбросила диалог: токенов втрое меньше, чем должно быть (замер: 1927 вместо 6233)
    assert "обрезан сервером" in context_problem(MSGS, Usage(prompt_tokens=chars // 8, completion_tokens=200), None)
    # полный промпт: ~2.6 символа на токен
    assert context_problem(MSGS, Usage(prompt_tokens=int(chars / 2.6), completion_tokens=200), None) is None
    # короткий промпт не проверяем по соотношению; нет usage — тоже
    short = [{"role": "user", "content": "x" * 1000}]
    assert context_problem(short, Usage(prompt_tokens=10), None) is None
    assert context_problem(MSGS, Usage(), None) is None


def test_context_problem_num_ctx_overflow():
    chars = sum(len(m["content"]) for m in MSGS)
    u = Usage(prompt_tokens=int(chars / 2.6), completion_tokens=300, reasoning_tokens=100)
    total = u.prompt_tokens + 400
    assert "переполнен" in context_problem(MSGS, u, total)
    assert "num_ctx" in context_problem(MSGS, u, total - 1)
    assert context_problem(MSGS, u, total + 1) is None


def _ps_transport(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


def test_fetch_num_ctx_ollama_ps():
    ps = {"models": [{"name": "cqa-gemma4:latest", "model": "cqa-gemma4:latest", "context_length": 16384}]}
    run = asyncio.run
    assert run(fetch_num_ctx("http://h:11434/v1", "cqa-gemma4", _ps_transport(ps))) == (True, 16384)
    assert run(fetch_num_ctx("http://h:11434/v1/", "cqa-gemma4:latest", _ps_transport(ps))) == (True, 16384)
    assert run(fetch_num_ctx("http://h:11434/v1", "other", _ps_transport(ps))) == (False, None)   # не загружена
    assert run(fetch_num_ctx("http://h:8080/v1", "m", _ps_transport({}, 404))) == (True, None)   # не Ollama
    old = {"models": [{"name": "cqa-gemma4:latest"}]}                                            # старая Ollama
    assert run(fetch_num_ctx("http://h:11434/v1", "cqa-gemma4", _ps_transport(old))) == (True, None)


def _chat_transport(prompt_tokens: int, completion_tokens: int = 50, finish: str = "stop"):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": ANSWER}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}})
    return httpx.MockTransport(handler)


@pytest.fixture
def local(monkeypatch):
    """Профиль local с подменённым HTTP-клиентом и размером контекста; возвращает функцию вызова."""
    from openai import AsyncOpenAI

    monkeypatch.setenv("CQA_TEST_LOCAL_URL", "http://ollama.test/v1")
    monkeypatch.setattr(openai_compat, "_NUM_CTX", {})
    profile = Profile(name="local", provider="openai_compat", model="cqa-gemma4", base_url_env="CQA_TEST_LOCAL_URL",
                      json_mode="json_schema", max_output_tokens=2048)
    req = LLMRequest(messages=[ChatMessage(**m) for m in MSGS], json_schema={"type": "object"}, max_output_tokens=2048)
    asked: list[str] = []

    def call(transport, num_ctx: int | None = 16384):
        async def fake_fetch(base_url, model, transport=None):
            asked.append(base_url)
            return True, num_ctx
        monkeypatch.setattr(openai_compat, "fetch_num_ctx", fake_fetch)
        monkeypatch.setattr(openai_compat, "_client", lambda p: AsyncOpenAI(
            base_url="http://ollama.test/v1", api_key="local", max_retries=0,
            http_client=httpx.AsyncClient(transport=transport)))
        return asyncio.run(OpenAICompatProvider().complete(req, profile))

    call.asked = asked
    return call


def test_complete_ok_full_prompt(local):
    chars = sum(len(m["content"]) for m in MSGS)
    r = local(_chat_transport(int(chars / 2.6)))
    assert r.status == "ok" and r.error is None and r.text == ANSWER
    assert local.asked == ["http://ollama.test/v1"]


def test_complete_marks_dropped_dialog_truncated(local):
    r = local(_chat_transport(400))                       # сервер выбросил сообщение с диалогом
    assert r.status == "truncated" and "обрезан сервером" in r.error


def test_complete_marks_ctx_overflow_truncated(local):
    chars = sum(len(m["content"]) for m in MSGS)
    pt = int(chars / 2.6)
    r = local(_chat_transport(pt, completion_tokens=600), num_ctx=pt + 500)
    assert r.status == "truncated" and "переполнен" in r.error


def test_complete_num_ctx_cached_and_length_kept(local):
    chars = sum(len(m["content"]) for m in MSGS)
    local(_chat_transport(int(chars / 2.6)))
    local(_chat_transport(int(chars / 2.6)))
    assert len(local.asked) == 1                          # /api/ps спрашиваем один раз на (адрес, модель)
    r = local(_chat_transport(int(chars / 2.6), finish="length"))
    assert r.status == "truncated" and "max_tokens" in r.error
