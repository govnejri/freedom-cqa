"""Проверка ответа судьи кодом (docs/implementation.md §5, таблица проверок).

Жёсткие ошибки (errors) → один повтор с их списком; мягкие (warnings) — только отметка.
Валидатор сам чинит то, что чинится однозначно (repaired): вид ID (M12, 12 → m12), ID по найденной цитате,
синонимы вердикта (да → Да, 20.0 → 20), лишние category/how_to_fix, перевёрнутый checked_range.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from cqa.checklist.model import Criterion
from cqa.models import Conversation, Evidence, Message

FUZZY_MIN = 85          # rapidfuzz.partial_ratio для цитаты
SHORT_QUOTE = 8         # короче (после нормализации) — только точная подстрока

_QUOTES = re.compile(r"[«»\"'“”„‟‹›`’‘]")
_PUNCT = re.compile(r"[.,!?;:()\[\]…—–\-]+")
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"…|\.\.\.")
_ID = re.compile(r"[\[\(\s]*[mMмМ]?\s*(\d+)[\]\)\s:.,]*")

_VERDICT_SYNONYMS = {
    "да": "Да", "yes": "Да", "иә": "Да", "true": "Да",
    "нет": "Нет", "no": "Нет", "жоқ": "Нет", "false": "Нет",
    "n/a": "N/A", "na": "N/A", "н/д": "N/A", "н/п": "N/A", "не применимо": "N/A", "неприменимо": "N/A",
}
ROLE_LABEL = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА"}


class ValidationReport(BaseModel):
    errors: list[str] = Field(default_factory=list)      # жёсткие
    warnings: list[str] = Field(default_factory=list)    # мягкие
    repaired: list[str] = Field(default_factory=list)    # что исправлено автоматически
    # нормализованный ответ
    verdict: str = ""
    verdict_ok: bool = False
    category: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    checked_range: tuple[str, str] | None = None
    reasoning: str = ""
    explanation: str = ""
    how_to_fix: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------- нормализация ----------

def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold().replace("ё", "е")
    s = _QUOTES.sub("", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def normalize_id(raw: Any) -> str | None:
    """m12 / M12 / 12 / «[m12]» → "m12"; не похоже на ID → None."""
    if raw is None:
        return None
    m = _ID.fullmatch(str(raw))
    return f"m{int(m.group(1))}" if m else None


def quote_score(quote: str, text: str) -> float:
    """0..100: насколько цитата находится в тексте. Многоточие в цитате — пропуск: каждая часть ищется отдельно."""
    parts = [normalize_text(p) for p in _ELLIPSIS.split(quote or "")]
    parts = [p for p in parts if len(p) >= 3] or [normalize_text(quote)]
    tn = normalize_text(text)
    scores = []
    for qn in parts:
        if not qn:
            scores.append(0.0)
        elif qn in tn:
            scores.append(100.0)
        elif len(qn) < SHORT_QUOTE or not tn:
            scores.append(0.0)
        else:
            scores.append(float(fuzz.partial_ratio(qn, tn)))
    return min(scores) if scores else 0.0


def find_quote(quote: str, conv: Conversation, roles: list[str], near_idx: int | None = None) -> Message | None:
    """Реплика, где есть цитата: лучший балл; при равенстве — роль из roles, не удержание, ближе к near_idx."""
    best: tuple | None = None
    found: Message | None = None
    for m in conv.messages:
        sc = quote_score(quote, m.text)
        if sc < FUZZY_MIN:
            continue
        key = (-sc, m.speaker not in roles, m.segment == "hold", m.dup_of is not None,
               abs(m.idx - near_idx) if near_idx else 0)
        if best is None or key < best:
            best, found = key, m
    return found


def _norm_verdict(raw: Any, crit: Criterion) -> str:
    v = str(raw if raw is not None else "").strip().strip("«»\"'.").strip()
    allowed = crit.allowed_verdicts()
    if v in allowed:
        return v
    syn = _VERDICT_SYNONYMS.get(v.casefold())
    if syn in allowed:
        return syn
    if crit.type == "scale":
        try:
            n = float(v.replace(",", "."))
            if n.is_integer() and str(int(n)) in allowed:
                return str(int(n))
        except ValueError:
            pass
    return v


def _roles_text(roles: list[str]) -> str:
    return ", ".join(ROLE_LABEL.get(r, r.upper()) for r in roles)


def is_violation(crit: Criterion, verdict: str) -> bool:
    """Нарушение критерия: «Нет», ступень ниже верхней; у флага — «Да»."""
    if crit.type == "flag":
        return verdict == "Да"
    if crit.type == "scale":
        return verdict not in ("N/A", str(crit.top)) and verdict.lstrip("-").isdigit()
    return verdict == "Нет"


def is_positive(crit: Criterion, verdict: str) -> bool:
    """«Да» или верхняя ступень — нужно доказательство."""
    if crit.type == "scale":
        return verdict == str(crit.top)
    return verdict == "Да"


# ---------- проверка ----------

def validate(out: dict[str, Any] | None, crit: Criterion, conv: Conversation) -> ValidationReport:
    rep = ValidationReport()
    if not isinstance(out, dict):
        rep.errors.append("ответ не является JSON-объектом по схеме")
        return rep

    required = ["evidence", "checked_range", "reasoning", "verdict", "explanation", "how_to_fix"]
    if crit.type == "flag":
        required.insert(4, "category")
    missing = [k for k in required if k not in out]
    if missing:
        rep.errors.append("нет обязательных полей: " + ", ".join(missing))

    rep.reasoning = str(out.get("reasoning") or "").strip()
    rep.explanation = str(out.get("explanation") or "").strip()
    rep.how_to_fix = str(out.get("how_to_fix") or "").strip()

    # вердикт
    allowed = crit.allowed_verdicts()
    raw_v = out.get("verdict")
    v = _norm_verdict(raw_v, crit)
    if v in allowed:
        rep.verdict, rep.verdict_ok = v, True
        if str(raw_v) != v:
            rep.repaired.append(f"вердикт {raw_v!r} → «{v}»")
    else:
        rep.verdict = str(raw_v or "")
        hint = " (N/A для этого критерия не допускается)" if v == "N/A" else ""
        rep.errors.append(f"вердикт «{raw_v}» недопустим{hint}; допустимо: " + ", ".join(f"«{a}»" for a in allowed))

    # категория флага
    if crit.type == "flag":
        cat = str(out.get("category") or "").strip()
        if rep.verdict == "Да":
            match = next((c for c in crit.flag_categories if c.casefold() == cat.casefold()), None)
            if match is None:
                rep.errors.append("при «Да» укажи category — одна из: " + ", ".join(f"«{c}»" for c in crit.flag_categories))
            else:
                rep.category = match
        elif cat:
            rep.repaired.append(f"category «{cat}» убрана: нарушения нет")

    # доказательства
    by_mid = conv.by_mid()
    roles = list(crit.evidence_roles)
    raw_ev = out.get("evidence") or []
    if not isinstance(raw_ev, list):
        rep.errors.append("evidence должен быть массивом")
        raw_ev = []
    seen: set[tuple[str, str]] = set()
    for i, item in enumerate(raw_ev, 1):
        if isinstance(item, dict):
            rid, quote = item.get("id"), str(item.get("quote") or "").strip()
        else:
            rid, quote = item, ""
        mid = normalize_id(rid)
        msg = by_mid.get(mid) if mid else None
        ev: Evidence
        if msg is None:
            found = find_quote(quote, conv, roles) if quote else None
            if found is not None:
                ev = Evidence(id=found.mid, quote=quote, repaired=True)
                rep.repaired.append(f"доказательство {i}: реплики {rid!r} нет, цитата найдена в {found.mid}")
                rep.warnings.append(f"доказательство {i}: ID {rid!r} исправлен на {found.mid}")
                msg = found
            else:
                rep.errors.append(f"доказательство {i}: реплики {rid!r} нет в диалоге")
                rep.evidence.append(Evidence(id=str(rid), quote=quote, ok=False))
                continue
        else:
            ev = Evidence(id=msg.mid, quote=quote)
            if str(rid) != msg.mid:
                rep.repaired.append(f"доказательство {i}: ID {rid!r} → {msg.mid}")
            if not quote:
                rep.warnings.append(f"доказательство {i} ({msg.mid}): пустая цитата")
            elif quote_score(quote, msg.text) < FUZZY_MIN:
                found = find_quote(quote, conv, roles, near_idx=msg.idx)
                if found is not None:
                    rep.repaired.append(f"доказательство {i}: цитата из {found.mid}, а не из {msg.mid}")
                    rep.warnings.append(f"доказательство {i}: ID {msg.mid} исправлен на {found.mid} по цитате")
                    ev = Evidence(id=found.mid, quote=quote, repaired=True)
                    msg = found
                else:
                    ev.ok = False
                    rep.warnings.append(f"доказательство {i} ({msg.mid}): цитата «{quote[:120]}» не найдена "
                                        "ни в этой, ни в других репликах — копируй текст дословно")
        if msg.segment == "hold" and msg.speaker != "system":
            ev.ok = False
            rep.errors.append(f"доказательство {i} ({msg.mid}): реплика с участка удержания — клиент её не слышал")
        key = (ev.id, normalize_text(ev.quote))
        if key in seen:
            continue
        seen.add(key)
        rep.evidence.append(ev)

    # проверенный участок
    cr = out.get("checked_range")
    a = b = None
    if isinstance(cr, dict):
        a, b = cr.get("from"), cr.get("to")
    elif isinstance(cr, (list, tuple)) and len(cr) == 2:
        a, b = cr
    a = str(a).strip() if a not in (None, "") else ""
    b = str(b).strip() if b not in (None, "") else ""
    if a or b:
        na, nb = normalize_id(a or b), normalize_id(b or a)
        if na in by_mid and nb in by_mid:
            if by_mid[na].idx > by_mid[nb].idx:
                na, nb = nb, na
                rep.repaired.append("checked_range: from и to переставлены")
            rep.checked_range = (na, nb)
        else:
            rep.warnings.append(f"checked_range: реплик {a!r}–{b!r} нет в диалоге, участок не учтён")

    # роли и наличие доказательств
    if rep.verdict_ok and rep.verdict != "N/A":
        valid = [e for e in rep.evidence if e.ok]
        by_role = [e for e in valid if by_mid.get(e.id) and by_mid[e.id].speaker in roles]
        if is_positive(crit, rep.verdict):
            if not valid:
                rep.errors.append(f"вердикт «{rep.verdict}» требует хотя бы одного доказательства с дословной цитатой")
            elif not by_role:
                rep.errors.append(f"среди доказательств нет реплик ролей: {_roles_text(roles)}")
        else:
            if not by_role and rep.checked_range is None:
                if valid:
                    rep.errors.append(f"среди доказательств нет реплик ролей: {_roles_text(roles)}; "
                                      "укажи такую реплику или checked_range")
                else:
                    rep.errors.append(f"вердикт «{rep.verdict}» требует доказательства или checked_range "
                                      "(проверенного участка)")
    if rep.verdict_ok and rep.verdict == "N/A" and not rep.explanation:
        rep.errors.append("для N/A заполни explanation: какое условие неприменимости выполнено")
    elif rep.verdict_ok and not rep.explanation:
        rep.warnings.append("пустое explanation")

    # как надо было — только при нарушении
    if rep.verdict_ok:
        if is_violation(crit, rep.verdict):
            if not rep.how_to_fix:
                rep.warnings.append("нарушение без how_to_fix")
        elif rep.how_to_fix:
            rep.repaired.append("how_to_fix убран: нарушения нет")
            rep.how_to_fix = ""
    return rep
