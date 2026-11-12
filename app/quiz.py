"""Генерация теста: вопросы в JSON, проверка схемы и правил для каждого типа вопроса."""
from typing import Callable, Literal

from pydantic import BaseModel, Field, model_validator

from app.llm import LLMClient

QuizType = Literal["single", "multiple", "true_false"]

TYPE_NAMES = {
    "single": "один правильный ответ из нескольких",
    "multiple": "несколько правильных ответов",
    "true_false": "верно или неверно",
}

DIFFICULTY_HINTS = {
    "easy": "лёгкие: проверка запоминания определений и фактов.",
    "medium": "средние: понимание смысла и применение знаний на простом примере.",
    "hard": "сложные: анализ, сравнение, выбор решения в нетривиальной ситуации. "
            "Неверные варианты должны выглядеть правдоподобно.",
}

SCHEMA_HINT = """{
  "title": "название теста",
  "questions": [
    {
      "type": "single | multiple | true_false",
      "question": "текст вопроса",
      "options": ["вариант 1", "вариант 2", "вариант 3", "вариант 4"],
      "correct": [0],
      "explanation": "почему ответ верный"
    }
  ]
}"""

SYSTEM_PROMPT = (
    "Ты составляешь тесты для студентов по материалу лекции на русском языке. "
    "Отвечай только корректным JSON без пояснений."
)


class QuizOptions(BaseModel):
    count: int = Field(default=10, ge=1, le=50)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    types: list[QuizType] = Field(default_factory=lambda: ["single"], min_length=1)


class Question(BaseModel):
    type: QuizType
    question: str
    options: list[str] = Field(default_factory=list)
    correct: list[int]
    explanation: str = ""

    @model_validator(mode="after")
    def check_answers(self):
        if self.type == "true_false":
            # Для верно/неверно варианты фиксированные: 0 - верно, 1 - неверно
            self.options = ["Верно", "Неверно"]
            if len(self.correct) != 1 or self.correct[0] not in (0, 1):
                raise ValueError("у вопроса «верно/неверно» должен быть один правильный ответ")
            return self
        if len(self.options) < 3:
            raise ValueError("у вопроса должно быть не меньше 3 вариантов")
        if any(i < 0 or i >= len(self.options) for i in self.correct):
            raise ValueError("индекс правильного ответа вне списка вариантов")
        if len(set(self.correct)) != len(self.correct):
            raise ValueError("повторяющиеся индексы правильных ответов")
        if self.type == "single" and len(self.correct) != 1:
            raise ValueError("у вопроса с одним ответом должен быть ровно один правильный ответ")
        if self.type == "multiple" and not (2 <= len(self.correct) < len(self.options)):
            raise ValueError("у вопроса с несколькими ответами должно быть от 2 правильных и не все варианты")
        return self


class QuizData(BaseModel):
    title: str
    questions: list[Question] = Field(min_length=1)


MAX_SOURCE_CHARS_NOTE = "Текст для теста сокращён до {} символов, так как он слишком длинный."


def _validate(data: dict, count: int) -> QuizData:
    quiz = QuizData.model_validate(data)
    if len(quiz.questions) < count:
        raise ValueError(f"получено {len(quiz.questions)} вопросов вместо {count}")
    quiz.questions = quiz.questions[:count]
    return quiz


def generate_quiz(text: str, opts: QuizOptions, llm: LLMClient,
                  progress: Callable[[str], None] = lambda _: None) -> QuizData:
    max_chars = llm.cfg.max_chunk_chars
    if len(text) > max_chars:
        text = text[:max_chars]
        progress(MAX_SOURCE_CHARS_NOTE.format(max_chars))

    types_text = ", ".join(TYPE_NAMES[t] for t in opts.types)
    user_text = (
        f"Составь тест из {opts.count} вопросов по тексту ниже.\n"
        f"Сложность: {DIFFICULTY_HINTS[opts.difficulty]}\n"
        f"Типы вопросов (распредели равномерно): {types_text}.\n"
        "Правила:\n"
        "- single: один правильный ответ, 4 варианта, correct - список из одного индекса.\n"
        "- multiple: 2 или больше правильных ответов из 4-5 вариантов, correct - список индексов.\n"
        "- true_false: утверждение, поле options не нужно, correct = [0] если верно, [1] если неверно.\n"
        "- Индексы начинаются с 0. Вопросы только по содержанию текста.\n"
        "- В explanation коротко объясни правильный ответ.\n"
        f"Верни JSON строго по схеме:\n{SCHEMA_HINT}\n\n"
        f"Текст:\n{text}"
    )
    progress("Генерирую вопросы...")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    return llm.chat_json(messages, validate=lambda d: _validate(d, opts.count))
