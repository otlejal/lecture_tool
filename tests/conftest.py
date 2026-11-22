"""Общие фикстуры: фейковый OpenAI-совместимый сервер и временная конфигурация приложения."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app

SUMMARY_JSON = {
    "title": "Тестовая лекция",
    "overview": "О связи тестов и кода.",
    "sections": [
        {"heading": "Введение", "points": ["Что такое тест", "Зачем он нужен"]},
        {"heading": "Практика", "points": ["Пишем первый тест"]},
    ],
    "terms": [{"term": "Тест", "definition": "Проверка поведения кода"}],
}

QUIZ_JSON = {
    "title": "Тест по лекции",
    "questions": [
        {
            "type": "single",
            "question": "Зачем нужен тест?",
            "options": ["Проверить код", "Украсить проект", "Ускорить интернет"],
            "correct": [0],
            "explanation": "Тест проверяет поведение кода.",
        },
        {
            "type": "true_false",
            "question": "Тесты заменяют код.",
            "options": [],
            "correct": [1],
            "explanation": "Тесты только проверяют код.",
        },
    ],
}

SLIDES_JSON = {
    "title": "Презентация о тестах",
    "subtitle": "Как проверять код",
    "theme": "nordic",
    "slides": [
        {"layout": "title", "heading": "Тесты в разработке", "subheading": "Зачем и как",
         "notes": "Представьтесь и назовите тему."},
        {"layout": "bullets", "heading": "Зачем нужен тест",
         "bullets": ["Проверяет поведение кода", "Ловит ошибки до релиза"],
         "notes": "Разберите два смысла теста."},
        {"layout": "statement", "heading": "Тест — это страховка, а не украшение",
         "notes": "Подведите итог."},
    ],
}

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 3.5, "text": "Здравствуйте, коллеги."},
    {"start": 3.5, "end": 9.0, "text": "Сегодня поговорим о тестах."},
]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "fake-model"}, {"id": "other-model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.path.endswith("/audio/transcriptions"):
            if "fail" in self.path:
                self._send({"error": "сервер сломан"}, 500)
                return
            self.server.calls.append(("whisper", len(raw)))
            self._send({"text": " ".join(s["text"] for s in FAKE_SEGMENTS), "segments": FAKE_SEGMENTS})
            return
        if self.path.endswith("/chat/completions"):
            payload = json.loads(raw)
            prompt = payload["messages"][-1]["content"]
            # Тип запроса ищем во всей истории: при повторе последнее сообщение уже другое
            history = " ".join(m["content"] for m in payload["messages"])
            self.server.calls.append(("chat", prompt))
            if "презентац" in history:
                payload_json = SLIDES_JSON
            elif "Составь тест" in history:
                payload_json = QUIZ_JSON
            else:
                payload_json = SUMMARY_JSON
            content = json.dumps(payload_json, ensure_ascii=False)
            self._send({"choices": [{"message": {"role": "assistant", "content": content}}]})
            return
        self._send({"error": "not found"}, 404)


@pytest.fixture
def client(app_env):
    return TestClient(create_app())


@pytest.fixture
def fake_server():
    """Поднимает фейковый сервер на свободном порту. server.calls хранит все запросы."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", server
    server.shutdown()
    server.server_close()


@pytest.fixture
def app_env(tmp_path, monkeypatch, fake_server):
    """Временный config.yaml, указывающий на фейковый сервер, и отдельная папка лекций."""
    import app.config as config_module
    import app.prompts as prompts_module
    import app.render as render_module

    # Пользовательские шаблоны тестов пишем во временную папку, а не в проект
    monkeypatch.setattr(render_module, "USER_TEMPLATES_DIR", tmp_path / "user_templates")
    # Мастер-промпты тоже
    monkeypatch.setattr(prompts_module, "PROMPTS_PATH", tmp_path / "prompts.json")
    base_url, server = fake_server
    lectures = tmp_path / "lectures"
    config = {
        "language": "ru",
        "lectures_dir": str(lectures),
        "transcription": {
            "backend": "remote",
            "remote": {"base_url": base_url, "model": "fake-whisper", "api_key": "", "timeout": 30},
        },
        "llm": {"base_url": base_url, "api_key": "none", "model": "fake-model",
                "timeout": 30, "temperature": 0.1, "max_chunk_chars": 20000},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
    return {"lectures": lectures, "config_path": config_path, "server": server, "base_url": base_url}
