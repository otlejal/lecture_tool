"""Сборка HTML из шаблона: данные вставляются в метку {{DATA}} как JSON."""
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from app.config import ROOT

TEMPLATES_DIR = ROOT / "templates"
USER_TEMPLATES_DIR = TEMPLATES_DIR / "user"
PLACEHOLDER = "{{DATA}}"
KINDS = ("summary", "quiz", "slides")
NAME_PATTERN = re.compile(r"[\w\-]+")


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"неизвестный тип шаблона: {kind}")


def template_path(kind: str, name: str) -> Path:
    _check_kind(kind)
    if name == "default":
        return TEMPLATES_DIR / f"{kind}_default.html"
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("имя шаблона может содержать только буквы, цифры, «_» и «-»")
    return USER_TEMPLATES_DIR / f"{kind}_{name}.html"


def list_templates(kind: str) -> list[str]:
    _check_kind(kind)
    names = ["default"]
    if USER_TEMPLATES_DIR.exists():
        for path in sorted(USER_TEMPLATES_DIR.glob(f"{kind}_*.html")):
            names.append(path.stem[len(kind) + 1:])
    return names


def load_template(kind: str, name: str) -> str:
    path = template_path(kind, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8-sig")


def save_user_template(kind: str, name: str, content: str) -> None:
    if name == "default":
        raise ValueError("имя «default» зарезервировано")
    if PLACEHOLDER not in content:
        raise ValueError(f"в шаблоне нет метки {PLACEHOLDER}, куда вставятся данные")
    USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    template_path(kind, name).write_text(content, encoding="utf-8")


def render_html(template: str, data: dict) -> str:
    if PLACEHOLDER not in template:
        raise ValueError(f"в шаблоне нет метки {PLACEHOLDER}")
    # Заменяем "<" на \u003c: JSON остаётся валидным, а текст не может закрыть тег <script>
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace(PLACEHOLDER, payload)


def _flatten(value) -> str:
    if isinstance(value, dict):
        return "\n".join(_flatten(v) for v in value.values() if v not in (None, ""))
    if isinstance(value, list):
        return "\n".join(_flatten(v) for v in value)
    return str(value)


def html_to_text(html: str) -> str:
    """Достаёт текст из HTML. Для наших файлов берёт данные из блока <script id="data">."""
    soup = BeautifulSoup(html, "html.parser")
    data_tag = soup.find("script", id="data")
    if data_tag and data_tag.string:
        try:
            return _flatten(json.loads(data_tag.string))
        except json.JSONDecodeError:
            pass
    for tag in soup(["script", "style"]):
        tag.decompose()
    body = soup.body or soup
    lines = (line.strip() for line in body.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)
