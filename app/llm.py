"""Клиент OpenAI-совместимого API (Ollama, LM Studio, llama.cpp, vLLM и т.п.)."""
import json
import re

import requests

from app.config import LLMConfig

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа модели, даже если там есть пояснения или ```блоки```."""
    cleaned = THINK_RE.sub("", text).strip()
    fence = FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end < start:
        raise LLMError("Модель вернула ответ без JSON")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Некорректный JSON: {exc}") from exc


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.api_key or 'none'}", "Content-Type": "application/json"}

    def list_models(self) -> list[str]:
        resp = requests.get(self.base + "/models", headers=self._headers(), timeout=20)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]

    def chat(self, messages: list[dict], json_mode: bool = True) -> str:
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = requests.post(self.base + "/chat/completions", headers=self._headers(),
                             json=payload, timeout=self.cfg.timeout)
        if resp.status_code == 400 and json_mode:
            # Некоторые серверы не знают response_format - повторяем без него
            return self.chat(messages, json_mode=False)
        if resp.status_code >= 400:
            raise LLMError(f"LLM-сервер вернул {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Неожиданный формат ответа LLM-сервера") from exc

    def chat_json(self, messages: list[dict], validate=lambda data: data, attempts: int = 3):
        """Запрашивает JSON и проверяет его через validate. При ошибке просит модель исправиться."""
        history = list(messages)
        for attempt in range(1, attempts + 1):
            try:
                return validate(parse_json(self.chat(history)))
            except (LLMError, ValueError) as exc:
                if attempt == attempts:
                    raise LLMError(f"Модель не вернула корректный результат: {exc}") from exc
                history.append({
                    "role": "user",
                    "content": f"Ответ не прошёл проверку: {exc}. "
                               "Верни исправленный JSON строго по заданной схеме, без пояснений.",
                })
