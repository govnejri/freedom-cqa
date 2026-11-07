"""Изменения автотестов (cqa/quality/mutate.py): структура транскрипта мутанта — ID, участки, событие завершения."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqa.ingest.normalize import load_event_rules, normalize_record
from cqa.ingest.parse import parse_record
from cqa.quality.mutate import (
    NotApplicable,
    kz_copula,
    load_config,
    mutate,
    parse_config,
    shift_ts,
)

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _rec(cid: str, lang: str, rows: list[tuple[str, str, str]], channel: str = "agent chat") -> dict:
    return {"conversation_id": cid, "channel": channel, "language": lang,
            "transcript": [{"timestamp": ts, "speaker": sp, "text": tx} for ts, sp, tx in rows]}


RU = _rec("t-ru", "RU", [
    ("00:00", "client", "Подскажите, где посмотреть мой тариф?"),
    ("00:02", "bot", "Здравствуйте! Я AIM, AI-ассистент. Чем помочь?"),
    ("00:15", "client", "Соедините с оператором."),
    ("00:17", "system", "Эскалация на оператора."),
    ("01:47", "operator", "Здравствуйте! Меня зовут Ольга. Сейчас проверю ваш тариф."),
    ("01:55", "client", "Хорошо, жду."),
    ("03:30", "operator", "Ваш тариф — «Стандарт», он указан в разделе «Профиль»."),
    ("03:41", "client", "Нашла, спасибо."),
    ("03:50", "operator", "Пожалуйста. Хорошего дня!"),
])
EN = _rec("t-en", "EN", [
    ("00:00", "client", "How do I find my plan?"),
    ("00:01", "bot", "Hello! I'm AIM, an AI assistant."),
    ("00:10", "client", "Agent please."),
    ("00:11", "system", "Chat transferred to an agent."),
    ("00:40", "operator", "Hi, I'm Maya from support. Open the account area to see your plan."),
    ("00:55", "client", "Found it. And the commission?"),
    ("01:20", "operator", "The commission is listed on the same page."),
    ("01:30", "client", "Great, thanks."),
    ("01:40", "operator", "You're welcome. Have a good day!"),
])
NO_NAME = _rec("t-ru-noname", "RU", [
    ("00:00", "client", "Где найти отчёт?"),
    ("00:05", "system", "Эскалация на оператора."),
    ("00:40", "operator", "Привет, где именно ищете?"),
    ("00:55", "client", "В приложении."),
    ("01:20", "operator", "Меню → Отчёты. Хорошего дня!"),
])
NO_GREETING = _rec("t-ru-nogreet", "RU", [
    ("00:00", "client", "Где найти отчёт?"),
    ("00:05", "system", "Эскалация на оператора."),
    ("00:40", "operator", "Меня зовут Ольга. Где именно ищете?"),
    ("00:55", "client", "В приложении."),
    ("01:20", "operator", "Меню → Отчёты. Хорошего дня!"),
])
EARLY_FAREWELL = _rec("t-ru-bye", "RU", [
    ("00:00", "client", "Где найти отчёт?"),
    ("00:05", "system", "Эскалация на оператора."),
    ("00:40", "operator", "Здравствуйте, я Ольга. Меню → Отчёты. Хорошего дня!"),
    ("00:55", "client", "Спасибо."),
    ("01:20", "operator", "Обращайтесь."),
])


def _mini(cid: str) -> dict:
    data = json.loads(MINI.read_text(encoding="utf-8"))
    return next(r for r in data["data"]["records"] if r["conversation_id"] == cid)


def conv(rec: dict, group: str = "DEV"):
    return normalize_record(parse_record(rec), group, load_event_rules())


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _assert_ids(m) -> None:
    assert [x.mid for x in m.messages] == [f"m{i}" for i in range(1, len(m.messages) + 1)]
    assert [x.idx for x in m.messages] == list(range(1, len(m.messages) + 1))
    assert m.facts is not None and m.facts.channel == m.channel


def test_config_has_all_mutations(cfg):
    assert set(cfg.mutations) >= {"insult", "private_offer", "drop_intro", "disconnect", "operator_closed",
                                  "injection", "rename_operator"}
    assert cfg.mutations["insult"].targets == ["08", "05"]
    assert cfg.mutations["injection"].same_as_base and cfg.mutations["injection"].targets is None
    assert cfg.mutations["injection"].channels is None                        # звонки — только для инъекции
    for mid, spec in cfg.mutations.items():
        if mid != "injection":
            assert spec.channels == ["agent chat"], mid


def test_event_texts_classified_per_language(cfg):
    rules = load_event_rules()
    for mid in ("disconnect", "operator_closed"):
        spec = cfg.mutations[mid]
        for lang, text in spec.params["append"].items():
            assert rules.classify(text) == spec.params["event"], (mid, lang, text)


def test_insult_inserted_after_first_client_reply(cfg):
    base = conv(RU)
    m = mutate(base, cfg.mutations["insult"], cfg)
    _assert_ids(m)
    assert m.uid == "AUTOTEST:t-ru~insult" and m.group_id == "AUTOTEST" and m.external_id == "t-ru~insult"
    assert len(m.messages) == len(base.messages) + 1
    ins = m.messages[6]                       # после «Хорошо, жду.» (m6)
    assert ins.speaker == "operator" and ins.segment == "operator"
    assert ins.text == cfg.mutations["insult"].params["text"]["RU"]
    assert m.messages[5].text == "Хорошо, жду." and m.messages[7].speaker == "operator"
    assert m.facts.first_operator_mid == base.facts.first_operator_mid == "m5"
    assert m.operator_name == "Ольга"


def test_insert_uses_dialog_language(cfg):
    kz = conv(_mini("mini-kz-chat"))
    m = mutate(kz, cfg.mutations["insult"], cfg)
    assert cfg.mutations["insult"].params["text"]["KZ"] in [x.text for x in m.messages]
    en = mutate(conv(EN), cfg.mutations["private_offer"], cfg)
    texts = [x.text for x in en.messages]
    assert texts[-2] == cfg.mutations["private_offer"].params["text"]["EN"]   # перед последней репликой оператора
    assert en.messages[-1].speaker == "operator" and en.messages[-2].speaker == "operator"


def test_private_offer_not_first_operator_message(cfg):
    m = mutate(conv(RU), cfg.mutations["private_offer"], cfg)
    _assert_ids(m)
    first = m.by_mid()[m.facts.first_operator_mid]
    assert first.text.startswith("Здравствуйте! Меня зовут Ольга")
    assert m.messages[-2].text == cfg.mutations["private_offer"].params["text"]["RU"]
    one_op = _rec("t-one", "RU", [("00:00", "client", "Тариф?"), ("00:05", "system", "Эскалация на оператора."),
                                  ("00:40", "operator", "Здравствуйте, я Ольга. Тариф «Стандарт»."),
                                  ("00:55", "client", "Спасибо.")])
    with pytest.raises(NotApplicable):
        mutate(conv(one_op), cfg.mutations["private_offer"], cfg)


@pytest.mark.parametrize("rec,lang", [(RU, "RU"), (EN, "EN"), (None, "KZ")])
def test_drop_intro_removes_greeting_and_name(cfg, rec, lang):
    base = conv(rec) if rec else conv(_mini("mini-kz-chat"))
    assert base.operator_name
    m = mutate(base, cfg.mutations["drop_intro"], cfg)
    _assert_ids(m)
    assert len(m.messages) == len(base.messages)
    first = m.by_mid()[m.facts.first_operator_mid]
    assert m.operator_name is None
    assert base.operator_name not in first.text
    assert first.text and first.text[0].isupper()
    low = first.text.casefold()
    assert not low.startswith(("здравствуйте", "hi", "hello", "сәлеметсіз"))
    # остальные реплики не тронуты
    assert [x.text for x in m.messages if x.mid != first.mid] == [x.text for x in base.messages if x.mid != first.mid]


@pytest.mark.parametrize("rec", [NO_NAME, NO_GREETING])
def test_drop_intro_skips_without_template(cfg, rec):
    with pytest.raises(NotApplicable):
        mutate(conv(rec), cfg.mutations["drop_intro"], cfg)


def test_disconnect_cuts_after_client_and_adds_event(cfg):
    base = conv(RU)
    m = mutate(base, cfg.mutations["disconnect"], cfg)
    _assert_ids(m)
    last = m.messages[-1]
    assert last.speaker == "system" and last.event_type == "connection_lost"
    assert last.text == "Соединение с клиентом прервано."
    assert m.messages[-2].speaker == "client" and m.messages[-2].text == "Нашла, спасибо."
    assert m.facts.ending.type == "connection_lost" and m.facts.ending.abrupt
    assert m.facts.ending.event_mid == last.mid
    assert "Пожалуйста. Хорошего дня!" not in [x.text for x in m.messages]
    assert last.ts_raw == "04:01"                                            # 03:41 + 20 с, тот же формат


def test_operator_closed_replaces_farewell(cfg):
    for rec, lang in ((RU, "RU"), (EN, "EN")):
        base = conv(rec)
        m = mutate(base, cfg.mutations["operator_closed"], cfg)
        _assert_ids(m)
        last = m.messages[-1]
        assert last.event_type == "operator_closed"
        assert last.text == cfg.mutations["operator_closed"].params["append"][lang]
        assert m.messages[-2].speaker == "client"
        assert m.facts.ending.type == "operator_closed" and not m.facts.ending.abrupt
        assert len(m.messages) == len(base.messages)                         # прощание → событие


def test_cut_end_on_existing_ending_event(cfg):
    kz = conv(_mini("mini-kz-chat"))                                         # заканчивается «Оператор чатты жапты»
    m = mutate(kz, cfg.mutations["disconnect"], cfg)
    assert m.messages[-1].text == "Клиентпен байланыс үзілді." and m.facts.ending.type == "connection_lost"
    assert sum(x.speaker == "system" and x.event_type in ("operator_closed", "connection_lost") for x in m.messages) == 1


def test_cut_end_skips_when_operator_already_said_goodbye(cfg):
    with pytest.raises(NotApplicable):
        mutate(conv(EARLY_FAREWELL), cfg.mutations["operator_closed"], cfg)


def test_injection_in_call(cfg):
    call = conv(_mini("mini-ru-call"))
    m = mutate(call, cfg.mutations["injection"], cfg)
    _assert_ids(m)
    ins = next(x for x in m.messages if x.text == cfg.mutations["injection"].params["text"]["RU"])
    assert ins.speaker == "client" and ins.segment == "operator"
    prev = m.messages[ins.idx - 2]
    assert prev.text == "Хочу узнать статус заявки."
    assert ins.recorded and ins.audio_sec is not None                        # звонок: запись пересчитана
    assert m.facts.hold_range is not None and m.facts.ending.type == "client_ended_call"
    assert m.channel == "voice call"


def test_calls_only_for_injection(cfg):
    call = conv(_mini("mini-ru-call"))
    for mid in ("insult", "drop_intro", "disconnect", "rename_operator"):
        with pytest.raises(NotApplicable):
            mutate(call, cfg.mutations[mid], cfg)


def test_rename_operator(cfg):
    m = mutate(conv(RU), cfg.mutations["rename_operator"], cfg)
    _assert_ids(m)
    assert m.operator_name == "Карина"                                       # женское имя → женское
    assert all("Ольга" not in x.text for x in m.messages)
    kz = conv(_mini("mini-kz-chat"))
    mk = mutate(kz, cfg.mutations["rename_operator"], cfg)
    assert mk.operator_name == "Жанар"
    assert any("мен Жанармын" in x.text for x in mk.messages)               # казахская связка пересчитана
    assert all("Айгүл" not in x.text for x in mk.messages)


def test_kz_copula_and_shift_ts():
    assert kz_copula("Ерлан") == "мын" and kz_copula("Әсел") == "мін" and kz_copula("Самат") == "пын"
    assert shift_ts("03:41", 20) == "04:01" and shift_ts("59:50", 20) == "60:10"
    assert shift_ts("2026-11-03T10:00:00.0000000+00:00", 5).startswith("2026-11-03T10:00:05")
    assert shift_ts("bad", 5) == "bad"


def test_parse_config_errors():
    with pytest.raises(ValueError):
        parse_config({"mutations": {"x": {"expect": {"01": "Нет"}}}})                    # нет операции
    with pytest.raises(ValueError):
        parse_config({"mutations": {"x": {"insert": {"at": "nowhere"}, "expect": {"01": "Нет"}}}})
    cfg = parse_config({"mutations": {"x": {"rename": {}, "expect": "same_as_base"}}})
    assert cfg.mutations["x"].same_as_base and cfg.group == "AUTOTEST"
