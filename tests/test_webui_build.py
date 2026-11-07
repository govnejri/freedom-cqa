"""Сборка фронтенда в один HTML: без внешних ресурсов, со всеми компонентами и мок-данными, без sig=."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cqa.webui.build import FRONTEND, OUT, BuildError, build

COMPONENT_RE = re.compile(r"customElements\.define\(\s*['\"]([a-z0-9-]+)['\"]")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, str]:
    """Собирает интерфейс так же, как `cqa build-ui`, но во временный файл: тесты не перезаписывают build/cqa.html,
    который в это время может раздавать запущенный сервер."""
    res = build(out=tmp_path_factory.mktemp("ui") / OUT.name)
    return res.path, res.path.read_text(encoding="utf-8")


def _components() -> list[str]:
    names = []
    for p in sorted((FRONTEND / "components").glob("*.js")):
        names += COMPONENT_RE.findall(p.read_text(encoding="utf-8"))
    return names


def test_no_external_resources(built):
    _, html = built
    # внешние src= (скрипты, картинки, iframe) — запрещены везде
    assert not re.search(r"\bsrc\s*=\s*[\"']?(?:https?:)?//", html, re.I)
    # href= на http(s) допустим только у якорей <a>
    for m in re.finditer(r"<(\w+)\b[^>]*\bhref\s*=\s*[\"']?((?:https?:)?//[^\"'\s>]*)", html, re.I):
        assert m.group(1).lower() == "a", f"внешний href у <{m.group(1)}>: {m.group(2)}"
    assert not re.search(r"<script\b[^>]*\bsrc\s*=", html, re.I), "остался <script src>"
    assert not re.search(r"<link\b[^>]*rel=[\"']?stylesheet", html, re.I), "остался <link rel=stylesheet>"
    assert not re.search(r"@import\s+url\(\s*[\"']?https?:", html, re.I)
    assert "type=\"module\"" not in html


def test_all_components_inlined(built):
    _, html = built
    names = _components()
    assert len(names) >= 15
    for name in names:
        assert f"customElements.define('{name}'" in html, name
    # все скрипты и стили из index.html попали в сборку
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for rel in re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", index):
        assert f"/* {rel} */" in html, rel
    for rel in re.findall(r"<link\b[^>]*href=\"([^\"]+)\"", index):
        assert f"/* {rel} */" in html, rel


def test_no_signed_links(built):
    _, html = built
    assert "sig=" not in html


def test_mock_embedded_as_json(built):
    _, html = built
    for p in (FRONTEND / "mock").glob("*.json"):
        m = re.search(rf'<script type="application/json" id="mock-{re.escape(p.stem)}">(.*?)</script>', html, re.S)
        assert m, p.stem
        assert json.loads(m.group(1)) == json.loads(p.read_text(encoding="utf-8"))
    # мок-данные стоят до кода приложения
    assert html.index('id="mock-') < html.index("/* js/app.js */")


def test_fonts_embedded(built):
    """Onest встроен в HTML как data:-URI (Dynamics не пускает внешние шрифты); кириллица с казахскими буквами — тоже."""
    _, html = built
    fonts = sorted(p.name for p in (FRONTEND / "fonts").glob("*.woff2"))
    assert fonts, "нет woff2 во frontend/fonts"
    assert html.count("data:font/woff2;base64,") == len(fonts)
    assert "onest-cyrillic-ext" not in html          # путь заменён данными, а не оставлен ссылкой
    # в CSS не осталось url() на файлы — только data:
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    for m in re.finditer(r"url\(\s*[\"']?([^\"')]+)", css):
        assert m.group(1).startswith(("data:", "#")), m.group(1)[:80]


def test_build_rejects_external_css_url(tmp_path):
    src = tmp_path / "fe"
    shutil.copytree(FRONTEND, src)
    css = src / "styles" / "fonts.css"
    css.write_text(css.read_text(encoding="utf-8") + '\n@font-face { font-family: X; src: url("https://fonts.example.com/x.woff2"); }\n', encoding="utf-8")
    with pytest.raises(BuildError):
        build(src=src, out=tmp_path / "out.html")


def test_build_rejects_external(tmp_path):
    src = tmp_path / "fe"
    shutil.copytree(FRONTEND, src)
    index = src / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace(
        "</head>", '<script src="https://cdn.example.com/x.js"></script></head>'), encoding="utf-8")
    with pytest.raises(BuildError):
        build(src=src, out=tmp_path / "out.html")


def test_cli_build_ui(tmp_path):
    from typer.testing import CliRunner

    from cqa.cli import app

    out = tmp_path / "cli.html"
    r = CliRunner().invoke(app, ["build-ui", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.stat().st_size > 50_000


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_js_syntax():
    files = sorted((FRONTEND / "js").glob("*.js")) + sorted((FRONTEND / "components").glob("*.js"))
    for p in files:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name}: {r.stderr}"


def test_no_es_modules():
    for p in list((FRONTEND / "js").glob("*.js")) + list((FRONTEND / "components").glob("*.js")):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|export)\s", text, re.M), p.name
