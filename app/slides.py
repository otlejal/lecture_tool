"""Презентация по конспекту: структура слайдов в JSON, проверка схемы и правил вёрстки.

ИИ не пишет HTML целиком: он возвращает структуру (заголовки, тезисы, цифры, цитаты),
а шаблон `templates/slides_default.html` собирает из неё готовую колоду с навигацией,
анимацией появления и подгонкой под экран. Так результат предсказуем даже на небольшой
локальной модели: содержание делает модель, механику показа — проверенный код.

Правила в блоке `build_rules` повторяют ключевые требования скилла frontend-slides:
плотность слайда, запрет «стен текста», один смысл на слайд, лимиты на код и списки.
"""
from typing import Callable

from pydantic import BaseModel, Field, model_validator

from app.llm import LLMClient
from app.prompts import apply_template

# Оформление: ключ → как его описать модели. Ключи используются как data-theme в шаблоне.
THEMES: dict[str, str] = {
    "editorial": "тёплая бумага, крупная антиква, тёмные чернила, один красный акцент",
    "midnight": "почти чёрный фон, холодный неоновый акцент, геометрический гротеск",
    "nordic": "светлый прохладный фон, много воздуха, сдержанный синий акцент",
    "bold": "чёрный фон, кислотный акцент, очень крупная типографика",
    "academic": "спокойный белый и синий, как конспект, строгая сетка",
}

DEFAULT_THEME = "editorial"

# Раскладки слайда: ключ → инструкция модели.
LAYOUTS: dict[str, str] = {
    "title": "титульный: heading — название презентации, subheading — подзаголовок",
    "section": "разделитель: heading — название раздела, subheading — короткое пояснение",
    "bullets": "обычный слайд: heading плюс 4-6 коротких пунктов в bullets",
    "statement": "одна мысль крупным шрифтом: heading — сама мысль, до 12 слов",
    "quote": "цитата: quote и author, heading необязателен",
    "stats": "цифры: heading плюс 2-4 элемента stats вида {value, label}",
    "compare": "сравнение: ровно 2 колонки columns вида {heading, points} по 2-4 пункта",
    "code": "фрагмент кода: heading и code не длиннее 8 строк",
}

MAX_BULLETS = 6
MAX_STATS = 4
MAX_COLUMNS = 2
MAX_COLUMN_POINTS = 4
MAX_CODE_LINES = 8

SCHEMA_HINT = """{
  "title": "название презентации",
  "subtitle": "подзаголовок для титульного слайда",
  "theme": "editorial | midnight | nordic | bold | academic",
  "slides": [
    {
      "layout": "title | section | bullets | statement | quote | stats | compare | code",
      "heading": "заголовок слайда",
      "subheading": "подзаголовок или короткое пояснение (можно пустым)",
      "bullets": ["пункт", "пункт"],
      "stats": [{"value": "3x", "label": "пояснение к цифре"}],
      "columns": [{"heading": "колонка", "points": ["пункт"]}],
      "quote": "текст цитаты",
      "author": "кто сказал",
      "code": "фрагмент кода\\nвторая строка",
      "notes": "1-2 предложения для выступающего"
    }
  ]
}"""

SYSTEM_PROMPT = (
    "Ты методист и дизайнер презентаций. Делаешь слайды для лекции на русском языке: "
    "короткие тезисы, один смысл на слайд, без «стен текста». "
    "Отвечай только корректным JSON без пояснений."
)

# Шаблон запроса по умолчанию: {{RULES}} — правила из настроек, {{TEXT}} — текст материала.
DEFAULT_PROMPT_TEMPLATE = "{{RULES}}\n\n{{TEXT}}"

MAX_SOURCE_CHARS_NOTE = "Текст для презентации сокращён до {} символов, так как он слишком длинный."


class SlidesOptions(BaseModel):
    count: int = Field(default=10, ge=3, le=40)
    theme: str = "auto"
    audience: str = ""
    extra_instructions: str = ""


def _clean(text: str) -> str:
    return " ".join(text.split())


def _clean_list(items: list[str], limit: int) -> list[str]:
    cleaned = [_clean(item) for item in items if item and item.strip()]
    return cleaned[:limit]


def normalize_theme(theme: str) -> str:
    theme = (theme or "").strip().lower()
    return theme if theme in THEMES else DEFAULT_THEME


def normalize_layout(layout: str) -> str:
    layout = (layout or "").strip().lower()
    return layout if layout in LAYOUTS else "bullets"


class Column(BaseModel):
    heading: str = ""
    points: list[str] = Field(default_factory=list)


