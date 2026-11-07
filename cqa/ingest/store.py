"""Доступ к БД модуля ingest: datasets, conversations, conversation_secrets, messages, messages_fts.

Публичные функции — контракт для других модулей. `audio_url` (с `sig=`) пишется только в conversation_secrets
и читается только `cqa/ingest/audio.py` через `_get_audio_url`.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cqa.db.conn import now_iso
from cqa.models import Conversation, Facts, Message

AUDIO_STATUSES = ("none", "pending", "ready", "failed")


@contextmanager
def _atomic(con: sqlite3.Connection):
    """Транзакция, если вызывающий её ещё не открыл (вложенные вызовы идут в его транзакции)."""
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------- datasets

def upsert_dataset(con: sqlite3.Connection, group_id: str, source: str, total_records: int,
                   raw_path: str | Path | None = None) -> None:
    con.execute(
        """INSERT INTO datasets(group_id, source, fetched_at, total_records, raw_path)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(group_id) DO UPDATE SET source=excluded.source, fetched_at=excluded.fetched_at,
             total_records=excluded.total_records, raw_path=COALESCE(excluded.raw_path, datasets.raw_path)""",
        (group_id, source, now_iso(), total_records, str(raw_path) if raw_path else None),
    )


# ---------------------------------------------------------------- conversations

def save_conversation(con: sqlite3.Connection, conv: Conversation, audio_url: str | None) -> None:
    """Upsert диалога, перезапись messages и messages_fts, audio_url — только в conversation_secrets.

    Если группы нет в datasets, она создаётся с source='manual'. Если `conv.facts` пуст, факты считаются здесь.
    """
    from cqa.ingest.normalize import NORMALIZER_VERSION

    facts = conv.facts
    if facts is None:
        from cqa.ingest.facts import build_facts
        facts = build_facts(conv)
    ts = now_iso()
    with _atomic(con):
        con.execute(
            "INSERT OR IGNORE INTO datasets(group_id, source, fetched_at, total_records) VALUES (?, 'manual', ?, 0)",
            (conv.group_id, ts),
        )
        con.execute(
            """INSERT INTO conversations(uid, group_id, external_id, channel, language, content_hash,
                 normalizer_version, facts_json, operator_name, audio_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET
                 channel=excluded.channel, language=excluded.language, content_hash=excluded.content_hash,
                 normalizer_version=excluded.normalizer_version, facts_json=excluded.facts_json,
                 operator_name=excluded.operator_name,
                 audio_status=CASE
                   WHEN conversations.audio_status IN ('none', 'failed') AND excluded.audio_status='pending'
                     THEN 'pending'
                   ELSE conversations.audio_status END""",
            (conv.uid, conv.group_id, conv.external_id, conv.channel, conv.language, conv.content_hash,
             NORMALIZER_VERSION, facts.model_dump_json(), conv.operator_name,
             "pending" if audio_url else "none", ts),
        )
        con.execute("DELETE FROM messages WHERE uid=?", (conv.uid,))
        con.execute("DELETE FROM messages_fts WHERE uid=?", (conv.uid,))
        con.executemany(
            """INSERT INTO messages(uid, idx, mid, speaker, text, ts_raw, offset_sec, audio_sec, segment,
                 recorded, after_end, event_type, dup_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(conv.uid, m.idx, m.mid, m.speaker, m.text, m.ts_raw, m.offset_sec, m.audio_sec, m.segment,
              int(m.recorded), int(m.after_end), m.event_type, m.dup_of) for m in conv.messages],
        )
        con.executemany(
            "INSERT INTO messages_fts(uid, mid, text) VALUES (?, ?, ?)",
            [(conv.uid, m.mid, m.text) for m in conv.messages],
        )
        con.execute(
            """INSERT INTO conversation_secrets(uid, audio_url, fetched_at) VALUES (?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET audio_url=excluded.audio_url, fetched_at=excluded.fetched_at""",
            (conv.uid, audio_url, ts),
        )


