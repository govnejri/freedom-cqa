"""Факты: первая реплика оператора, эскалация, ожидание, паузы, удержание, завершение."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from cqa.ingest.facts import build_facts
from cqa.ingest.normalize import normalize_record
from cqa.ingest.parse import parse_record

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _records() -> dict[str, dict]:
    recs = json.loads(MINI.read_text(encoding="utf-8"))["data"]["records"]
    return {r["conversation_id"]: r for r in recs}


def _facts(cid: str, rec: dict | None = None):
    return normalize_record(parse_record(rec or _records()[cid]), "MINI").facts


def test_ru_chat_facts():
    f = _facts("mini-ru-chat")
    assert f.channel == "agent chat" and f.language == "RU"
    assert f.first_operator_mid == "m7"
    assert f.escalation_mid == "m5"
    assert f.operator_joined_mid is None
    assert f.first_reaction_sec == 90.0
    assert f.client_msgs_before_operator == ["m1", "m3", "m6"]
    assert len(f.waits) == 1
    w = f.waits[0]
    assert (w.after_mid, w.operator_mid, w.seconds, w.prev_operator_mid) == ("m8", "m10", 95.0, "m7")
    assert f.id_events == ["m9"]
    assert f.hold_range is None
    assert f.ending.type == "client_left" and f.ending.event_mid == "m13"
    assert f.ending.last_client_mid == "m11"      # m12 — дубль
    assert f.ending.last_operator_mid == "m10"
    assert f.ending.abrupt is True                # оператор не ответил на последнюю реплику клиента
    assert f.ending.seconds_since_last_client == 9.0
    assert f.after_end_mids == ["m14"]
    assert f.unknown_events == []


def test_kz_chat_facts():
    f = _facts("mini-kz-chat")
    assert f.first_operator_mid == "m5"
    assert f.escalation_mid == "m4"
    assert f.first_reaction_sec == 38.0
    assert f.client_msgs_before_operator == ["m1", "m3"]
    assert f.waits == []                          # ровно 60 с — не пауза
    assert f.id_events == ["m7"]
    assert f.ending.type == "operator_closed"
    assert f.ending.abrupt is False               # не из abrupt_ending_types
    assert f.after_end_mids == []


def test_call_facts():
    f = _facts("mini-ru-call")
    assert f.first_operator_mid == "m8"
    assert f.escalation_mid == "m4"               # transfer в звонке
    assert f.operator_joined_mid == "m6"
    assert f.first_reaction_sec == 16.0
    assert f.client_msgs_before_operator == ["m3"]
    assert f.hold_range == ("m10", "m13")
    assert f.waits == []                          # 166 с ожидания — это удержание
    assert f.ending.type == "client_ended_call" and f.ending.event_mid == "m17"
    assert f.ending.last_client_mid == "m15"
    assert f.ending.last_operator_mid == "m14"
    assert f.ending.abrupt is True
    assert f.after_end_mids == ["m18"]


def test_abrupt_false_when_operator_answered_last_client():
    rec = copy.deepcopy(_records()["mini-ru-call"])
    t = rec["transcript"]
    # оператор прощается после последней реплики клиента, затем клиент кладёт трубку
    t.insert(16, {"timestamp": "2026-11-03T10:03:26.0000000+00:00", "speaker": "operator", "text": "До свидания."})
    f = _facts("x", rec)
    assert f.ending.type == "client_ended_call"
    assert f.ending.last_operator_mid == "m17"
    assert f.ending.abrupt is False


def test_wait_not_counted_before_first_operator_reply():
    rec = copy.deepcopy(_records()["mini-ru-chat"])
    f = _facts("x", rec)
    # m6 (waiting) → m7 оператор через 67 с: это first_reaction, а не пауза
    assert all(w.after_mid != "m6" for w in f.waits)


def test_build_facts_is_recomputable():
    conv = normalize_record(parse_record(_records()["mini-ru-chat"]), "MINI", with_facts=False)
    assert conv.facts is None
    assert build_facts(conv) == _facts("mini-ru-chat")
