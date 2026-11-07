"""Генератор мок-данных фронтенда: 5 реальных диалогов DEV + правдоподобные оценки.

    uv run python frontend/mock/generate.py

Пишет frontend/mock/*.json в формате DTO (`cqa/api/schemas.py`); каждый объект проверяется
pydantic-моделью DTO. Итоги считаются по правилам §3 спецификации (Fraction → ROUND_HALF_UP).
`audio_url` организаторов (с `sig=`) не копируется: у звонка `audio_url = null`, фронт в мок-режиме
подставляет тихую запись.
"""
from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cqa.api.schemas import (  # noqa: E402
    AppealDTO,
    ChecklistVersionDTO,
    ConversationDTO,
    ConversationSummaryDTO,
    EvaluationDTO,
    HealthDTO,
    RunDTO,
)
from cqa.checklist.model import ChecklistConfig  # noqa: E402

OUT = Path(__file__).resolve().parent
GROUP = "DEV"
IDS = ["p1-ru-001", "p1-ru-022", "p1-kz-015", "p1-ru-025", "p4-ru-002"]
EVAL_ID = {cid: 101 + i for i, cid in enumerate(IDS)}
OPERATOR = {"p1-ru-001": "Алина", "p1-ru-022": "Кирилл", "p1-kz-015": "Мұрат", "p1-ru-025": "Валерия", "p4-ru-002": None}

# ---------------------------------------------------------------- нормализация (упрощённо по §1)

def _norm_event(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().strip()).rstrip(".")


def load_events():
    cfg = yaml.safe_load((ROOT / "config" / "events.yaml").read_text(encoding="utf-8"))
    exact = {}
    regex = []
    for etype, spec in cfg["events"].items():
        for s in spec.get("exact", []):
            exact[_norm_event(s)] = etype
        for r in spec.get("regex", []):
            regex.append((re.compile(r), etype))
    return exact, regex, set(cfg["ending_types"]), set(cfg["abrupt_ending_types"])


def offset(ts: str) -> float:
    if "T" in ts:
        return datetime.fromisoformat(ts).timestamp()
    mm, ss = ts.split(":")
    return int(mm) * 60 + int(ss)


def normalize(rec, events):
    exact, regex, ending_types, abrupt_types = events
    tr = rec["transcript"]
    is_call = rec["channel"] == "voice call"
    t0 = offset(tr[0]["timestamp"])
    msgs = []
    for i, t in enumerate(tr, 1):
        et = None
        if t["speaker"] == "system":
            n = _norm_event(t["text"])
            et = exact.get(n) or next((e for r, e in regex if r.search(n)), "unknown")
        msgs.append({
            "idx": i, "mid": f"m{i}", "speaker": t["speaker"], "text": t["text"], "ts_raw": t["timestamp"],
            "offset_sec": round(offset(t["timestamp"]) - t0, 3), "audio_sec": None, "segment": "operator",
            "recorded": True, "after_end": False, "event_type": et, "dup_of": None,
        })
    # дубли
    for a, b in zip(msgs, msgs[1:]):
        if (a["speaker"], a["text"], a["ts_raw"]) == (b["speaker"], b["text"], b["ts_raw"]):
            b["dup_of"] = a["dup_of"] or a["mid"]
    # участки
    first_op = next((m["idx"] for m in msgs if m["speaker"] == "operator"), None)
    esc = next((m["idx"] for m in msgs if m["event_type"] == "escalated"), None)
    for m in msgs:
        if first_op is None or m["idx"] < first_op:
            m["segment"] = "waiting" if esc is not None and m["idx"] > esc else "bot"
    hold = None
    for m in msgs:
        if m["event_type"] == "hold_start":
            hold = [m["idx"], None]
        if m["event_type"] == "hold_end" and hold:
            hold[1] = m["idx"]
    if hold and hold[1]:
        for m in msgs:
            if hold[0] <= m["idx"] <= hold[1]:
                m["segment"] = "hold"
    # завершение
    end = next((m for m in msgs if m["event_type"] in ending_types), None)
    if end:
        for m in msgs:
            if m["idx"] > end["idx"]:
                m["after_end"] = True
    # запись (звонки)
    if is_call:
        started = next((m for m in msgs if m["event_type"] == "recording_started"), None)
        base = offset(started["ts_raw"]) if started else t0
        paused_at, paused_total, recording = None, 0.0, started is None
        for m in msgs:
            ts = offset(m["ts_raw"])
            if m["event_type"] == "recording_started":
                recording = True
            elif m["event_type"] == "recording_paused":
                recording, paused_at = False, ts
            elif m["event_type"] == "recording_resumed" and paused_at is not None:
                paused_total += ts - paused_at
                recording, paused_at = True, None
            m["recorded"] = recording
            m["audio_sec"] = round(ts - base - paused_total, 2) if recording else None
    # факты
    by = {m["mid"]: m for m in msgs}
    last_op = next((m["mid"] for m in reversed(msgs) if m["speaker"] == "operator" and not m["after_end"]), None)
    last_cl = next((m["mid"] for m in reversed(msgs) if m["speaker"] == "client" and not m["after_end"]), None)
    ending = {
        "type": end["event_type"] if end else None, "event_mid": end["mid"] if end else None,
        "last_operator_mid": last_op, "last_client_mid": last_cl,
        "abrupt": bool(end and end["event_type"] in abrupt_types and (not last_op or not last_cl or by[last_cl]["idx"] > by[last_op]["idx"])),
        "seconds_since_last_client": round(end["offset_sec"] - by[last_cl]["offset_sec"], 1) if end and last_cl else None,
    }
    waits = []
    prev_client, prev_op = None, None
    for m in msgs:
        if m["segment"] == "hold":
            prev_client = None
            continue
        if m["speaker"] == "client":
            prev_client = prev_client or m
        elif m["speaker"] == "operator":
            if prev_client and m["offset_sec"] - prev_client["offset_sec"] > 60 and first_op and m["idx"] > first_op:
                waits.append({"after_mid": prev_client["mid"], "operator_mid": m["mid"],
                              "seconds": round(m["offset_sec"] - prev_client["offset_sec"], 1),
                              "prev_operator_mid": prev_op})
            prev_client, prev_op = None, m["mid"]
    facts = {
        "channel": rec["channel"], "language": rec["language"],
        "first_operator_mid": f"m{first_op}" if first_op else None,
        "escalation_mid": f"m{esc}" if esc else None,
        "operator_joined_mid": next((m["mid"] for m in msgs if m["event_type"] == "operator_joined"), None),
        "first_reaction_sec": round(by[f"m{first_op}"]["offset_sec"] - by[f"m{esc}"]["offset_sec"], 1) if esc and first_op else None,
        "client_msgs_before_operator": [m["mid"] for m in msgs if m["speaker"] == "client" and m["segment"] in ("bot", "waiting")],
        "waits": waits,
        "id_events": [m["mid"] for m in msgs if m["event_type"] == "identity_verified"],
        "hold_range": [f"m{hold[0]}", f"m{hold[1]}"] if hold and hold[1] else None,
        "ending": ending,
        "after_end_mids": [m["mid"] for m in msgs if m["after_end"]],
        "unknown_events": [m["mid"] for m in msgs if m["event_type"] == "unknown"],
    }
    return msgs, facts


