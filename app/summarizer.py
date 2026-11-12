"""Конспект лекции: настройки, промпт, разбиение длинного текста на части (map-reduce)."""
import json
from typing import Callable, Literal

from pydantic import BaseModel, Field

from app.llm import LLMClient

SYSTEM_PROMPT = (
    "Ты помощник преподавателя. Готовишь конспект по расшифровке лекции на русском языке. "
    "Отвечай только корректным JSON без пояснений."
)

SCHEMA_HINT = """{
  "title": "название лекции",
  "overview": "1-3 предложения: о чём лекция",
  "sections": [{"heading": "название раздела", "points": ["пункт", "пункт"]}],
  "terms": [{"term": "термин", "definition": "определение"}]
}"""


class SummaryOptions(BaseModel):
    length: Literal["short", "full"] = "full"
    include_offtopic: bool = False
    remove_filler: bool = True
    include_examples: bool = True
    extra_instructions: str = ""


class SummarySection(BaseModel):
    heading: str
    points: list[str] = Field(default_factory=list)


class TermItem(BaseModel):
    term: str
    definition: str


class Summary(BaseModel):
    title: str
    overview: str = ""
    sections: list[SummarySection] = Field(default_factory=list)
    terms: list[TermItem] = Field(default_factory=list)


def build_rules(opts: SummaryOptions) -> str:
    parts = ["Сделай конспект лекции по расшифровке."]
    if opts.length == "short":
        parts.append("Конспект краткий: 3-6 разделов, в каждом 2-4 коротких пункта. Только главное.")
    else:
        parts.append("Конспект полный и подробный: все основные темы, определения, выводы, "
                     "в каждом разделе несколько пунктов.")
    if opts.include_offtopic:
        parts.append("Отступления и разговоры не по теме включай отдельным разделом «Отступления».")
    else:
        parts.append("Игнорируй разговоры не по теме: организационные вопросы, бытовые реплики, "
                     "шутки, обсуждение оценок и посещаемости.")
    if opts.remove_filler:
        parts.append("Убирай слова-паразиты, повторы, оговорки и незаконченные фразы.")
    if opts.include_examples:
        parts.append("Сохраняй примеры, если они помогают понять тему.")
    else:
        parts.append("Примеров не включай.")
    if opts.extra_instructions.strip():
        parts.append(f"Пожелания преподавателя: {opts.extra_instructions.strip()}")
    parts.append(f"Верни JSON строго по схеме:\n{SCHEMA_HINT}")
    return "\n".join(parts)


def split_text(text: str, max_chars: int) -> list[str]:
    """Делит текст по строкам так, чтобы каждая часть была не длиннее max_chars."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > max_chars:
            chunks.append(current)
            current = ""
        while len(line) > max_chars:
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        current += line
    if current.strip():
        chunks.append(current)
    return chunks


def _validate(data: dict) -> Summary:
    summary = Summary.model_validate(data)
    if not summary.sections:
        raise ValueError("в конспекте нет ни одного раздела")
    return summary


def _ask(llm: LLMClient, rules: str, user_text: str) -> Summary:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{rules}\n\n{user_text}"},
    ]
    return llm.chat_json(messages, validate=_validate)


def summarize(text: str, opts: SummaryOptions, llm: LLMClient,
              progress: Callable[[str], None] = lambda _: None) -> Summary:
    rules = build_rules(opts)
    max_chars = llm.cfg.max_chunk_chars
    if len(text) <= max_chars:
        progress("Составляю конспект...")
        return _ask(llm, rules, f"Текст лекции:\n{text}")

    chunks = split_text(text, max_chars)
    partials = []
    for i, chunk in enumerate(chunks, 1):
        progress(f"Конспект части {i} из {len(chunks)}...")
        partials.append(_ask(llm, rules, f"Фрагмент лекции (часть {i} из {len(chunks)}):\n{chunk}"))

    progress("Объединяю части в общий конспект...")
    partials_json = json.dumps([p.model_dump() for p in partials], ensure_ascii=False)
    return _ask(
        llm,
        rules,
        "Ниже конспекты частей одной лекции в JSON. Объедини их в один конспект без повторов, "
        f"сохранив порядок разделов:\n{partials_json}",
    )


def summary_to_text(summary: Summary) -> str:
    lines = [summary.title, summary.overview]
    for section in summary.sections:
        lines.append(section.heading)
        lines.extend(f"- {point}" for point in section.points)
    lines.extend(f"{term.term}: {term.definition}" for term in summary.terms)
    return "\n".join(line for line in lines if line)
