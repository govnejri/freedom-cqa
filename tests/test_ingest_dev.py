"""Проверки на data/dev.json (пропускаются, если файла нет). Без сети."""
from __future__ import annotations

import json

import pytest

from conftest import DEV_JSON, requires_dev

pytestmark = requires_dev


@pytest.fixture
def dev(tmp_db):
    from cqa.ingest.service import ingest_payload

    rep = ingest_payload(tmp_db, json.loads(DEV_JSON.read_text(encoding="utf-8")), "file", "DEV")
    return tmp_db, rep


def _segments(conv) -> dict[str, str]:
    return {m.mid: m.segment for m in conv.messages}


def test_dev_report(dev):
    _, rep = dev
    assert rep.conversations == 65
    assert rep.messages == 1268
    assert rep.unknown_events == []
    assert rep.duplicates == [{"uid": "DEV:p4-ru-001", "mid": "m21", "dup_of": "m20"}]
    assert rep.calls == 5 and rep.with_audio_url == 5
    assert "sig=" not in repr(rep)


def test_dev_call_hold_and_recording(dev):
    from cqa.ingest.store import get_conversation

    con, _ = dev
    conv = get_conversation(con, "DEV:p4-ru-002")
    seg = _segments(conv)
    assert all(seg[f"m{i}"] == "hold" for i in range(18, 60))
    assert seg["m17"] != "hold" and seg["m60"] != "hold"
    assert conv.facts.hold_range == ("m18", "m59")
    by = conv.by_mid()
    assert all(not by[f"m{i}"].recorded and by[f"m{i}"].audio_sec is None for i in range(1, 5))
    assert by["m5"].event_type == "recording_started" and by["m5"].audio_sec == 0.0
    assert all(m.recorded for m in conv.messages[4:])


def test_dev_waiting_and_after_end(dev):
    from cqa.ingest.store import get_conversation

    con, _ = dev
    c3 = get_conversation(con, "DEV:p1-ru-003")
    assert c3.by_mid()["m10"].speaker == "client" and c3.by_mid()["m10"].segment == "waiting"
    assert "m10" in c3.facts.client_msgs_before_operator

    c4 = get_conversation(con, "DEV:p4-ru-004")
    m115 = c4.by_mid()["m115"]
    assert m115.speaker == "operator" and m115.after_end
    assert c4.facts.ending.type == "client_ended_call" and c4.facts.ending.event_mid == "m114"
    assert c4.facts.after_end_mids == ["m115"]


def test_dev_operator_names_and_pause(dev):
    from cqa.ingest.store import get_conversation, list_uids

    con, _ = dev
    names = {u: get_conversation(con, u).operator_name for u in list_uids(con, "DEV")}
    assert all(names[f"DEV:p4-ru-00{i}"] is None for i in range(1, 6))       # имя скрыто в звонках
    assert sum(1 for v in names.values() if v) >= 55
    c28 = get_conversation(con, "DEV:p1-ru-028")
    assert [(w.after_mid, w.operator_mid, w.prev_operator_mid) for w in c28.facts.waits] == [("m11", "m12", "m10")]
