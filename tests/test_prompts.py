"""Мастер-промпты: хранение, валидация шаблона и предпросмотр."""
import time

import pytest


def test_prompt_crud(client, app_env):
    resp = client.post("/api/prompts/summary",
                       json={"name": "Строгий", "template": "Правила: {{RULES}}\nТекст: {{TEXT}}"})
    assert resp.status_code == 200
    pid = resp.json()["prompt"]["id"]

    listed = client.get("/api/prompts/summary").json()
    assert [p["id"] for p in listed["prompts"]] == [pid]
    assert listed["prompts"][0]["name"] == "Строгий"
    assert "default_template" in listed

    client.put(f"/api/prompts/{pid}", json={"name": "Другое имя", "template": "{{TEXT}}"})
    listed = client.get("/api/prompts/summary").json()
    assert listed["prompts"][0]["name"] == "Другое имя"

    assert client.delete(f"/api/prompts/{pid}").json()["ok"] is True
    assert client.get("/api/prompts/summary").json()["prompts"] == []
    assert client.delete(f"/api/prompts/{pid}").status_code == 404


def test_prompt_templates_are_separate_per_kind(client, app_env):
    client.post("/api/prompts/summary", json={"name": "S", "template": "{{TEXT}}"})
    assert client.get("/api/prompts/quiz").json()["prompts"] == []


def test_template_requires_text_marker(client, app_env):
    resp = client.post("/api/prompts/quiz", json={"name": "Без метки", "template": "просто текст"})
    assert resp.status_code == 400
    assert "TEXT" in resp.json()["detail"]


def test_preview_reflects_options(client, app_env):
    resp = client.post("/api/prompt/preview", json={
        "kind": "summary",
        "options": {"length": "short", "extra_instructions": "сделай таблицу"},
    })
    prompt = resp.json()["prompt"]
    assert "краткий" in prompt
    assert "сделай таблицу" in prompt
    assert "текст темы" in prompt  # вместо {{TEXT}} подставлена пометка

    resp = client.post("/api/prompt/preview", json={
        "kind": "quiz",
        "options": {"count": 7, "difficulty": "hard", "types": ["multiple"]},
    })
    prompt = resp.json()["prompt"]
    assert "из 7 вопросов" in prompt
    assert "несколько правильных ответов" in prompt


def test_preview_uses_custom_template(client, app_env):
    resp = client.post("/api/prompt/preview", json={
        "kind": "summary",
        "options": {},
        "template": "МОЙ ШАБЛОН {{RULES}} <<< {{TEXT}}",
    })
    prompt = resp.json()["prompt"]
    assert prompt.startswith("МОЙ ШАБЛОН ")
    assert prompt.endswith("<<< [текст темы — подставится при генерации]")


def test_bad_options_rejected_in_preview(client, app_env):
    resp = client.post("/api/prompt/preview", json={
        "kind": "quiz",
        "options": {"count": 0, "difficulty": "hard", "types": ["single"]},
    })
    assert resp.status_code == 400


@pytest.mark.usefixtures("app_env")
def test_generation_uses_custom_template(client, fake_server):
    template = "МОИ ПРАВИЛА: {{RULES}}\nИСХОДНИК: {{TEXT}}"
    created = client.post("/api/prompts/summary", json={"name": "X", "template": template}).json()["prompt"]

    # Фейковый ответ сервера содержит секции, валидация пройдёт
    lecture = client.post("/api/lectures", json={"title": "Т"}).json()
    client.put(f"/api/lectures/{lecture['id']}/transcript", json={"text": "Текст лекции."})
    job = client.post(f"/api/lectures/{lecture['id']}/summary", json={
        "source": {"type": "transcript"},
        "options": {},
        "prompt_id": created["id"],
    }).json()
    state = client.get(f"/api/jobs/{job['job_id']}")
    deadline = time.time() + 20
    while state.json()["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
        state = client.get(f"/api/jobs/{job['job_id']}")
    assert state.json()["status"] == "done", state.json()["error"]
    _, server = fake_server
    chat_prompts = [c[1] for c in server.calls if c[0] == "chat"]
    assert "МОИ ПРАВИЛА" in chat_prompts[0]
    assert "ИСХОДНИК" in chat_prompts[0]
