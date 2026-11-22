"""Мастер-промпты: пользовательские шаблоны промптов для конспекта, теста и презентации.

Мастер-промпт — шаблон, по которому собирается запрос к языковой модели.
Метки в шаблоне: {{RULES}} — блок правил, собранный из настроек блока;
{{TEXT}} — текст источника (транскрипт, конспект или вставленный текст).
Хранятся в prompts.json рядом с config.yaml; файл не коммитится.
"""
import json
import threading
import uuid
from typing import Literal

from pydantic import BaseModel

from app.config import ROOT

PROMPTS_PATH = ROOT / "prompts.json"
_lock = threading.Lock()


class MasterPrompt(BaseModel):
    id: str
    kind: Literal["summary", "quiz", "slides"]
    name: str
    template: str


def _load() -> list[MasterPrompt]:
    if not PROMPTS_PATH.exists():
        return []
    data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8-sig"))
    return [MasterPrompt.model_validate(item) for item in data]


def _save(items: list[MasterPrompt]) -> None:
    PROMPTS_PATH.write_text(
        json.dumps([i.model_dump() for i in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _check_template(template: str) -> str:
    if "{{TEXT}}" not in template:
        raise ValueError("В шаблоне промпта должна быть метка {{TEXT}} — на её место подставляется текст темы")
    return template.strip()


def list_prompts(kind: str) -> list[MasterPrompt]:
    with _lock:
        return [p for p in _load() if p.kind == kind]


def get(prompt_id: str) -> MasterPrompt | None:
    with _lock:
        return next((p for p in _load() if p.id == prompt_id), None)


def create(kind: str, name: str, template: str) -> MasterPrompt:
    prompt = MasterPrompt(id=uuid.uuid4().hex, kind=kind, name=name.strip(),
                          template=_check_template(template))
    with _lock:
        items = _load()
        items.append(prompt)
        _save(items)
    return prompt


def update(prompt_id: str, name: str, template: str) -> MasterPrompt:
    with _lock:
        items = _load()
        for prompt in items:
            if prompt.id == prompt_id:
                prompt.name = name.strip()
                prompt.template = _check_template(template)
                _save(items)
                return prompt
    raise KeyError("Мастер-промпт не найден")


def delete(prompt_id: str) -> bool:
    with _lock:
        items = _load()
        remaining = [p for p in items if p.id != prompt_id]
        if len(remaining) == len(items):
            return False
        _save(remaining)
    return True


def template_for(prompt_id: str) -> str:
    """Шаблон выбранного мастер-промпта; пустая строка — используется встроенный."""
    prompt = get(prompt_id)
    return prompt.template if prompt else ""


def apply_template(template: str, rules: str, text: str) -> str:
    """Собирает итоговый промпт: подставляет правила и текст вместо меток."""
    return template.replace("{{RULES}}", rules).replace("{{TEXT}}", text)