# ---------------------------------------------------------------- оценки (ответы «модели»)

def E(mid, quote):
    return {"id": mid, "quote": quote}


NO_GROSS = "Грубые ошибки — Нет. Оскорблений, предложений услуг в обход компании и раскрытия конфиденциальных данных нет."

EVALS = {
    "p1-ru-001": {
        "01": dict(verdict="Да", evidence=[E("m10", "Здравствуйте! Меня зовут Алина.")],
                   explanation="Установление контакта — Да. В первом ответе оператор поздоровался и назвал имя. Основание — m10.",
                   reasoning="Первая реплика оператора — m10: «Здравствуйте! Меня зовут Алина». Приветствие бота (m2) не учитывается."),
        "02": dict(verdict="N/A", evidence=[E("m5", "Я сейчас с ноутбука, отчёт качать не хочу."), E("m10", "В веб-версии откройте вкладку «Кабинет».")],
                   explanation="Выявление потребности — N/A. Клиент сразу сообщил, что сидит с ноутбука и не хочет скачивать отчёт; данных хватало, оператор ответил без уточнений. Основание — m5, m10.",
                   reasoning="Всё нужное клиент сообщил боту (m1, m5). Оператор учёл ноутбук и отказ от отчёта, переспрашивать было нечего."),
        "03": dict(verdict="N/A", evidence=[E("m1", "Где посмотреть мой тариф?")],
                   explanation="Идентификация — N/A. Вопрос о том, где в интерфейсе виден тариф; данные счёта не раскрывались, действий по счёту не было. Основание — m1.",
                   reasoning="Оператор назвал только путь в интерфейсе, конкретный тариф клиента не сообщал."),
        "04": dict(verdict="20", evidence=[E("m10", "Название тарифа будет под вашим ФИО."), E("m11", "Нашла, спасибо.")],
                   explanation="Корректная и полная консультация — 20. Дан точный путь в веб-версии, клиент подтвердил, что нашла тариф. Основание — m10, m11.",
                   reasoning="Единственный вопрос — где тариф. Ответ точный, клиент подтвердил результат."),
        "05": dict(verdict="Да", evidence=[E("m5", "отчёт качать не хочу"), E("m10", "В веб-версии откройте вкладку «Кабинет».")],
                   explanation="Эмпатия — Да. Тон вежливый; оператор учёл, что клиент не хочет скачивать отчёт, и дал путь без него. Основание — m5, m10.",
                   reasoning="Неудобство клиента (не хочет качать отчёт) решено по существу."),
        "06": dict(verdict="10", evidence=[E("m10", "В веб-версии откройте вкладку «Кабинет». Название тарифа будет под вашим ФИО.")],
                   explanation="Управление диалогом — 10. Один ясный ответ, ожиданий без предупреждения не было. Основание — m10.",
                   reasoning="Пауз больше 60 с после подключения нет. Ответ однозначный."),
        "07": dict(verdict="Нет", evidence=[E("m12", "Пожалуйста. Хорошего вечера!")], checked_range=["m11", "m12"],
                   explanation="Завершение — Нет. Оператор попрощался, но не подвёл итог и не предложил дальнейшую помощь. Основание — m12.",
                   reasoning="Последняя реплика оператора — короткое прощание. Итога и предложения помощи на участке m11–m12 нет.",
                   how_to_fix="Пожалуйста! Рада, что получилось. Если появятся вопросы по тарифу — пишите, поможем. Хорошего вечера!"),
        "08": dict(verdict="Нет", evidence=[E("m10", "Здравствуйте! Меня зовут Алина.")], explanation=NO_GROSS,
                   reasoning="Проверены все реплики оператора (m10, m12): нарушений нет."),
    },
    "p1-ru-022": {
        "01": dict(verdict="Да", evidence=[E("m10", "Здравствуйте, я Кирилл.")],
                   explanation="Установление контакта — Да. Оператор поздоровался и назвал имя в первом ответе. Основание — m10.",
                   reasoning="m10 начинается с «Здравствуйте, я Кирилл»."),
        "02": dict(verdict="Нет", evidence=[E("m10", "Скиньте деньги мне на карту, я сам их вложу с выгодой."), E("m11", "Я про консультацию в приложении спрашивал.")],
                   checked_range=["m10", "m12"],
                   explanation="Выявление потребности — Нет. Клиент просил помочь с выбором облигаций, оператор не выяснил запрос и проигнорировал уточнение клиента. Основание — m10, m11.",
                   reasoning="Клиент сформулировал запрос боту (m1). Оператор подменил потребность и не вернулся к ней после m11.",
                   how_to_fix="Подскажите, пожалуйста, на какой срок и с какой целью планируете вложение? Покажу, как подобрать облигации в приложении."),
        "03": dict(verdict="N/A", evidence=[],
                   explanation="Идентификация — N/A. Данные конкретного счёта не запрашивались и не раскрывались, действий по счёту не было.",
                   reasoning="В диалоге нет раскрытия данных счёта."),
        "04": dict(verdict="0", evidence=[E("m10", "Скиньте деньги мне на карту"), E("m12", "через компанию оформлять не надо")],
                   explanation="Корректная и полная консультация — 0. Вместо ответа о выборе облигаций — предложение перевести деньги лично оператору; ответа по существу нет. Основание — m10, m12.",
                   reasoning="Вопрос о выборе облигаций остался без ответа.",
                   how_to_fix="В приложении откройте раздел «Облигации»: фильтры по сроку погашения и доходности помогут выбрать бумаги. Индивидуальные рекомендации даёт персональный менеджер — могу передать ему ваш запрос."),
        "05": dict(verdict="Нет", evidence=[E("m11", "Вы серьёзно?"), E("m12", "Да, это я лично предлагаю")],
                   explanation="Эмпатия и клиентоориентированность — Нет. Клиент выразил недоумение, оператор настаивал на личной схеме и не отреагировал на проблему. Основание — m11, m12.",
                   reasoning="Реакция на недоумение клиента — повтор предложения.",
                   how_to_fix="Извините за путаницу. Давайте вернёмся к вашему вопросу: покажу, где в приложении выбрать облигации."),
        "06": dict(verdict="10", evidence=[E("m12", "Да, это я лично предлагаю, через компанию оформлять не надо.")],
                   explanation="Управление диалогом — 10. Реплики оператора понятны и связны; неэтичность учтена в 08, отсутствие ответа — в 04. Основание — m10, m12.",
                   reasoning="Принцип «одно нарушение — один критерий»: формулировки ясные."),
        "07": dict(verdict="Нет", evidence=[E("m13", "Нет, спасибо."), E("m14", "Оператор закрыл чат.")], checked_range=["m13", "m14"],
                   explanation="Завершение — Нет. После отказа клиента оператор закрыл чат без прощания. Основание — m13, m14.",
                   reasoning="Последнее событие — «Оператор закрыл чат», прощания оператора нет.",
                   how_to_fix="Понимаю. Если захотите разобраться с облигациями в приложении — напишите, помогу. Хорошего дня!"),
        "08": dict(verdict="Да", category="обход компании",
                   evidence=[E("m10", "Скиньте деньги мне на карту, я сам их вложу с выгодой."), E("m12", "через компанию оформлять не надо")],
                   explanation="Грубые ошибки — Да (обход компании). Оператор предложил перевести деньги ему лично для инвестирования в обход компании. Основание — m10, m12.",
                   reasoning="Достаточно самого предложения, согласие клиента не требуется.",
                   how_to_fix="Инвестировать можно только через ваш счёт в Freedom Broker. Покажу, как выбрать облигации в приложении."),
    },
    "p1-kz-015": {
        "01": dict(verdict="Да", evidence=[E("m10", "Сәлеметсіз бе, мен Мұратпын.")],
                   explanation="Установление контакта — Да. Оператор поздоровался на казахском и назвал имя в первом ответе. Основание — m10.",
                   reasoning="«Сәлеметсіз бе, мен Мұратпын» — приветствие и имя на языке клиента."),
        "02": dict(verdict="N/A", evidence=[E("m1", "Менің шотымда неге ақша шығару жабық екенін қарап беріңізші.")],
                   explanation="Выявление потребности — N/A. Клиент сразу сформулировал запрос — почему закрыт вывод; данных для ответа хватало. Основание — m1, m10.",
                   reasoning="Кроме идентификации (критерий 03), уточнять было нечего."),
        "03": dict(verdict="Нет", evidence=[E("m10", "Сіздің шотыңызда ақша шығаруға шектеу қойылғанын көріп тұрмын."),
                                           E("m11", "Мен әлі жеке басымды растаған жоқпын ғой."), E("m12", "Қазір растаудың қажеті жоқ.")],
                   checked_range=["m1", "m12"],
                   explanation="Идентификация — Нет. Оператор раскрыл ограничение на счёте без подтверждения личности и отклонил возражение клиента. Основание — m10, m11, m12.",
                   reasoning="Системного сообщения о подтверждении личности на участке m1–m12 нет; данные счёта раскрыты в m10.",
                   how_to_fix="Шотыңызды тексеру үшін алдымен жеке басыңызды растау қажет. Растау сілтемесін жіберейін бе?"),
        "04": dict(verdict="5", evidence=[E("m13", "Ал себебі қандай?"), E("m14", "Оны тексеруге жіберемін.")],
                   explanation="Корректная и полная консультация — 5. Сообщён только факт ограничения; причина не названа, «отправлю на проверку» — без срока и канала ответа. Основание — m10, m13, m14.",
                   reasoning="Главный вопрос клиента — причина ограничения — остался открытым.",
                   how_to_fix="Шектеу себебін анықтау үшін сұранысты тексеруге жібердім, жауап 1 жұмыс күні ішінде қосымшадағы чатқа келеді."),
        "05": dict(verdict="Нет", evidence=[E("m11", "Мен әлі жеке басымды растаған жоқпын ғой."), E("m12", "Қазір растаудың қажеті жоқ.")],
                   explanation="Эмпатия и клиентоориентированность — Нет. Обоснованное возражение клиента о непройденной проверке отклонено без реакции. Основание — m11, m12.",
                   reasoning="Отписка на возражение клиента — «Нет» по трактовке критерия.",
                   how_to_fix="Дұрыс айтасыз, кешіріңіз. Алдымен жеке басыңызды растайық, содан кейін шектеу себебін айтамын."),
        "06": dict(verdict="7", evidence=[E("m13", "Ал себебі қандай?")],
                   explanation="Управление диалогом — 7. Клиенту пришлось отдельно спрашивать о причине; реплики короткие, но понятные. Основание — m12, m13.",
                   reasoning="Один заметный сбой: причина не названа сразу.",
                   how_to_fix="Шектеу себебін бірден түсіндіріп, келесі қадамды атаңыз."),
        "07": dict(verdict="Нет", evidence=[E("m14", "Оны тексеруге жіберемін. Сау болыңыз.")], checked_range=["m13", "m14"],
                   explanation="Завершение — Нет. Прощание оборвало незакрытый вопрос клиента о причине ограничения (Т1б). Основание — m13, m14.",
                   reasoning="Вопрос в m13 не закрыт, оператор сразу попрощался.",
                   how_to_fix="Сұранысыңызды тексеруге жібердім, жауапты осы чатқа жазамыз. Тағы сұрақтарыңыз болса, жазыңыз. Сау болыңыз!"),
        "08": dict(verdict="Да", category="раскрытие данных", needs_review=True,
                   evidence=[E("m10", "Сіздің шотыңызда ақша шығаруға шектеу қойылғанын көріп тұрмын.")],
                   explanation="Грубые ошибки — Да (раскрытие данных). Оператор сообщил ограничение на конкретном счёте до подтверждения личности (Т3). Основание — m10, m11.",
                   reasoning="Раскрытие данных счёта без идентификации — и 03 «Нет», и 08 «Да».",
                   second_opinion={"profile": "gemini-argon", "model": "gemini-4-argon", "verdict": "Нет",
                                   "explanation": "Факт ограничения на вывод — общий статус без конкретных сумм и операций; это 03 «Нет», но не грубое раскрытие."}),
    },
    "p1-ru-025": {
        "01": dict(verdict="Да", evidence=[E("m10", "Здравствуйте! Меня зовут Валерия, поддержка Freedom Broker.")],
                   explanation="Установление контакта — Да. Приветствие и имя в первом ответе. Основание — m10.",
                   reasoning="m10 — приветствие, имя и название компании."),
        "02": dict(verdict="Да", evidence=[E("m5", "Сразу подключите оператора, я ему объясню."), E("m10", "С чем помочь?")],
                   explanation="Выявление потребности — Да. Клиент собирался объяснить вопрос оператору; оператор спросил, чем помочь. Основание — m5, m10.",
                   reasoning="Клиент ушёл до формулировки запроса, а оператор спросил, чем помочь, — «Да» по трактовке."),
        "03": dict(verdict="N/A", evidence=[E("m11", "Клиент покинул чат.")],
                   explanation="Идентификация — N/A. Запрос не прозвучал, данные счёта не раскрывались. Основание — m11.",
                   reasoning="Действий по счёту не было."),
        "04": dict(verdict="N/A", evidence=[E("m11", "Клиент покинул чат.")],
                   explanation="Корректная и полная консультация — N/A. Клиент покинул чат до того, как сформулировал запрос. Основание — m11.",
                   reasoning="Условие N/A критерия выполнено: запроса не было."),
        "05": dict(verdict="Да", evidence=[E("m10", "Здравствуйте! Меня зовут Валерия")],
                   explanation="Эмпатия и клиентоориентированность — Да. Вежливое приветствие и предложение помощи. Основание — m10.",
                   reasoning="Недовольства клиент не выражал."),
        "06": dict(verdict="10", evidence=[E("m10", "С чем помочь?")],
                   explanation="Управление диалогом — 10. Единственная реплика оператора ясна. Основание — m10.",
                   reasoning="Пауза до подключения — время первой реакции, оно не оценивается."),
        "07": dict(verdict="N/A", evidence=[E("m11", "Клиент покинул чат.")],
                   explanation="Завершение — N/A. Клиент покинул чат через 13 секунд после приветствия оператора — завершить диалог было невозможно. Основание — m11.",
                   reasoning="Тип завершения — client_left, обрыв раньше, чем оператор мог ответить."),
        "08": dict(verdict="Нет", evidence=[E("m10", "С чем помочь?")], explanation=NO_GROSS,
                   reasoning="Одна реплика оператора, нарушений нет."),
    },
    "p4-ru-002": {
        "01": dict(verdict="Нет", evidence=[E("m3", "Да компания федеброкер меня зовут [Имя скрыто]")], checked_range=["m3", "m3"],
                   explanation="Установление контакта — Нет. Оператор назвал компанию и представился, но не поздоровался в первой реплике. Основание — m3.",
                   reasoning="Первая реплика оператора — m3: представление есть, приветствия нет. Реплика до начала записи.",
                   how_to_fix="Добрый день! Компания Freedom Broker, меня зовут …, мы с вами ранее разговаривали."),
        "02": dict(verdict="N/A", evidence=[E("m3", "мы с вами ранее разговаривали"), E("m6", "можете сейчас попробовать выставить лимит на приказ")],
                   explanation="Выявление потребности — N/A. Продолжение ранее начатого обращения: бумага и ситуация оператору уже известны, уточнений не требовалось. Основание — m3, m6.",
                   reasoning="Повторный звонок по известному вопросу."),
        "03": dict(verdict="Да", evidence=[E("m12", "подскажете, пожалуйста, ещё раз ваш ин полное имя и адрес"), E("m15", "Да, всё верно.")],
                   explanation="Идентификация — Да. Перед соединением с трейдером оператор повторно проверил личность (ИИН, ФИО, адрес) и подтвердил: «Да, всё верно». Основание — m12, m15.",
                   reasoning="Голосовая идентификация в звонке: запрос данных и подтверждение оператора до обсуждения сделки."),
        "04": dict(verdict="15", evidence=[E("m64", "его можно купить только по уйти всей сделки"),
                                          E("m66", "есть также другие юрисдикции, которые у которых есть доступ к покупке"),
                                          E("m67", "только путь все сделки его можно приобрести или продать")],
                   explanation="Корректная и полная консультация — 15. Объяснено, что бумагу можно купить или продать только внебиржевой сделкой и почему отображается стакан. Не сказано, как оформить такую сделку и на каких условиях. Основание — m64, m66, m67.",
                   reasoning="Сказанное коллеге на удержании (m42–m44 о комиссии) клиент не слышал и не учитывается.",
                   how_to_fix="Такую бумагу можно купить через внебиржевую сделку: оставьте заявку трейдеру, комиссия — 2% от суммы, минимум 25 долларов. Оформить заявку?"),
        "05": dict(verdict="Да", evidence=[E("m60", "благодарю вас за ожидание"), E("m64", "К сожалению")],
                   explanation="Эмпатия и клиентоориентированность — Да. Оператор вежлив, благодарит за ожидание и сочувственно объясняет отказ. Основание — m60, m64.",
                   reasoning="Тон доброжелательный на всём участке вне удержания."),
        "06": dict(verdict="10", evidence=[E("m15", "Сейчас прошу вас ожидать на линии."), E("m60", "благодарю вас за ожидание")],
                   explanation="Управление диалогом — 10. Удержание предупреждено и закрыто благодарностью за ожидание; объяснение понятно. Основание — m15, m60, m64.",
                   reasoning="Консультация на удержании не оценивается. Ошибки распознавания — не ошибки оператора."),
        "07": dict(verdict="Да", evidence=[E("m70", "Я понял спасибо."), E("m71", "Благодарю вас за уделенное время всего хорошего до свидания.")],
                   explanation="Завершение — Да. После подтверждения клиента оператор поблагодарил за время и попрощался. Основание — m70, m71.",
                   reasoning="Клиент подтвердил, что понял ответ, затем прощание оператора."),
        "08": dict(verdict="Нет", evidence=[E("m64", "К сожалению, его на бирже нельзя купить")],
                   explanation=NO_GROSS + " Участок удержания не учитывается.",
                   reasoning="Реплики удержания клиент не слышал."),
    },
}

