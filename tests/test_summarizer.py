from app.config import LLMConfig
from app.llm import LLMClient
from app.summarizer import SummaryOptions, build_rules, split_text, summarize, summary_to_text


def test_split_text_respects_limit():
    text = "\n".join(f"строка {i} " + "x" * 50 for i in range(100))
    chunks = split_text(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks) == text


def test_split_text_cuts_very_long_line():
    chunks = split_text("а" * 1200, 500)
    assert [len(c) for c in chunks] == [500, 500, 200]


def test_short_text_is_one_request(fake_server):
    base_url, server = fake_server
    llm = LLMClient(LLMConfig(base_url=base_url, model="m", max_chunk_chars=20000))
    result = summarize("Короткий текст лекции.", SummaryOptions(), llm)
    assert result.title == "Тестовая лекция"
    assert len([c for c in server.calls if c[0] == "chat"]) == 1


def test_long_text_uses_map_reduce(fake_server):
    base_url, server = fake_server
    llm = LLMClient(LLMConfig(base_url=base_url, model="m", max_chunk_chars=2000))
    text = "\n".join("Фраза лекции номер %d про тесты и код." % i for i in range(400))
    result = summarize(text, SummaryOptions(), llm)
    chat_calls = [c[1] for c in server.calls if c[0] == "chat"]
    parts = len(split_text(text, 2000))
    # По одному запросу на каждую часть и один на объединение
    assert len(chat_calls) == parts + 1
    assert "Объедини их" in chat_calls[-1]
    assert result.sections


def test_rules_reflect_options():
    rules = build_rules(SummaryOptions(length="short", include_offtopic=False, remove_filler=True,
                                       extra_instructions="сделай таблицу"))
    assert "краткий" in rules
    assert "Игнорируй разговоры не по теме" in rules
    assert "сделай таблицу" in rules

    rules = build_rules(SummaryOptions(length="full", include_offtopic=True))
    assert "полный" in rules
    assert "Отступления" in rules


def test_summary_to_text_contains_all_parts(fake_server):
    base_url, _ = fake_server
    llm = LLMClient(LLMConfig(base_url=base_url, model="m"))
    summary = summarize("текст", SummaryOptions(), llm)
    text = summary_to_text(summary)
    assert "Введение" in text
    assert "Тест: Проверка поведения кода" in text
