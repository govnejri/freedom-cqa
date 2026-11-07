"""БД: save/get, секреты только в conversation_secrets, перезапись реплик и FTS, отчёт загрузки."""
from __future__ import annotations

import json
from pathlib import Path

from cqa.ingest import store
from cqa.ingest.normalize import normalize_record
from cqa.ingest.parse import parse_record
from cqa.ingest.service import ingest_payload, ingest_records

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _payload() -> dict:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _dump_db(con) -> str:
    """Все таблицы, кроме conversation_secrets, одной строкой."""
    out = []
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'messages_fts_%'"):
        if name == "conversation_secrets":
            continue
        out.extend(str(tuple(r)) for r in con.execute(f"SELECT * FROM {name}"))
    return "\n".join(out)


def test_ingest_report_and_roundtrip(tmp_db):
    rep = ingest_payload(tmp_db, _payload(), "file", raw_path=MINI)
    assert rep.group_id == "MINI"
    assert (rep.conversations, rep.messages, rep.calls, rep.with_audio_url) == (3, 14 + 11 + 18, 1, 1)
    assert rep.unknown_events == []
    assert {(d["uid"], d["mid"], d["dup_of"]) for d in rep.duplicates} == {
        ("MINI:mini-ru-chat", "m12", "m11"), ("MINI:mini-ru-call", "m16", "m15")}
    assert "sig=" not in repr(rep)

    ds = tmp_db.execute("SELECT * FROM datasets WHERE group_id='MINI'").fetchone()
    assert ds["source"] == "file" and ds["total_records"] == 3 and ds["raw_path"].endswith("ingest_mini.json")

    assert store.list_uids(tmp_db, "MINI") == ["MINI:mini-kz-chat", "MINI:mini-ru-call", "MINI:mini-ru-chat"]
    assert store.list_uids(tmp_db) == store.list_uids(tmp_db, "MINI")
    assert store.list_uids(tmp_db, "NOPE") == []

    conv = store.get_conversation(tmp_db, "MINI:mini-ru-call")
    expected = normalize_record(parse_record(_payload()["data"]["records"][2]), "MINI")
    assert conv.messages == expected.messages
    assert conv.facts == expected.facts
    assert conv.content_hash == expected.content_hash
    assert conv.has_audio is False                       # mp3 ещё не скачан
    row = tmp_db.execute("SELECT audio_status, normalizer_version FROM conversations WHERE uid=?",
                         (conv.uid,)).fetchone()
    assert row["audio_status"] == "pending"
    chat = tmp_db.execute("SELECT audio_status FROM conversations WHERE uid='MINI:mini-ru-chat'").fetchone()
    assert chat[0] == "none"


def test_audio_url_only_in_secrets(tmp_db):
    ingest_payload(tmp_db, _payload(), "file")
    assert "sig=" not in _dump_db(tmp_db)
    secret = tmp_db.execute("SELECT audio_url FROM conversation_secrets WHERE uid='MINI:mini-ru-call'").fetchone()
    assert "sig=" in secret[0]
    assert "sig=" not in store.get_conversation(tmp_db, "MINI:mini-ru-call").model_dump_json()


def test_resave_overwrites_messages_and_fts(tmp_db):
    ingest_payload(tmp_db, _payload(), "file")
    payload = _payload()
    rec = payload["data"]["records"][0]
    rec["transcript"] = rec["transcript"][:3]
    rec["transcript"][0]["text"] = "Уникальныйтокен вопрос"
    ingest_records(tmp_db, "MINI", [rec], "file")
    conv = store.get_conversation(tmp_db, "MINI:mini-ru-chat")
    assert len(conv.messages) == 3
    n = tmp_db.execute("SELECT count(*) FROM messages_fts WHERE uid='MINI:mini-ru-chat'").fetchone()[0]
    assert n == 3
    hits = tmp_db.execute("SELECT uid, mid FROM messages_fts WHERE messages_fts MATCH 'Уникальныйтокен'").fetchall()
    assert [tuple(h) for h in hits] == [("MINI:mini-ru-chat", "m1")]
    assert tmp_db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 3


def test_save_conversation_creates_dataset_and_facts(tmp_db):
    conv = normalize_record(parse_record(_payload()["data"]["records"][1]), "AUTO", with_facts=False)
    store.save_conversation(tmp_db, conv, None)
    ds = tmp_db.execute("SELECT source FROM datasets WHERE group_id='AUTO'").fetchone()
    assert ds[0] == "manual"
    got = store.get_conversation(tmp_db, conv.uid)
    assert got.facts is not None and got.facts.first_operator_mid == "m5"
    assert got.operator_name == "Айгүл"


def test_set_audio_and_path(tmp_db, tmp_path):
    ingest_payload(tmp_db, _payload(), "file")
    uid = "MINI:mini-ru-call"
    assert store.get_audio_path(tmp_db, uid) is None
    mp3 = tmp_path / "a.mp3"
    store.set_audio(tmp_db, uid, "ready", mp3)
    assert store.get_audio_path(tmp_db, uid) is None     # файла нет на диске
    mp3.write_bytes(b"ID3fake")
    assert store.get_audio_path(tmp_db, uid) == mp3
    assert store.get_conversation(tmp_db, uid).has_audio is True
    # повторная загрузка не сбрасывает готовое аудио
    ingest_payload(tmp_db, _payload(), "file")
    assert store.get_audio_path(tmp_db, uid) == mp3
    store.set_audio(tmp_db, uid, "failed", None)
    assert store.get_audio_path(tmp_db, uid) is None
    try:
        store.set_audio(tmp_db, uid, "weird", None)
    except ValueError:
        pass
    else:
        raise AssertionError("ожидался ValueError")


def test_bad_records_are_skipped(tmp_db):
    rep = ingest_records(tmp_db, "G", [{"channel": "agent chat"}, "junk",
                                       {"conversation_id": "ok", "transcript": []}], "manual")
    assert rep.conversations == 1 and len(rep.skipped) == 2
    assert store.list_uids(tmp_db, "G") == ["G:ok"]
