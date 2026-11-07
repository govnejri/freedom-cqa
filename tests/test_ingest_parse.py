"""Терпимый разбор записей API."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqa.ingest.parse import ParseError, parse_payload, parse_record

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def test_payload_envelope():
    p = parse_payload(json.loads(MINI.read_text(encoding="utf-8")))
    assert p.group_id == "MINI"
    assert p.total_records == 3
    assert len(p.records) == 3
    assert p.is_success


def test_payload_variants():
    assert parse_payload([{"conversation_id": "a"}]).records == [{"conversation_id": "a"}]
    assert parse_payload({"isSuccess": False, "error": "x", "data": None}).is_success is False
    assert parse_payload({"data": {"records": None}}).records == []


def test_unknown_fields_ignored():
    raw = parse_record({
        "conversation_id": "c1", "channel": "agent chat", "language": "ru", "new_field": {"a": 1},
        "transcript": [{"timestamp": "00:01", "speaker": "client", "text": "hi", "extra": True}],
    })
    assert raw.language == "RU"
    assert raw.transcript[0].text == "hi"
    assert raw.warnings == []


def test_unknown_role():
    raw = parse_record({"conversation_id": "c1", "channel": "agent chat", "language": "RU",
                        "transcript": [{"timestamp": "00:01", "speaker": "Supervisor", "text": "x"},
                                       {"timestamp": "00:02", "speaker": " Operator ", "text": "y"}]})
    assert [m.speaker for m in raw.transcript] == ["unknown", "operator"]
    assert any("неизвестная роль" in w for w in raw.warnings)


@pytest.mark.parametrize("transcript", [[], None, "", "[]"])
def test_empty_transcript_does_not_fail(transcript):
    raw = parse_record({"conversation_id": "c1", "channel": "agent chat", "language": "RU",
                        "transcript": transcript})
    assert raw.transcript == []
    assert "пустой транскрипт" in raw.warnings


def test_transcript_as_json_string():
    raw = parse_record({"conversation_id": "c1", "channel": "agent chat", "language": "RU",
                        "transcript": json.dumps([{"timestamp": "00:00", "speaker": "bot", "text": "a"}])})
    assert raw.transcript[0].speaker == "bot"


def test_missing_values_are_tolerated():
    raw = parse_record({"conversation_id": 17, "transcript": [{"speaker": "client"}, 42]})
    assert raw.conversation_id == "17"
    assert raw.channel == "agent chat"
    assert raw.language == "RU"
    assert raw.transcript[0].text == "" and raw.transcript[0].timestamp == ""
    # битый элемент сохраняет позицию, чтобы ID реплик не сдвигались
    assert len(raw.transcript) == 2 and raw.transcript[1].speaker == "unknown"


def test_missing_conversation_id():
    with pytest.raises(ParseError):
        parse_record({"channel": "agent chat", "transcript": []})
    with pytest.raises(ParseError):
        parse_record("not a record")


def test_audio_url_kept_but_not_in_warnings():
    rec = json.loads(MINI.read_text(encoding="utf-8"))["data"]["records"][2]
    raw = parse_record(rec)
    assert raw.is_call
    assert raw.audio_url and "sig=" in raw.audio_url
    assert not any("sig=" in w for w in raw.warnings)
