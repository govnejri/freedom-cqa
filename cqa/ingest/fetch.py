"""Загрузка группы из API организаторов. Ссылка API содержит `sig=`: в логи, исключения и вывод не попадает."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from cqa.settings import get_settings

TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_SIG = re.compile(r"(sig=)[^&\s\"'<>]+", re.I)
_URL = re.compile(r"https?://\S+", re.I)


class FetchError(RuntimeError):
    """Ошибка загрузки. Сообщение не содержит ссылок и подписей."""


def redact(text: str) -> str:
    """Убрать подписи и ссылки из текста для логов и исключений."""
    return _URL.sub("<url>", _SIG.sub(r"\1***", text or ""))


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "group"


def group_url(group_id: str) -> str:
    base = get_settings().cqa_api_url
    if not base:
        raise FetchError("CQA_API_URL не задан (.env)")
    return f"{base}&group_id={group_id}"


def fetch_group_raw(group_id: str, client: httpx.Client | None = None) -> tuple[dict, Path]:
    """GET группы → (ответ, путь к сохранённому сырому ответу в settings.raw_dir)."""
    url = group_url(group_id)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        try:
            resp = client.get(url)
        except httpx.HTTPError as e:
            # str(e) у httpx может содержать URL — наружу только тип ошибки
            raise FetchError(f"API недоступен: {type(e).__name__}") from None
        if resp.status_code != 200:
            raise FetchError(f"API вернул HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            raise FetchError("API вернул не JSON") from None
    finally:
        if own:
            client.close()

    if isinstance(payload, dict) and payload.get("isSuccess") is False:
        raise FetchError(f"API: isSuccess=false: {redact(str(payload.get('error') or ''))[:200]}")

    raw_dir = Path(get_settings().raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"{_safe_name(group_id)}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload, path


def fetch_group(group_id: str) -> dict:
    """GET `{settings.cqa_api_url}&group_id={group_id}`; сырой ответ сохраняется в settings.raw_dir."""
    payload, _ = fetch_group_raw(group_id)
    return payload
