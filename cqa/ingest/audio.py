"""Аудио звонков: `audio_url` → HTML с `<source src="…mp3…">` → mp3 в settings.audio_dir.

Ссылки (страница и mp3) содержат `sig=`: не печатаются, не логируются, не попадают в исключения.
Ссылка на mp3 живёт около часа, поэтому mp3 скачивается сразу при загрузке набора.
Если ссылка истекла (401/403/410), группа перезапрашивается из API и ссылки в conversation_secrets обновляются.
"""
from __future__ import annotations

import html
import logging
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import httpx

from cqa.ingest import store
from cqa.settings import get_settings

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(120.0, connect=15.0)
_SOURCE = re.compile(r"<source\b[^>]*?\bsrc\s*=\s*([\"'])(?P<src>.+?)\1", re.I | re.S)
_AUDIO_SRC = re.compile(r"<audio\b[^>]*?\bsrc\s*=\s*([\"'])(?P<src>.+?)\1", re.I | re.S)
_EXPIRED = (401, 403, 410)


class AudioError(RuntimeError):
    """Ошибка получения аудио. Сообщение без ссылок."""


class LinkExpired(AudioError):
    pass


@dataclass
class AudioReport:
    group_id: str
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)       # уже скачаны
    failed: dict[str, str] = field(default_factory=dict)   # uid → причина (без ссылок)
    refreshed: bool = False                                 # ссылки обновлялись из API

    def summary(self) -> str:
        s = (f"аудио {self.group_id}: скачано {len(self.downloaded)}, уже было {len(self.skipped)}, "
             f"ошибок {len(self.failed)}")
        return s + (" (ссылки обновлены из API)" if self.refreshed else "")


def audio_file(uid: str) -> Path:
    """settings.audio_dir/{uid}.mp3; символы, недопустимые в именах файлов (`:` в uid), заменены на `_`."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", uid)
    return Path(get_settings().audio_dir) / f"{safe}.mp3"


def extract_source_url(page_html: str, base_url: str) -> str | None:
    """Ссылка на mp3 из HTML-страницы: `<source src="…">` (амперсанды экранированы как `&amp;`)."""
    m = _SOURCE.search(page_html) or _AUDIO_SRC.search(page_html)
    if not m:
        return None
    return urljoin(base_url, html.unescape(m.group("src").strip()))


def _looks_like_mp3(head: bytes) -> bool:
    return head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)


def _check_status(code: int, what: str) -> None:
    if code in _EXPIRED:
        raise LinkExpired(f"{what}: HTTP {code} (ссылка истекла)")
    if code != 200:
        raise AudioError(f"{what}: HTTP {code}")


def _save_response(r: httpx.Response, dest: Path, what: str) -> None:
    tmp = dest.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_bytes(64 * 1024):
            f.write(chunk)
    with open(tmp, "rb") as f:
        head = f.read(4)
    if not _looks_like_mp3(head):
        tmp.unlink(missing_ok=True)
        raise AudioError(f"{what}: ответ не похож на mp3")
    tmp.replace(dest)


def _stream_to(client: httpx.Client, url: str, dest: Path, what: str) -> None:
    with client.stream("GET", url) as r:
        _check_status(r.status_code, what)
        _save_response(r, dest, what)


def download_one(client: httpx.Client, audio_url: str, dest: Path) -> None:
    """audio_url (HTML-страница) → mp3 в dest. Исключения без ссылок."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with client.stream("GET", audio_url) as r:
            _check_status(r.status_code, "страница аудио")
            ctype = r.headers.get("content-type", "").lower()
            if ctype.startswith("audio/") or "mpeg" in ctype:  # ссылка сразу на mp3
                _save_response(r, dest, "mp3")
                return
            r.read()
            page, base = r.text, str(r.url)
        mp3_url = extract_source_url(page, base)
        if not mp3_url:
            raise AudioError("на странице аудио нет <source src>")
        _stream_to(client, mp3_url, dest, "mp3")
    except httpx.HTTPError as e:
        raise AudioError(f"сеть: {type(e).__name__}") from None


def refresh_audio_urls(con: sqlite3.Connection, group_id: str) -> int:
    """Перезапросить группу из API и обновить audio_url в conversation_secrets. → сколько ссылок обновлено."""
    from cqa.ingest.fetch import fetch_group_raw
    from cqa.ingest.normalize import make_uid
    from cqa.ingest.parse import ParseError, parse_payload, parse_record

    payload, raw_path = fetch_group_raw(group_id)
    known = set(store.list_uids(con, group_id))
    n = 0
    with store._atomic(con):
        for rec in parse_payload(payload).records:
            try:
                raw = parse_record(rec)
            except ParseError:
                continue
            uid = make_uid(group_id, raw.conversation_id)
            if uid in known and raw.audio_url:
                store._set_audio_url(con, uid, raw.audio_url)
                n += 1
        con.execute("UPDATE datasets SET raw_path=? WHERE group_id=?", (str(raw_path), group_id))
    return n