def _row_to_message(r: sqlite3.Row) -> Message:
    return Message(
        idx=r["idx"], mid=r["mid"], speaker=r["speaker"], text=r["text"], ts_raw=r["ts_raw"],
        offset_sec=r["offset_sec"], audio_sec=r["audio_sec"], segment=r["segment"],
        recorded=bool(r["recorded"]), after_end=bool(r["after_end"]),
        event_type=r["event_type"], dup_of=r["dup_of"],
    )


def _has_audio(row: sqlite3.Row) -> bool:
    return row["audio_status"] == "ready" and bool(row["audio_path"]) and Path(row["audio_path"]).is_file()


def get_conversation(con: sqlite3.Connection, uid: str) -> Conversation:
    """Диалог с репликами, фактами и has_audio (mp3 скачан и лежит на диске). KeyError, если нет."""
    row = con.execute("SELECT * FROM conversations WHERE uid=?", (uid,)).fetchone()
    if row is None:
        raise KeyError(uid)
    msgs = [_row_to_message(r) for r in
            con.execute("SELECT * FROM messages WHERE uid=? ORDER BY idx", (uid,))]
    return Conversation(
        uid=row["uid"],
        group_id=row["group_id"],
        external_id=row["external_id"],
        channel=row["channel"],
        language=row["language"],
        messages=msgs,
        facts=Facts.model_validate_json(row["facts_json"]) if row["facts_json"] else None,
        operator_name=row["operator_name"],
        has_audio=_has_audio(row),
        content_hash=row["content_hash"],
    )


def list_uids(con: sqlite3.Connection, group_id: str | None = None) -> list[str]:
    if group_id is None:
        rows = con.execute("SELECT uid FROM conversations ORDER BY group_id, external_id")
    else:
        rows = con.execute("SELECT uid FROM conversations WHERE group_id=? ORDER BY external_id", (group_id,))
    return [r[0] for r in rows]


# ---------------------------------------------------------------- аудио

def get_audio_path(con: sqlite3.Connection, uid: str) -> Path | None:
    """Путь к скачанному mp3 или None (не звонок, не скачан, файла нет)."""
    row = con.execute("SELECT audio_status, audio_path FROM conversations WHERE uid=?", (uid,)).fetchone()
    if row is None or not _has_audio(row):
        return None
    return Path(row["audio_path"])


def set_audio(con: sqlite3.Connection, uid: str, status: str, path: str | Path | None) -> None:
    if status not in AUDIO_STATUSES:
        raise ValueError(f"audio_status: {status!r}, ожидается одно из {AUDIO_STATUSES}")
    con.execute("UPDATE conversations SET audio_status=?, audio_path=? WHERE uid=?",
                (status, str(path) if path else None, uid))


def audio_rows(con: sqlite3.Connection, group_id: str) -> list[sqlite3.Row]:
    """Звонки группы с секретной ссылкой: uid, external_id, audio_status, audio_path (без самой ссылки)."""
    return con.execute(
        """SELECT c.uid, c.external_id, c.audio_status, c.audio_path
           FROM conversations c JOIN conversation_secrets s ON s.uid = c.uid
           WHERE c.group_id=? AND s.audio_url IS NOT NULL ORDER BY c.external_id""",
        (group_id,),
    ).fetchall()


def _get_audio_url(con: sqlite3.Connection, uid: str) -> str | None:
    """СЕКРЕТ (sig=). Только для cqa/ingest/audio.py: не печатать, не логировать, не отдавать наружу."""
    row = con.execute("SELECT audio_url FROM conversation_secrets WHERE uid=?", (uid,)).fetchone()
    return row[0] if row else None


def _set_audio_url(con: sqlite3.Connection, uid: str, audio_url: str | None) -> None:
    """Обновить секретную ссылку (после перезагрузки группы из API)."""
    con.execute(
        """INSERT INTO conversation_secrets(uid, audio_url, fetched_at) VALUES (?, ?, ?)
           ON CONFLICT(uid) DO UPDATE SET audio_url=excluded.audio_url, fetched_at=excluded.fetched_at""",
        (uid, audio_url, now_iso()),
    )

