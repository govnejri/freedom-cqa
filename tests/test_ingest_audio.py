"""Аудио и API без сети: httpx.MockTransport. Подписи sig= не должны утекать в ошибки и отчёты."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from cqa.ingest import audio, fetch, store
from cqa.ingest.service import ingest_payload
from cqa.settings import get_settings

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
MP3 = b"ID3\x04\x00" + b"\x00" * 64
PAGE = ('<html><body><audio controls><source src="https://cdn.example.invalid/f.mp3?a=1&amp;sig=MP3SIG" '
        'type="audio/mpeg"></audio></body></html>')


def _payload() -> dict:
    return json.loads(MINI.read_text(encoding="utf-8"))


def test_extract_source_url_unescapes_amp():
    url = audio.extract_source_url(PAGE, "https://audio.example.invalid/play")
    assert url == "https://cdn.example.invalid/f.mp3?a=1&sig=MP3SIG"
    assert audio.extract_source_url("<audio src='/x.mp3'></audio>", "https://h.invalid/p") == "https://h.invalid/x.mp3"
    assert audio.extract_source_url("<html></html>", "https://h.invalid/") is None


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_one_via_html_page(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        if req.url.host == "audio.example.invalid":
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        assert req.url.params["a"] == "1" and req.url.params["sig"] == "MP3SIG"
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    dest = tmp_path / "x.mp3"
    audio.download_one(_client(handler), "https://audio.example.invalid/play?id=1&sig=PAGESIG", dest)
    assert dest.read_bytes() == MP3
    assert seen == ["audio.example.invalid", "cdn.example.invalid"]


def test_download_errors_do_not_leak_sig(tmp_path):
    def expired(req):
        return httpx.Response(403, text="expired sig=PAGESIG")

    with pytest.raises(audio.LinkExpired) as ei:
        audio.download_one(_client(expired), "https://audio.example.invalid/play?sig=PAGESIG", tmp_path / "a.mp3")
    assert "sig" not in str(ei.value)

    def broken(req):
        raise httpx.ConnectError("boom https://audio.example.invalid/play?sig=PAGESIG", request=req)

    with pytest.raises(audio.AudioError) as ei:
        audio.download_one(_client(broken), "https://audio.example.invalid/play?sig=PAGESIG", tmp_path / "a.mp3")
    assert "sig" not in str(ei.value) and "http" not in str(ei.value)

    def not_mp3(req):
        if "cdn" in req.url.host:
            return httpx.Response(200, text="<html>nope</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    with pytest.raises(audio.AudioError):
        audio.download_one(_client(not_mp3), "https://audio.example.invalid/play?sig=PAGESIG", tmp_path / "a.mp3")
    assert not (tmp_path / "a.mp3").exists()


def test_download_audio_sets_status_and_refreshes_expired(tmp_db, monkeypatch):
    ingest_payload(tmp_db, _payload(), "file")
    uid = "MINI:mini-ru-call"
    state = {"page_calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "audio.example.invalid":
            state["page_calls"] += 1
            if req.url.params.get("sig") == "FAKESIG":     # старая ссылка истекла
                return httpx.Response(403)
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    real_client = httpx.Client
    monkeypatch.setattr(audio.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))

    fresh = _payload()
    fresh["data"]["records"][2]["audio_url"] = "https://audio.example.invalid/play?id=42&sig=NEWSIG"
    raw_file = Path(get_settings().raw_dir) / "fresh.json"

    def fake_fetch(group_id, client=None):
        assert group_id == "MINI"
        return fresh, raw_file

    monkeypatch.setattr(fetch, "fetch_group_raw", fake_fetch)
    monkeypatch.setattr(get_settings(), "cqa_api_url", "https://api.example.invalid/x?sig=APISIG")

    rep = audio.download_audio(tmp_db, "MINI")
    assert rep.downloaded == [uid] and rep.failed == {} and rep.refreshed
    assert state["page_calls"] == 2
    path = store.get_audio_path(tmp_db, uid)
    assert path == audio.audio_file(uid) and path.read_bytes() == MP3
    assert ":" not in path.name
    assert store._get_audio_url(tmp_db, uid).endswith("sig=NEWSIG")
    assert "sig" not in rep.summary()

    # повторный вызов ничего не качает
    rep2 = audio.download_audio(tmp_db, "MINI")
    assert rep2.skipped == [uid] and rep2.downloaded == []


def test_download_audio_failure_status(tmp_db, monkeypatch):
    ingest_payload(tmp_db, _payload(), "file")
    real_client = httpx.Client
    monkeypatch.setattr(audio.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    monkeypatch.setattr(get_settings(), "cqa_api_url", None)
    rep = audio.download_audio(tmp_db, "MINI")
    assert list(rep.failed) == ["MINI:mini-ru-call"] and "500" in rep.failed["MINI:mini-ru-call"]
    st = tmp_db.execute("SELECT audio_status FROM conversations WHERE uid='MINI:mini-ru-call'").fetchone()[0]
    assert st == "failed"


def test_transcript_durations_and_probe_without_mp3(tmp_db):
    ingest_payload(tmp_db, _payload(), "file")
    conv = store.get_conversation(tmp_db, "MINI:mini-ru-call")
    recorded, span, paused, n = audio.transcript_durations(conv)
    assert (recorded, span, paused, n) == (pytest.approx(203.0), pytest.approx(207.5), 10.0, 1)
    lines = []
    rows = audio.probe(tmp_db, "MINI", echo=lines.append)
    assert [r.uid for r in rows] == ["MINI:mini-ru-call"]
    assert rows[0].mp3_sec is None and rows[0].diff_sec is None
    assert not any("sig=" in ln for ln in lines)


# ---------------------------------------------------------------- fetch

def test_fetch_group_saves_raw_and_hides_url(tmp_db, monkeypatch):
    monkeypatch.setattr(get_settings(), "cqa_api_url", "https://api.example.invalid/run?api-version=1&sig=APISIG")
    calls = []

    def ok(req):
        calls.append(str(req.url))
        return httpx.Response(200, json=_payload())

    payload, path = fetch.fetch_group_raw("MINI", client=_client(ok))
    assert calls[0].endswith("&sig=APISIG&group_id=MINI")
    assert payload["data"]["dataset_group_id"] == "MINI"
    assert path.parent == Path(get_settings().raw_dir) and json.loads(path.read_text(encoding="utf-8")) == payload

    for handler in (lambda r: httpx.Response(500, text="err sig=APISIG"),
                    lambda r: httpx.Response(200, text="not json"),
                    lambda r: httpx.Response(200, json={"isSuccess": False, "error": "bad https://x?sig=APISIG"})):
        with pytest.raises(fetch.FetchError) as ei:
            fetch.fetch_group_raw("MINI", client=_client(handler))
        assert "APISIG" not in str(ei.value) and "api.example" not in str(ei.value)

    def boom(req):
        raise httpx.ConnectTimeout("timeout on https://api.example.invalid/run?sig=APISIG", request=req)

    with pytest.raises(fetch.FetchError) as ei:
        fetch.fetch_group_raw("MINI", client=_client(boom))
    assert "APISIG" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_fetch_requires_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "cqa_api_url", None)
    with pytest.raises(fetch.FetchError):
        fetch.fetch_group("DEV")


def test_redact():
    assert fetch.redact("x https://h.invalid/p?a=1&sig=SECRET y") == "x <url> y"
    assert fetch.redact("sig=SECRET&b=2") == "sig=***&b=2"
