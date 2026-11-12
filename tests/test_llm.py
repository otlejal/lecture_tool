import pytest

from app.config import LLMConfig
from app.llm import LLMClient, LLMError, parse_json


def test_parse_json_plain():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_with_code_fence_and_text():
    text = 'Вот результат:\n```json\n{"title": "Да"}\n```\nГотово.'
    assert parse_json(text) == {"title": "Да"}


def test_parse_json_strips_reasoning_block():
    text = "<think>Думаю... {лишнее}</think>{\"ok\": true}"
    assert parse_json(text) == {"ok": True}


def test_parse_json_without_object_raises():
    with pytest.raises(LLMError):
        parse_json("ответ без скобок")


def test_list_models(fake_server):
    base_url, _ = fake_server
    client = LLMClient(LLMConfig(base_url=base_url, model="fake-model"))
    assert client.list_models() == ["fake-model", "other-model"]


def test_chat_json_retries_after_validation_error(fake_server):
    base_url, server = fake_server
    client = LLMClient(LLMConfig(base_url=base_url, model="fake-model"))
    attempts = []

    def validate(data):
        attempts.append(data)
        if len(attempts) == 1:
            raise ValueError("первый ответ не подходит")
        return data

    result = client.chat_json([{"role": "user", "content": "Summary: дай JSON"}], validate=validate)
    assert "title" in result
    assert len(attempts) == 2
    # Во второй попытке модель получает указание исправиться
    second_request = [c for c in server.calls if c[0] == "chat"][-1][1]
    assert second_request