def download_audio(con: sqlite3.Connection, group_id: str, *, force: bool = False,
                   refresh_expired: bool = True) -> AudioReport:
    """Скачать mp3 всех звонков группы в settings.audio_dir и выставить audio_status."""
    report = AudioReport(group_id=group_id)
    rows = store.audio_rows(con, group_id)
    todo: list[str] = []
    for r in rows:
        if not force and r["audio_status"] == "ready" and r["audio_path"] and Path(r["audio_path"]).is_file():
            report.skipped.append(r["uid"])
        else:
            todo.append(r["uid"])
    if not todo:
        return report

    expired: list[str] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        def attempt(uid: str) -> bool:
            url = store._get_audio_url(con, uid)
            dest = audio_file(uid)
            store.set_audio(con, uid, "pending", None)
            try:
                download_one(client, url, dest)
            except LinkExpired as e:
                store.set_audio(con, uid, "failed", None)
                report.failed[uid] = str(e)
                return False
            except (AudioError, OSError) as e:
                store.set_audio(con, uid, "failed", None)
                report.failed[uid] = str(e) if isinstance(e, AudioError) else f"файл: {type(e).__name__}"
                return True
            store.set_audio(con, uid, "ready", dest)
            report.failed.pop(uid, None)
            report.downloaded.append(uid)
            return True

        for uid in todo:
            if not attempt(uid):
                expired.append(uid)

        if expired and refresh_expired and get_settings().cqa_api_url:
            try:
                refresh_audio_urls(con, group_id)
                report.refreshed = True
            except Exception as e:  # noqa: BLE001 — отчёт без ссылок
                from cqa.ingest.fetch import redact
                log.warning("не удалось обновить ссылки группы %s: %s", group_id, redact(str(e)))
            else:
                for uid in expired:
                    attempt(uid)
    return report


# ---------------------------------------------------------------- длительности

@dataclass
class ProbeRow:
    uid: str
    mp3_sec: float | None          # ffprobe
    recorded_sec: float | None     # по транскрипту: audio_sec последней записанной реплики
    span_sec: float | None         # от начала записи до последней реплики, паузы не вычтены
    paused_sec: float              # суммарная длительность пауз записи
    pauses: int

    @property
    def diff_sec(self) -> float | None:
        if self.mp3_sec is None or self.recorded_sec is None:
            return None
        return round(self.mp3_sec - self.recorded_sec, 2)


def ffprobe_duration(path: Path) -> float | None:
    exe = shutil.which("ffprobe")
    if not exe or not path.is_file():
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        return round(float(out), 3)
    except (subprocess.SubprocessError, ValueError):
        return None


def transcript_durations(conv) -> tuple[float | None, float | None, float, int]:
    """→ (recorded_sec, span_sec, paused_sec, число пауз) по разметке нормализатора."""
    from cqa.ingest.normalize import REC_PAUSED, REC_RESUMED, REC_STARTED

    msgs = conv.messages
    rec = [m.audio_sec for m in msgs if m.audio_sec is not None]
    recorded = max(rec) if rec else None
    start = next((m.offset_sec for m in msgs if m.speaker == "system" and m.event_type == REC_STARTED), None)
    if start is None:
        start = next((m.offset_sec for m in msgs if m.offset_sec is not None), None)
    last = max((m.offset_sec for m in msgs if m.offset_sec is not None), default=None)
    span = round(last - start, 3) if start is not None and last is not None else None
    paused, n, pause_from = 0.0, 0, None
    for m in msgs:
        if m.speaker != "system" or m.offset_sec is None:
            continue
        if m.event_type == REC_PAUSED and pause_from is None:
            pause_from = m.offset_sec
        elif m.event_type in (REC_RESUMED, REC_STARTED) and pause_from is not None:
            paused += m.offset_sec - pause_from
            n += 1
            pause_from = None
    return recorded, span, round(paused, 3), n


def probe(con: sqlite3.Connection, group_id: str, echo: Callable[[str], None] = print) -> list[ProbeRow]:
    """Для каждого звонка группы: длительность mp3 (ffprobe) против записанного участка по транскрипту."""
    uids = [r[0] for r in con.execute(
        """SELECT uid FROM conversations WHERE group_id=?
             AND (audio_status != 'none' OR lower(channel) LIKE '%call%' OR lower(channel) LIKE '%voice%')
           ORDER BY external_id""", (group_id,))]
    rows: list[ProbeRow] = []
    if not shutil.which("ffprobe"):
        echo("ffprobe не найден: длительность mp3 не измерить")
    echo(f"{'uid':<22} {'mp3,с':>8} {'запись,с':>9} {'mp3−запись':>11} {'пауз':>5} {'паузы,с':>8} {'без вычета,с':>13}")
    for uid in uids:
        conv = store.get_conversation(con, uid)
        recorded, span, paused, n = transcript_durations(conv)
        path = store.get_audio_path(con, uid)
        row = ProbeRow(uid=uid, mp3_sec=ffprobe_duration(path) if path else None,
                       recorded_sec=recorded, span_sec=span, paused_sec=paused, pauses=n)
        rows.append(row)

        def f(v):
            return "—" if v is None else f"{v:.2f}"
        echo(f"{uid:<22} {f(row.mp3_sec):>8} {f(row.recorded_sec):>9} {f(row.diff_sec):>11} "
             f"{row.pauses:>5} {row.paused_sec:>8.2f} {f(row.span_sec):>13}")
    return rows

