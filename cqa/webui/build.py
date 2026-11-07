"""Сборка фронтенда в один HTML-файл (веб-ресурс Dynamics 365).

`frontend/index.html` → `build/cqa.html`:
- `<link rel="stylesheet" href="…">` → `<style>` с содержимым файла;
- `<script src="…"></script>` → `<script>` с содержимым файла (классические скрипты, порядок сохраняется);
- `frontend/mock/*.json` → `<script type="application/json" id="mock-…">`, чтобы сборка работала и без бэкенда.

Внешние ссылки (http/https, //) не допускаются: сборка падает с ошибкой.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
OUT = ROOT / "build" / "cqa.html"

_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_SCRIPT_RE = re.compile(r"<script\b([^>]*)\bsrc\s*=\s*[\"']([^\"']+)[\"']([^>]*)>\s*</script>", re.I)
_HREF_RE = re.compile(r"\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_REL_RE = re.compile(r"\brel\s*=\s*[\"']?stylesheet", re.I)
_EXTERNAL = re.compile(r"^(?:[a-z]+:)?//", re.I)


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    path: Path
    size: int
    styles: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    mocks: list[str] = field(default_factory=list)


def _read(src: Path, rel: str) -> str:
    if _EXTERNAL.match(rel):
        raise BuildError(f"внешний ресурс запрещён: {rel}")
    p = (src / rel).resolve()
    if src.resolve() not in p.parents:
        raise BuildError(f"ресурс вне frontend/: {rel}")
    if not p.is_file():
        raise BuildError(f"нет файла: {rel}")
    return p.read_text(encoding="utf-8")


def _safe_script(js: str) -> str:
    # закрывающий тег внутри кода оборвал бы <script>
    return re.sub(r"</(script)", r"<\\/\1", js, flags=re.I)


def _safe_style(css: str) -> str:
    return re.sub(r"</(style)", r"<\\/\1", css, flags=re.I)


def _mock_tags(src: Path, names: list[str]) -> str:
    tags = []
    for p in sorted((src / "mock").glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        # компактно; «</» экранируется как «<\/» — валидный JSON и безопасно внутри <script>
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        tags.append(f'<script type="application/json" id="mock-{p.stem}">{text}</script>')
        names.append(p.stem)
    return "\n".join(tags)


def build(src: Path = FRONTEND, out: Path = OUT, *, with_mock: bool = True) -> BuildResult:
    html = (src / "index.html").read_text(encoding="utf-8")
    res = BuildResult(path=out, size=0)

    def link(m: re.Match) -> str:
        tag = m.group(0)
        if not _REL_RE.search(tag):
            href = _HREF_RE.search(tag)
            if href and _EXTERNAL.match(href.group(1)):
                raise BuildError(f"внешняя ссылка запрещена: {href.group(1)}")
            return tag
        href = _HREF_RE.search(tag)
        if not href:
            return tag
        rel = href.group(1)
        res.styles.append(rel)
        return f"<style>/* {rel} */\n{_safe_style(_read(src, rel))}\n</style>"

    def script(m: re.Match) -> str:
        rel = m.group(2)
        attrs = (m.group(1) + m.group(3)).strip()
        if re.search(r"\btype\s*=\s*[\"']?module", attrs, re.I):
            raise BuildError(f"ES-модули не поддерживаются сборкой: {rel}")
        res.scripts.append(rel)
        return f"<script>/* {rel} */\n{_safe_script(_read(src, rel))}\n</script>"

    html = _LINK_RE.sub(link, html)
    html = _SCRIPT_RE.sub(script, html)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = f'<meta name="cqa-build" content="{stamp}">'
    html = html.replace("</head>", f"  {meta}\n</head>", 1)
    if with_mock:
        mocks = _mock_tags(src, res.mocks)
        if mocks:
            # мок-данные — до первого скрипта приложения
            idx = html.find("<script>")
            html = html[:idx] + mocks + "\n" + html[idx:] if idx >= 0 else html.replace("</body>", mocks + "\n</body>", 1)

    if re.search(r"<script\b[^>]*\bsrc\s*=", html, re.I):
        raise BuildError("остался внешний <script src>")
    if "sig=" in html:
        raise BuildError("в сборке найдена подписанная ссылка (sig=)")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    res.size = out.stat().st_size
    return res


if __name__ == "__main__":
    r = build()
    print(f"{r.path} ({r.size / 1024:.0f} КБ): {len(r.styles)} css, {len(r.scripts)} js, мок: {', '.join(r.mocks)}")
