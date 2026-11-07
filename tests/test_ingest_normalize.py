"""Нормализация: ID, время, дубли, события, участки, after_end, запись, имя оператора, hash."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from cqa.ingest.normalize import (
    EventRules, extract_operator_name, load_event_rules, norm_event_text, normalize_record, parse_ts,
)
from cqa.ingest.parse import parse_record

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _records() -> dict[str, dict]:
    recs = json.loads(MINI.read_text(encoding="utf-8"))["data"]["records"]
    return {r["conversation_id"]: r for r in recs}


def _conv(cid: str, rec: dict | None = None):
    return normalize_record(parse_record(rec or _records()[cid]), "MINI")


def _seg(conv) -> dict[str, str]:
    return {m.mid: m.segment for m in conv.messages}


# ---------------------------------------------------------------- события

def test_norm_event_text():
    assert norm_event_text("  Эскалация   на\tоператора. ") == "эскалация на оператора"
    assert norm_event_text("Agent closed the chat...") == "agent closed the chat"


def test_classify_all_languages_and_regex():
    rules = load_event_rules()
    assert rules.classify("Эскалация на оператора.") == "escalated"
    assert rules.classify("Чат операторға жіберілді.") == "escalated"
    assert rules.classify("CHAT TRANSFERRED TO AN AGENT") == "escalated"
    assert rules.classify("Клиенттің жеке басы расталды.") == "identity_verified"
    assert rules.classify("Bot dialog and transcription languages have been updated. The dialog language "
                          "changed from kk-KZ to ru-RU.") == "language_changed"
    assert rules.classify("Совсем новое событие.") == "unknown"


def test_rules_from_dict_defaults():
    rules = EventRules.from_dict({"events": {"x": {"exact": ["A b."]}}, "ending_types": ["x"]})
    assert rules.classify("a   B") == "x"
    assert rules.escalation_types == ("escalated", "transfer")


# ---------------------------------------------------------------- время

def test_parse_ts():
    assert parse_ts("01:47") == 107
    assert parse_ts("1:02:03") == 3723
    assert parse_ts("2026-11-03T13:10:00.6388245+00:00") == pytest.approx(
        parse_ts("2026-11-03T13:10:00+00:00") + 0.6388245, abs=1e-5)
    assert parse_ts("") is None
    assert parse_ts("вчера") is None


# ---------------------------------------------------------------- чат RU

def test_chat_ids_offsets_dups():
    conv = _conv("mini-ru-chat")
    assert conv.uid == "MINI:mini-ru-chat"
    assert [m.mid for m in conv.messages] == [f"m{i}" for i in range(1, 15)]
    by = conv.by_mid()
    assert by["m7"].offset_sec == 107.0
    assert by["m7"].ts_raw == "01:47"
    assert by["m12"].dup_of == "m11"
    assert [m.mid for m in conv.messages if m.dup_of] == ["m12"]
    # у чатов нет секунд записи
    assert all(m.audio_sec is None and m.recorded for m in conv.messages)


def test_chat_events_and_segments():
    conv = _conv("mini-ru-chat")
    by = conv.by_mid()
    assert by["m5"].event_type == "escalated"
    assert by["m9"].event_type == "identity_verified"   # двойной пробел, без точки
    assert by["m13"].event_type == "client_left"        # верхний регистр
    assert by["m1"].event_type is None                  # не system
    seg = _seg(conv)
    assert [seg[f"m{i}"] for i in range(1, 6)] == ["bot"] * 5
    assert seg["m6"] == "waiting"
    assert all(seg[f"m{i}"] == "operator" for i in range(7, 15))
    assert [m.mid for m in conv.messages if m.after_end] == ["m14"]
    assert conv.operator_name == "Ольга"


def test_kz_chat():
    conv = _conv("mini-kz-chat")
    seg = _seg(conv)
    assert [seg[f"m{i}"] for i in range(1, 5)] == ["bot"] * 4
    assert "waiting" not in seg.values()
    assert conv.operator_name == "Айгүл"
    assert conv.by_mid()["m11"].event_type == "operator_closed"
    assert not any(m.after_end for m in conv.messages)


# ---------------------------------------------------------------- звонок

def test_call_recording_with_pause():
    conv = _conv("mini-ru-call")
    by = conv.by_mid()
    assert by["m1"].event_type == "recording_started" and by["m1"].audio_sec == 0.0
    assert by["m4"].audio_sec == 12.0
    assert by["m5"].event_type == "recording_paused" and by["m5"].recorded and by["m5"].audio_sec == 13.0
    assert not by["m6"].recorded and by["m6"].audio_sec is None       # между паузой и возобновлением
    assert by["m7"].recorded and by["m7"].audio_sec == 18.5           # 23 − 10 с паузы + 5.5 (RESUME_EXTRA_SEC)
    assert by["m8"].audio_sec == 23.5  # + RESUME_EXTRA_SEC
    assert by["m14"].audio_sec == pytest.approx(201.0 - 10.0 + 5.5)  # + RESUME_EXTRA_SEC
    assert by["m8"].offset_sec == 28.0                                # offset — реальное время


def test_call_segments_hold_and_end():
    conv = _conv("mini-ru-call")
    seg = _seg(conv)
    assert [seg[f"m{i}"] for i in range(1, 5)] == ["bot"] * 4         # m4 — сама эскалация (transfer)
    assert [seg[f"m{i}"] for i in range(5, 8)] == ["waiting"] * 3
    assert seg["m8"] == seg["m9"] == "operator"
    assert [seg[f"m{i}"] for i in range(10, 14)] == ["hold"] * 4      # от hold_start до hold_end включительно
    assert all(seg[f"m{i}"] == "operator" for i in range(14, 19))
    assert conv.by_mid()["m16"].dup_of == "m15"
    assert [m.mid for m in conv.messages if m.after_end] == ["m18"]
    assert conv.operator_name is None                                 # «[Имя скрыто]»


def test_call_before_recording_started_is_not_recorded():
    rec = copy.deepcopy(_records()["mini-ru-call"])
    t = rec["transcript"]
    t.insert(0, {"timestamp": "2026-11-03T09:59:50.0000000+00:00", "speaker": "client", "text": "Алло."})
    conv = _conv("x", rec)
    by = conv.by_mid()
    assert not by["m1"].recorded and by["m1"].audio_sec is None
    assert by["m2"].recorded and by["m2"].audio_sec == 0.0
    assert by["m2"].offset_sec == 10.0


def test_call_without_recording_started_counts_from_first_message():
    rec = copy.deepcopy(_records()["mini-ru-call"])
    rec["transcript"] = rec["transcript"][1:]  # без «Запись … запущены»
    conv = _conv("x", rec)
    by = conv.by_mid()
    assert by["m1"].audio_sec == 0.0 and by["m1"].recorded            # бот, 10:00:05
    assert by["m4"].audio_sec == 8.0                                   # пауза 10:00:13
    assert not by["m5"].recorded
    assert by["m7"].audio_sec == 18.5                                  # 28 − 5 − 10 + 5.5 (RESUME_EXTRA_SEC)


def test_empty_transcript_normalizes():
    conv = normalize_record(parse_record({"conversation_id": "e", "channel": "agent chat",
                                          "language": "RU", "transcript": []}), "G")
    assert conv.messages == []
    assert conv.facts is not None and conv.facts.first_operator_mid is None


def test_unknown_role_and_event():
    conv = normalize_record(parse_record({
        "conversation_id": "u", "channel": "agent chat", "language": "RU",
        "transcript": [{"timestamp": "00:00", "speaker": "robot", "text": "?"},
                       {"timestamp": "00:01", "speaker": "system", "text": "Что-то новое"}]}), "G")
    assert conv.messages[0].speaker == "unknown"
    assert conv.messages[1].event_type == "unknown"
    assert conv.facts.unknown_events == ["m2"]


# ---------------------------------------------------------------- имя оператора

@pytest.mark.parametrize("text,name", [
    ("Здравствуйте! Меня зовут Валерия, поддержка Freedom Broker.", "Валерия"),
    ("Добрый день! Я Лера из поддержки.", "Лера"),
    ("Hi, I'm Maya from support.", "Maya"),
    ("Hello, I am Daniel.", "Daniel"),
    ("Сәлеметсіз бе, менің атым Аружан.", "Аружан"),
    ("Сәлеметсіз бе! Мен Нұрланмын.", "Нұрлан"),
    ("Сәлеметсіз бе, мен Мадина.", "Мадина"),
    ("Сәлеметсіз бе, мен Саматпын.", "Самат"),
    ("Здравствуйте, Фридом брокер меня зовут [Имя скрыто], чем могу помочь?", None),
    ("Привет, где именно ищете?", None),
    ("Здравствуйте, я Вас слушаю.", None),
    ("Open settings then go back.", None),
])
def test_operator_name(text, name):
    assert extract_operator_name(text) == name


# ---------------------------------------------------------------- hash

def test_content_hash_stable_and_sensitive():
    a = _conv("mini-ru-chat")
    b = _conv("mini-ru-chat")
    assert a.content_hash == b.content_hash and len(a.content_hash) == 64
    rec = copy.deepcopy(_records()["mini-ru-chat"])
    rec["transcript"][6]["text"] += " "
    assert _conv("x", rec).content_hash != a.content_hash