class Stat(BaseModel):
    value: str
    label: str = ""


class Slide(BaseModel):
    layout: str = "bullets"
    heading: str = ""
    subheading: str = ""
    bullets: list[str] = Field(default_factory=list)
    stats: list[Stat] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    quote: str = ""
    author: str = ""
    code: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _normalize(self):
        self.layout = normalize_layout(self.layout)
        self.heading = _clean(self.heading)
        self.subheading = _clean(self.subheading)
        self.bullets = _clean_list(self.bullets, MAX_BULLETS)
        self.stats = self.stats[:MAX_STATS]
        for stat in self.stats:
            stat.value = _clean(stat.value)
            stat.label = _clean(stat.label)
        self.columns = self.columns[:MAX_COLUMNS]
        for column in self.columns:
            column.heading = _clean(column.heading)
            column.points = _clean_list(column.points, MAX_COLUMN_POINTS)
        self.quote = _clean(self.quote)
        self.author = _clean(self.author)
        self.code = "\n".join(self.code.strip().splitlines()[:MAX_CODE_LINES])
        self.notes = _clean(self.notes)

        # Слайд без заголовка выглядит как ошибка вёрстки, поэтому просим модель исправиться
        if not self.heading and not self.quote:
            raise ValueError("у слайда нет заголовка (поле heading)")
        return self


class SlidesData(BaseModel):
    title: str
    subtitle: str = ""
    theme: str = DEFAULT_THEME
    slides: list[Slide] = Field(min_length=1)

    @model_validator(mode="after")
    def _normalize(self):
        self.title = _clean(self.title) or "Презентация"
        self.subtitle = _clean(self.subtitle)
        self.theme = normalize_theme(self.theme)
        return self


def build_rules(opts: SlidesOptions) -> str:
    """Блок инструкций для презентации: собирается из настроек блока."""
    if opts.theme in THEMES:
        theme_text = f"{opts.theme} — {THEMES[opts.theme]}"
    else:
        theme_text = ("выбери сам из списка и укажи выбранный ключ в поле theme: "
                      + "; ".join(f"{key} — {desc}" for key, desc in THEMES.items()))
    parts = [
        "Сделай презентацию к лекции по тексту ниже.",
        f"Слайдов ровно {opts.count}, включая титульный.",
        "Структура: титульный слайд (layout title) → 1-2 разделителя (layout section) → "
        "смысловые блоки → слайд с итогом или выводом.",
        "Каждый слайд — одна мысль. Не переписывай конспект дословно, сокращай до тезисов.",
        f"Плотность: в bullets 4-6 пунктов, каждый до 12 слов; в compare две колонки по 2-4 пункта; "
        f"в stats 2-4 цифры; в code не больше {MAX_CODE_LINES} строк.",
        "Запрещено: длинные списки, абзацы внутри пункта, несколько разных мыслей на одном слайде, "
        "подряд идущие одинаковые раскладки без причины.",
        "В notes — 1-2 предложения: что сказать вслух на этом слайде.",
        f"Тема оформления: {theme_text}.",
    ]
    if opts.audience.strip():
        parts.append(f"Аудитория и цель: {opts.audience.strip()}.")
    if opts.extra_instructions.strip():
        parts.append(f"Пожелания преподавателя: {opts.extra_instructions.strip()}.")
    parts.append("Доступные layout: " + "; ".join(f"{key} — {desc}" for key, desc in LAYOUTS.items()) + ".")
    parts.append(f"Верни JSON строго по схеме:\n{SCHEMA_HINT}")
    return "\n".join(parts)


def _validate(data: dict, count: int) -> SlidesData:
    deck = SlidesData.model_validate(data)
    need = max(3, count)
    if len(deck.slides) < need:
        raise ValueError(f"получено {len(deck.slides)} слайдов вместо {count}")
    deck.slides = deck.slides[:count]
    return deck


def generate_slides(text: str, opts: SlidesOptions, llm: LLMClient,
                    prompt_template: str = "",
                    progress: Callable[[str], None] = lambda _: None) -> SlidesData:
    max_chars = llm.cfg.max_chunk_chars
    if len(text) > max_chars:
        text = text[:max_chars]
        progress(MAX_SOURCE_CHARS_NOTE.format(max_chars))

    rules = build_rules(opts)
    template = prompt_template.strip() or DEFAULT_PROMPT_TEMPLATE
    prompt = apply_template(template, rules, text)
    progress("Придумываю структуру презентации...")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    return llm.chat_json(messages, validate=lambda data: _validate(data, opts.count))