APPEALS = [
    dict(id=1, ext="p1-ru-001", cid="07", status="decided", created_at="2026-11-07T19:41:10Z",
         comment="Клиентка подтвердила, что нашла тариф, а я вежливо попрощалась. По трактовке Т1 это «Да».",
         counter=["m11", "m12"],
         decision=dict(action="change", new_verdict="Да", reviewer="reviewer", make_precedent=True,
                       reason="Т1: клиент подтвердил, что вопрос решён, — вежливое прощание засчитывается. Трактовка добавлена в чек-лист v2.",
                       created_at="2026-11-07T19:52:31Z")),
    dict(id=2, ext="p4-ru-002", cid="01", status="open", created_at="2026-11-07T20:15:44Z",
         comment="Это повторный звонок: клиент перезвонил по открытому вопросу, начало фразы обрезано распознаванием. Компанию и имя я назвал сразу.",
         counter=["m3", "m4"],
         ai_opinion={"profile": "gemini-argon", "model": "gemini-4-argon", "verdict": "Да",
                     "explanation": "Без исходного вердикта: реплика m3 начинается с «Да компания…» — похоже на обрезанное распознаванием приветствие. Оператор назвал компанию и имя, клиент узнал его. Склоняюсь к «Да», уверенность средняя."}),
]

