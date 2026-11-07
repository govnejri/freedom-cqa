"""Доступ LLM-слоя к БД: кэш ответов (llm_cache) и журнал вызовов (llm_calls)."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from cqa.db.conn import now_iso
from cqa.llm.base import CallContext, LLMResponse, Profile, Usage
from cqa.llm.redact import redact

# Соединение создаётся с check_same_thread=False: пишущие вызовы сериализуем сами.
_LOCK = threading.Lock()


def cache_get(con: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    with _LOCK:
        row = con.execute("SELECT * FROM llm_cache WHERE key=?", (key,)).fetchone()
        if row is not None:
            con.execute("UPDATE llm_cache SET hits = hits + 1 WHERE key=?", (key,))
    return row


def cache_put(con: sqlite3.Connection, key: str, profile: Profile, resp: LLMResponse) -> None:
    with _LOCK:
        con.execute(
            "INSERT OR REPLACE INTO llm_cache(key, profile, model, response_text, parsed_json, usage_json, cost_usd, created_at, hits)"
            " VALUES (?,?,?,?,?,?,?,?,0)",
            (
                key, profile.name, resp.model or profile.model, resp.text,
                json.dumps(resp.parsed, ensure_ascii=False) if resp.parsed is not None else None,
                resp.usage.model_dump_json(), resp.cost_usd, now_iso(),
            ),
        )


def response_from_cache(row: sqlite3.Row, profile: Profile) -> LLMResponse:
    usage = Usage.model_validate_json(row["usage_json"]) if row["usage_json"] else Usage()
    return LLMResponse(
        text=row["response_text"],
        parsed=json.loads(row["parsed_json"]) if row["parsed_json"] else None,
        usage=usage,
        finish_reason="cache",
        status="ok",
        model=row["model"],
        provider=profile.provider,
        cost_usd=row["cost_usd"],     # исходная стоимость; вызывающий обнуляет её в ответе
        cache_hit=True,
    )


def request_json(payload: dict[str, Any]) -> str:
    """Запрос для журнала: без секретов."""
    return redact(json.dumps(payload, ensure_ascii=False))


def log_call(
    con: sqlite3.Connection,
    *,
    ctx: CallContext,
    profile: Profile,
    cache_key: str | None,
    request: str,
    resp: LLMResponse,
    status: str | None = None,
) -> int:
    """Одна строка журнала на попытку. Для попадания в кэш resp.cost_usd — исходная стоимость, cache_hit=1."""
    with _LOCK:
        cur = con.execute(
            "INSERT INTO llm_calls(ts, purpose, run_id, evaluation_id, criterion_result_id, uid, criterion_id,"
            " profile, provider, model, cache_key, cache_hit, request_json, response_text, status, error, finish_reason,"
            " prompt_tokens, cached_tokens, completion_tokens, reasoning_tokens, cost_usd, latency_ms, attempt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now_iso(), ctx.purpose, ctx.run_id, ctx.evaluation_id, ctx.criterion_result_id, ctx.uid, ctx.criterion_id,
                profile.name, profile.provider, resp.model or profile.model, cache_key, int(resp.cache_hit),
                request, redact(resp.text or ""), status or resp.status,
                redact(resp.error) if resp.error else None, resp.finish_reason,
                resp.usage.prompt_tokens, resp.usage.cached_tokens, resp.usage.completion_tokens, resp.usage.reasoning_tokens,
                resp.cost_usd, resp.latency_ms, ctx.attempt,
            ),
        )
        return int(cur.lastrowid)


def mark_call(con: sqlite3.Connection, call_id: int, status: str, error: str | None = None) -> None:
    """Поменять статус попытки задним числом (например, validation_failed после проверки судьёй)."""
    with _LOCK:
        con.execute("UPDATE llm_calls SET status=?, error=COALESCE(?, error) WHERE id=?",
                    (status, redact(error) if error else None, call_id))


def recent_calls(con: sqlite3.Connection, limit: int = 20, uid: str | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT id, ts, purpose, uid, criterion_id, profile, model, status, cache_hit, attempt, prompt_tokens,"
           " cached_tokens, completion_tokens, reasoning_tokens, cost_usd, latency_ms, error FROM llm_calls")
    args: tuple = ()
    if uid:
        sql += " WHERE uid=?"
        args = (uid,)
    sql += " ORDER BY id DESC LIMIT ?"
    return con.execute(sql, args + (limit,)).fetchall()


def get_call(con: sqlite3.Connection, call_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM llm_calls WHERE id=?", (call_id,)).fetchone()


def max_call_id(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COALESCE(MAX(id), 0) FROM llm_calls").fetchone()[0])


def calls_summary(con: sqlite3.Connection, after_id: int, uid: str | None = None) -> dict[str, Any]:
    """Сводка по вызовам после after_id: число, из кэша, реальная стоимость (без попаданий в кэш)."""
    sql = ("SELECT COUNT(*) n, SUM(cache_hit) hits,"
           " SUM(CASE WHEN cache_hit=0 THEN cost_usd END) cost,"
           " SUM(CASE WHEN cache_hit=0 AND cost_usd IS NULL THEN 1 ELSE 0 END) unknown_cost,"
           " SUM(prompt_tokens) p, SUM(cached_tokens) c, SUM(completion_tokens) o, SUM(reasoning_tokens) r"
           " FROM llm_calls WHERE id > ?")
    args: tuple = (after_id,)
    if uid:
        sql += " AND uid=?"
        args += (uid,)
    row = con.execute(sql, args).fetchone()
    return {k: row[k] for k in row.keys()}
