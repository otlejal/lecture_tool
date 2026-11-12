import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(app_env):
    return TestClient(create_app())


def _wait(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("задача не завершилась")


def _new_lecture(client, title="Лекция про тесты"):
    return client.post("/api/lectures", json={"title": title}).json()["id"]


def test_settings_roundtrip(client, app_env):
    settings = client.get("/api/settings").json()
    assert settings["transcription"]["backend"] == "remote"

    settings["llm"]["model"] = "new-model"
    resp = client.put("/api/settings", json=settings)
    assert resp.status_code == 200
    assert client.get("/api/settings").json()["llm"]["model"] == "new-model"
    assert "new-model" in app_env["config_path"].read_text(encoding="utf-8")


def test_llm_check(client, app_env):
    body = client.get("/api/settings").json()["llm"]
    result = client.post("/api/llm/check", json=body).json()
    assert result["ok"] is True
    assert "fake-model" in result["models"]


def test_create_list_and_get_lecture(client):
    lecture_id = _new_lecture(client, "Физика")
    listed = client.get("/api/lectures").json()
    assert any(item["id"] == lecture_id and item["title"] == "Физика" for item in listed)
    info = client.get(f"/api/lectures/{lecture_id}").json()
    assert info["stages"] == {"audio": False, "transcript": False, "summary": False, "quiz": False}


def test_unknown_lecture_is_404(client):
    assert client.get("/api/lectures/..%2Fevil").status_code == 404
    assert client.get("/api/lectures/no-such-id").status_code == 404


def test_audio_chunks_are_appended(client):
    lecture_id = _new_lecture(client)
    client.post(f"/api/lectures/{lecture_id}/audio/chunk?first=true", content=b"part1")
    client.post(f"/api/lectures/{lecture_id}/audio/chunk?first=false", content=b"part2")
    info = client.get(f"/api/lectures/{lecture_id}").json()
    assert info["stages"]["audio"] is True


def test_full_pipeline(client, app_env):
    lecture_id = _new_lecture(client)

    # 1. Загрузка аудио и распознавание через удалённый whisper
    resp = client.post(f"/api/lectures/{lecture_id}/audio",
                       files={"file": ("lecture.mp3", b"fake mp3", "audio/mpeg")})
    assert resp.status_code == 200
    job = _wait(client, client.post(f"/api/lectures/{lecture_id}/transcribe").json()["job_id"])
    assert job["status"] == "done", job
    transcript = client.get(f"/api/lectures/{lecture_id}/transcript").json()["text"]
    assert "Здравствуйте" in transcript

    # Правка текста вручную сохраняется
    client.put(f"/api/lectures/{lecture_id}/transcript", json={"text": transcript + "\nДополнение."})
    assert "Дополнение." in client.get(f"/api/lectures/{lecture_id}/transcript").json()["text"]

    # 2. Конспект из текста лекции
    job = _wait(client, client.post(f"/api/lectures/{lecture_id}/summary",
                                    json={"options": {"length": "short"}, "template": "default"}).json()["job_id"])
    assert job["status"] == "done", job
    summary_html = client.get(f"/api/lectures/{lecture_id}/files/summary.html")
    assert summary_html.status_code == 200
    assert "Тестовая лекция" in summary_html.text
    assert "{{DATA}}" not in summary_html.text

    # 3. Тест из конспекта этой лекции
    job = _wait(client, client.post(f"/api/lectures/{lecture_id}/quiz",
                                    json={"source": {"type": "summary"},
                                          "options": {"count": 2, "types": ["single", "true_false"]}}).json()["job_id"])
    assert job["status"] == "done", job
    quiz_json = client.get(f"/api/lectures/{lecture_id}/quiz").json()
    assert len(quiz_json["questions"]) == 2
    assert client.get(f"/api/lectures/{lecture_id}/files/quiz.html").status_code == 200

    info = client.get(f"/api/lectures/{lecture_id}").json()
    assert all(info["stages"].values())


def test_quiz_from_pasted_html_summary(client):
    lecture_id = _new_lecture(client)
    html = ('<html><body><script id="data" type="application/json">'
            '{"title": "Чужой конспект", "sections": [{"heading": "Раздел", "points": ["Пункт"]}]}'
            "</script></body></html>")
    job = _wait(client, client.post(f"/api/lectures/{lecture_id}/quiz",
                                    json={"source": {"type": "text", "text": html, "is_html": True},
                                          "options": {"count": 2, "types": ["single"]}}).json()["job_id"])
    assert job["status"] == "done", job


def test_summary_needs_transcript_first(client):
    lecture_id = _new_lecture(client)
    resp = client.post(f"/api/lectures/{lecture_id}/summary", json={})
    assert resp.status_code == 400


def test_custom_template_used_for_quiz(client):
    lecture_id = _new_lecture(client)
    template = '<div id="x"></div><script id="data" type="application/json">{{DATA}}</script>MARK'
    resp = client.post("/api/templates/quiz", json={"name": "mark", "content": template})
    assert resp.status_code == 200
    assert "mark" in resp.json()["names"]

    client.put(f"/api/lectures/{lecture_id}/transcript", json={"text": "Текст для теста"})
    job = _wait(client, client.post(f"/api/lectures/{lecture_id}/quiz",
                                    json={"source": {"type": "text", "text": "Материал"},
                                          "options": {"count": 2, "types": ["single"]},
                                          "template": "mark"}).json()["job_id"])
    assert job["status"] == "done", job
    html = client.get(f"/api/lectures/{lecture_id}/files/quiz.html").text
    assert "MARK" in html and "{{DATA}}" not in html


def test_template_without_placeholder_rejected(client):
    resp = client.post("/api/templates/summary", json={"name": "bad", "content": "<p>нет метки</p>"})
    assert resp.status_code == 400


def test_unknown_file_not_downloadable(client):
    lecture_id = _new_lecture(client)
    assert client.get(f"/api/lectures/{lecture_id}/files/meta.json").status_code == 404