# ---------------------------------------------------------------- скоринг §3

def q2(x: Fraction) -> float:
    d = (Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(d)


def score(cfg: ChecklistConfig, verdicts: dict[str, str], categories: dict[str, str]):
    crits = cfg.enabled_criteria()
    for c in crits:
        if verdicts[c.id] == "N/A" and not c.na_allowed:
            raise ValueError(f"N/A не разрешён для {c.id}")
    W = sum((Fraction(str(c.weight)) for c in crits
             if c.type != "flag" and c.weight > 0 and verdicts[c.id] != "N/A"), Fraction(0))
    out = []
    for c in crits:
        v = verdicts[c.id]
        w = Fraction(str(c.weight))
        applicable = v != "N/A"
        raw = eff = pts = Fraction(0)
        if c.type != "flag" and applicable and W:
            if c.type == "scale":
                raw = w * int(v) / c.top
            else:
                raw = w if v == "Да" else Fraction(0)
            eff = w * 100 / W
            pts = raw * 100 / W
        out.append((c, v, applicable, w, eff, raw, pts))
    total = q2(sum((p for *_, p in out), Fraction(0))) if W else None
    crit_scores = [{
        "criterion_id": c.id, "verdict": v, "applicable": a, "weight": float(w),
        "effective_weight": round(float(eff), 4), "raw_points": round(float(raw), 4),
        "points": round(float(pts), 4), "max_points": round(float(eff), 4),
    } for c, v, a, w, eff, raw, pts in out]
    sections = []
    for s in cfg.sections:
        rows = [r for r in out if r[0].section == s.id]
        sections.append({"section_id": s.id, "title": s.title,
                         "points": q2(sum((r[6] for r in rows), Fraction(0))),
                         "max_points": q2(sum((r[4] for r in rows), Fraction(0)))})
    flags = [c for c in crits if c.type == "flag" and verdicts[c.id] == "Да"]
    gross_categories = sorted({categories.get(c.id, "") for c in flags} - {""})
    return {"total": total, "criteria": crit_scores, "sections": sections,
            "gross": bool(flags), "gross_categories": gross_categories}


# ---------------------------------------------------------------- сборка DTO

def checklist_versions():
    raw = yaml.safe_load((ROOT / "config" / "checklists" / "freedom-cqa.yaml").read_text(encoding="utf-8"))
    v2 = ChecklistConfig.model_validate(raw).model_dump(mode="json")
    v1 = deepcopy(v2)
    v1["version_note"] = "v1 — чек-лист организаторов"
    for c in v1["criteria"]:
        if c["id"] == "07":
            c["interpretations"] = [i for i in c["interpretations"] if not i.startswith("Т1:")]
            c["examples"] = [e for e in c["examples"] if e["fragment"] != "КЛИЕНТ: Нашла, спасибо. / ОПЕРАТОР: Пожалуйста. Хорошего вечера!"]
        if c["id"] == "02":
            c["interpretations"] = [i for i in c["interpretations"] if not i.startswith("Т2б")]
    ChecklistConfig.model_validate(v1)
    return [
        {"id": 1, "checklist_id": "freedom-cqa", "version": 1, "status": "published", "config": v1,
         "comment": "Импорт чек-листа организаторов", "created_at": "2026-11-07T18:30:00Z", "is_active": False},
        {"id": 2, "checklist_id": "freedom-cqa", "version": 2, "status": "published", "config": v2,
         "comment": "Трактовки Т1 (вежливое прощание после решения) и Т2б по итогам апелляции #1",
         "created_at": "2026-11-07T19:55:12Z", "is_active": True},
    ]


def main() -> None:
    dev = ROOT / "data" / "dev.json"
    if not dev.exists():
        sys.exit("нет data/dev.json")
    records = {r["conversation_id"]: r for r in json.loads(dev.read_text(encoding="utf-8"))["data"]["records"]}
    events = load_events()
    versions = checklist_versions()
    cfg_v1 = ChecklistConfig.model_validate(versions[0]["config"])  # оценки сделаны версией 1
    titles = {c.id: c for c in cfg_v1.criteria}

    conversations, evaluations, summaries, orig_scores = {}, {}, [], {}
    appeals_by_key = {(a["ext"], a["cid"]): a for a in APPEALS}

    for ext in IDS:
        rec = records[ext]
        uid = f"{GROUP}:{ext}"
        msgs, facts = normalize(rec, events)
        by_mid = {m["mid"]: m for m in msgs}
        conv = ConversationDTO.model_validate({
            "uid": uid, "group_id": GROUP, "external_id": ext, "channel": rec["channel"], "language": rec["language"],
            "operator_name": OPERATOR[ext], "has_audio": bool(rec.get("audio_url")), "audio_url": None,
            "messages": msgs, "facts": facts,
        })
        conversations[uid] = conv.model_dump(mode="json")

        spec = EVALS[ext]
        for cid, r in spec.items():
            for ev in r["evidence"]:
                assert ev["quote"] in by_mid[ev["id"]]["text"], (ext, cid, ev)
        verdicts = {cid: r["verdict"] for cid, r in spec.items()}
        cats = {cid: r.get("category", "") for cid, r in spec.items()}
        final_verdicts = dict(verdicts)
        for (aext, acid), a in appeals_by_key.items():
            if aext == ext and a["status"] == "decided" and a["decision"]["action"] == "change":
                final_verdicts[acid] = a["decision"]["new_verdict"]
        orig = score(cfg_v1, verdicts, cats)
        fin = score(cfg_v1, final_verdicts, cats)
        orig_scores[uid] = orig
        eid = EVAL_ID[ext]
        crit_rows = []
        for i, c in enumerate(cfg_v1.criteria, 1):
            r = spec[c.id]
            ap = appeals_by_key.get((ext, c.id))
            crit_rows.append({
                "id": eid * 10 + i, "criterion_id": c.id, "title": c.title, "section": c.section, "type": c.type,
                "weight": c.weight, "verdict": r["verdict"], "final_verdict": final_verdicts[c.id],
                "category": r.get("category", ""),
                "score": next(s for s in fin["criteria"] if s["criterion_id"] == c.id),
                "evidence": r["evidence"], "checked_range": r.get("checked_range"),
                "explanation": r["explanation"], "reasoning": r.get("reasoning", ""), "how_to_fix": r.get("how_to_fix", ""),
                "needs_review": r.get("needs_review", False), "second_opinion": r.get("second_opinion"),
                "status": "done", "error": None,
                "appeal_id": ap["id"] if ap else None, "appeal_status": ap["status"] if ap else None,
            })
        n_calls = len(crit_rows) + sum(1 for r in crit_rows if r["needs_review"])
        ev = EvaluationDTO.model_validate({
            "id": eid, "uid": uid, "run_id": 1, "checklist_version_id": 1, "checklist_version": 1,
            "profile": "gemini-flash", "model": "gemini-3.8-flash", "prompt_version": "p1-3fa9c2d1", "status": "done",
            "total": orig["total"], "final_total": fin["total"], "sections": fin["sections"], "original_sections": orig["sections"],
            "gross": orig["gross"], "gross_categories": orig["gross_categories"],
            "needs_review": any(r["needs_review"] for r in crit_rows), "criteria": crit_rows,
            "cost_usd": round(0.0011 * n_calls + 0.0002 * len(msgs) / 10, 4),
            "latency_ms": 4200 + 310 * len(msgs) // 4, "n_llm_calls": n_calls,
            "created_at": "2026-11-07T19:12:%02dZ" % (10 + IDS.index(ext) * 7),
        })
        evaluations[uid] = ev.model_dump(mode="json")
        summaries.append(ConversationSummaryDTO.model_validate({
            "uid": uid, "group_id": GROUP, "external_id": ext, "channel": rec["channel"], "language": rec["language"],
            "operator_name": OPERATOR[ext], "n_messages": len(msgs), "has_audio": conv.has_audio, "evaluation_id": eid,
            "total": orig["total"], "final_total": fin["total"], "gross": orig["gross"],
            "gross_categories": orig["gross_categories"], "needs_review": ev.needs_review,
            "open_appeals": sum(1 for a in APPEALS if a["ext"] == ext and a["status"] == "open"), "status": "done",
        }).model_dump(mode="json"))

    appeals = []
    for a in APPEALS:
        uid = f"{GROUP}:{a['ext']}"
        ev = evaluations[uid]
        cr = next(c for c in ev["criteria"] if c["criterion_id"] == a["cid"])
        conv = conversations[uid]
        mids = [e["id"] for e in cr["evidence"]] + list(cr["checked_range"] or []) + a["counter"]
        idxs = [int(m[1:]) for m in mids]
        lo, hi = max(1, min(idxs) - 3), max(idxs) + 3
        original = deepcopy(cr)
        original["final_verdict"] = cr["verdict"]
        # исходная оценка — баллы по исходному вердикту, до решения
        original["score"] = next(x for x in orig_scores[uid]["criteria"] if x["criterion_id"] == a["cid"])
        decision = a.get("decision")
        prev_total = new_total = None
        if decision:
            prev_total, new_total = ev["total"], ev["final_total"]
            decision = {**decision, "prev_final_total": prev_total, "new_final_total": new_total}
        appeals.append(AppealDTO.model_validate({
            "id": a["id"], "evaluation_id": ev["id"], "uid": uid, "criterion_id": a["cid"], "criterion_title": titles[a["cid"]].title,
            "comment": a["comment"], "counter_evidence": a["counter"], "status": a["status"], "created_at": a["created_at"],
            "original": original, "fragment": [m for m in conv["messages"] if lo <= m["idx"] <= hi],
            "ai_opinion": a.get("ai_opinion"), "decision": decision,
            "prev_final_total": prev_total, "new_final_total": new_total,
        }).model_dump(mode="json"))

    runs = [RunDTO.model_validate(r).model_dump(mode="json") for r in [
        {"id": 1, "kind": "batch", "status": "done", "checklist_version_id": 1, "judge_profile": "gemini-flash",
         "model": "gemini-3.8-flash", "units_total": 40, "units_done": 40, "units_failed": 0, "units_cached": 0,
         "cost_usd": round(sum(e["cost_usd"] for e in evaluations.values()), 4), "eta_sec": None,
         "created_at": "2026-11-07T19:11:58Z", "started_at": "2026-11-07T19:12:00Z", "finished_at": "2026-11-07T19:13:12Z"},
        {"id": 2, "kind": "batch", "status": "done", "checklist_version_id": 2, "judge_profile": "gemini-flash",
         "model": "gemini-3.8-flash", "units_total": 10, "units_done": 9, "units_failed": 1, "units_cached": 0,
         "cost_usd": 0.0112, "eta_sec": None, "metrics": {"criteria": ["02", "07"]},
         "created_at": "2026-11-07T19:58:40Z", "started_at": "2026-11-07T19:58:41Z", "finished_at": "2026-11-07T19:59:02Z"},
    ]]

    health = HealthDTO.model_validate({"ok": True, "db": True, "roles": {"judge": "gemini-flash", "cross_check": "gemini-argon"},
                                       "keys": {"GEMINI_API_KEY": True}, "active_checklist_version_id": 2}).model_dump(mode="json")
    for v in versions:
        ChecklistVersionDTO.model_validate(v)
    datasets = [{"group_id": GROUP, "source": "file", "fetched_at": "2026-11-07T19:05:00Z", "total_records": len(IDS)}]

    files = {
        "health": health, "datasets": datasets, "conversations": summaries, "conversation-details": conversations,
        "evaluations": evaluations, "appeals": appeals, "checklist-versions": versions, "runs": runs,
    }
    for name, data in files.items():
        text = json.dumps(data, ensure_ascii=False, indent=1)
        assert "sig=" not in text
        (OUT / f"{name}.json").write_text(text + "\n", encoding="utf-8")
    for s in summaries:
        print(f"{s['external_id']:10} total={s['total']} final={s['final_total']} gross={s['gross_categories']}")


if __name__ == "__main__":
    main()
