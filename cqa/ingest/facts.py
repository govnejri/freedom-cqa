"""Факты для судьи (`cqa.models.Facts`) по нормализованному диалогу. Считаются кодом, без LLM."""
from __future__ import annotations

from cqa.ingest.normalize import EventRules, UNKNOWN_EVENT, find_landmarks, load_event_rules
from cqa.models import Conversation, Ending, Facts, Message, Wait

WAIT_THRESHOLD_SEC = 60.0


def _mid(msgs: list[Message], i: int | None) -> str | None:
    return msgs[i].mid if i is not None else None


def _gap(a: Message, b: Message) -> float | None:
    if a.offset_sec is None or b.offset_sec is None:
        return None
    return round(b.offset_sec - a.offset_sec, 3)


def find_waits(msgs: list[Message], threshold: float = WAIT_THRESHOLD_SEC) -> list[Wait]:
    """Паузы > threshold между репликой клиента и следующей репликой оператора вне hold.

    Считаются только в участке `operator` (ожидание первого ответа — это first_reaction_sec, а не пауза).
    Берётся последняя реплика клиента перед ответом: серия реплик клиента — это его монолог, а не ожидание.
    Если между ними было удержание, пауза не считается.
    """
    waits: list[Wait] = []
    pending: Message | None = None
    prev_op_for_pending: str | None = None
    last_op: str | None = None
    crossed_hold = False
    for m in msgs:
        if m.dup_of:
            continue
        if m.segment == "hold":
            crossed_hold = True
            continue
        if m.speaker == "client":
            if m.segment == "operator" and not m.after_end:
                pending, prev_op_for_pending, crossed_hold = m, last_op, False
            continue
        if m.speaker == "operator" and m.segment == "operator":
            if pending is not None and not crossed_hold and not m.after_end:
                gap = _gap(pending, m)
                if gap is not None and gap > threshold:
                    waits.append(Wait(after_mid=pending.mid, operator_mid=m.mid, seconds=round(gap, 1),
                                      prev_operator_mid=prev_op_for_pending))
            pending, crossed_hold = None, False
            last_op = m.mid
    return waits


def find_ending(msgs: list[Message], ending_idx: int | None, rules: EventRules) -> Ending:
    scope = msgs[:ending_idx] if ending_idx is not None else msgs
    talk = [m for m in scope if not m.dup_of and m.segment != "hold"]
    last_client_i = next((i for i in range(len(talk) - 1, -1, -1) if talk[i].speaker == "client"), None)
    last_op_i = next((i for i in range(len(talk) - 1, -1, -1) if talk[i].speaker == "operator"), None)
    last_client = talk[last_client_i] if last_client_i is not None else None
    last_op = talk[last_op_i] if last_op_i is not None else None

    if ending_idx is None:
        return Ending(type=None, event_mid=None,
                      last_operator_mid=last_op.mid if last_op else None,
                      last_client_mid=last_client.mid if last_client else None)

    event = msgs[ending_idx]
    etype = event.event_type
    answered = last_client_i is not None and last_op_i is not None and last_op_i > last_client_i
    # клиент ни разу не написал оператору (только боту) и ушёл — оператор не мог завершить (p1-ru-025)
    client_engaged = any(m.speaker == "client" and m.segment in ("waiting", "operator") for m in talk)
    abrupt = etype in rules.abrupt_ending_types and (not answered or not client_engaged)
    return Ending(
        type=etype,
        event_mid=event.mid,
        last_operator_mid=last_op.mid if last_op else None,
        last_client_mid=last_client.mid if last_client else None,
        abrupt=abrupt,
        seconds_since_last_client=_gap(last_client, event) if last_client else None,
    )


def build_facts(conv: Conversation, rules: EventRules | None = None) -> Facts:
    rules = rules or load_event_rules()
    msgs = conv.messages
    lm = find_landmarks(msgs, rules)

    first_reaction = None
    if lm.escalation is not None and lm.first_operator is not None:
        first_reaction = _gap(msgs[lm.escalation], msgs[lm.first_operator])

    hold = [m.mid for m in msgs if m.segment == "hold"]
    system = [m for m in msgs if m.speaker == "system"]
    return Facts(
        channel=conv.channel,
        language=conv.language,
        first_operator_mid=_mid(msgs, lm.first_operator),
        escalation_mid=_mid(msgs, lm.escalation),
        operator_joined_mid=_mid(msgs, lm.operator_joined),
        first_reaction_sec=first_reaction,
        client_msgs_before_operator=[m.mid for m in msgs
                                     if m.speaker == "client" and not m.dup_of and m.segment in ("bot", "waiting")],
        waits=find_waits(msgs),
        id_events=[m.mid for m in system if m.event_type in rules.identity_types],
        hold_range=(hold[0], hold[-1]) if hold else None,
        ending=find_ending(msgs, lm.ending, rules),
        after_end_mids=[m.mid for m in msgs if m.after_end],
        unknown_events=[m.mid for m in system if m.event_type == UNKNOWN_EVENT],
    )
