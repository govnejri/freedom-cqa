"""CLI LLM-слоя: `cqa llm-ping`, `cqa log`. Ключи и подписи ссылок не печатаются."""
from __future__ import annotations

import json

import typer

PING_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["id", "quote"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["Да", "Нет"]},
    },
    "required": ["evidence", "reasoning", "verdict"],
    "additionalProperties": False,
}
PING_ORDER = ["evidence", "reasoning", "verdict"]
PING_SYSTEM = "Ты проверяешь работу оператора поддержки. Ответь JSON-объектом строго по схеме."
PING_USER = (
    "<transcript>\n"
    "m1 | 00:00 | КЛИЕНТ | Здравствуйте, где посмотреть мой тариф?\n"
    "m2 | 00:05 | ОПЕРАТОР | Здравствуйте! Меня зовут Алина. Тариф указан в разделе «Профиль».\n"
    "</transcript>\n"
    "Поздоровался ли оператор и представился ли в первом ответе? Сначала приведи цитату (evidence), "
    "потом краткий разбор, потом вердикт."
)


def _con():
    from cqa.db.conn import connect, migrate

    con = connect()
    migrate(con)
    return con


def _profile(name: str | None):
    from cqa.settings import get_profile, role_profile

    try:
        return get_profile(name) if name else role_profile("judge")
    except KeyError:
        from cqa.settings import load_models_config
        names = ", ".join(load_models_config()["profiles"])
        raise typer.BadParameter(f"нет профиля {name!r}; есть: {names}") from None


def _cost(v: float | None) -> str:
    return "неизвестна" if v is None else f"${v:.6f}"


def register(app: typer.Typer) -> None:
    @app.command("llm-ping")
    def llm_ping(
        profile: str = typer.Option(None, "--profile", "-p", help="профиль из config/models.yaml (по умолчанию роль judge)"),
        no_log: bool = typer.Option(False, "--no-log", help="не писать в журнал llm_calls"),
    ) -> None:
        """Один вызов со схемой: порядок ключей ответа, usage, задержка, стоимость."""
        from cqa.llm.base import CallContext, ChatMessage, LLMRequest
        from cqa.llm.gateway import complete_sync

        p = _profile(profile)
        req = LLMRequest(
            messages=[ChatMessage(role="system", content=PING_SYSTEM), ChatMessage(role="user", content=PING_USER)],
            json_schema=PING_SCHEMA,
            property_ordering=PING_ORDER,
        )
        con = None if no_log else _con()
        r = complete_sync(req, p, CallContext(purpose="ping", no_cache=True), con)
        typer.echo(f"профиль: {p.name} ({p.provider}, {p.model}, thinking={p.thinking_level})")
        typer.echo(f"статус: {r.status}; finish_reason: {r.finish_reason}; задержка: {r.latency_ms} мс")
        if r.error:
            typer.echo(f"ошибка: {r.error}")
        if r.parsed is not None:
            keys = list(r.parsed.keys())
            typer.echo(f"порядок ключей: {' → '.join(keys)}" + ("" if keys == PING_ORDER else "  (НЕ совпадает со схемой)"))
            typer.echo("ответ: " + json.dumps(r.parsed, ensure_ascii=False))
        elif r.text:
            typer.echo("текст: " + r.text[:500])
        u = r.usage
        typer.echo(f"usage: prompt={u.prompt_tokens} cached={u.cached_tokens} completion={u.completion_tokens} "
                   f"reasoning={u.reasoning_tokens}")
        typer.echo(f"стоимость: {_cost(r.cost_usd)}")
        if r.status != "ok":
            raise typer.Exit(1)

    @app.command("log")
    def log_cmd(
        last: bool = typer.Option(False, "--last", help="последние вызовы (по умолчанию)"),
        n: int = typer.Option(20, "-n", help="сколько строк"),
        uid: str = typer.Option(None, "--uid", help="только вызовы по диалогу"),
        call_id: int = typer.Option(None, "--id", help="показать вызов целиком: запрос и ответ"),
    ) -> None:
        """Журнал вызовов моделей (llm_calls)."""
        from cqa.llm import store

        con = _con()
        if call_id is not None:
            row = store.get_call(con, call_id)
            if row is None:
                raise typer.BadParameter(f"нет вызова {call_id}")
            meta = {k: row[k] for k in row.keys() if k not in ("request_json", "response_text")}
            typer.echo(json.dumps(meta, ensure_ascii=False, indent=2))
            req = json.loads(row["request_json"])
            for m in req.get("messages", []):
                typer.echo(f"\n===== {m['role']}\n{m['content']}")
            typer.echo(f"\n===== ответ\n{row['response_text']}")
            return
        rows = store.recent_calls(con, n, uid)
        if not rows:
            typer.echo("журнал пуст")
            return
        typer.echo("id    время                      цель      диалог / критерий         профиль       статус             кэш  поп  токены (in/cached/out/think)  стоимость   мс")
        for r in reversed(rows):
            tokens = f"{r['prompt_tokens']}/{r['cached_tokens']}/{r['completion_tokens']}/{r['reasoning_tokens']}"
            what = f"{r['uid'] or '-'} / {r['criterion_id'] or '-'}"
            typer.echo(f"{r['id']:<5} {r['ts']:<26} {r['purpose']:<9} {what[:25]:<25} {r['profile'][:13]:<13} "
                       f"{r['status']:<18} {'да' if r['cache_hit'] else '-':<4} {r['attempt']:<4} {tokens:<29} "
                       f"{_cost(r['cost_usd']):<11} {r['latency_ms']}")
            if r["error"]:
                typer.echo(f"      ! {r['error'][:200]}")
