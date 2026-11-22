"""Презентация: правила промпта, нормализация ответа модели и сборка HTML."""
from types import SimpleNamespace

import pytest

from app import render, slides


def _fake_llm(payload):
    class FakeLLM:
        cfg = SimpleNamespace(max_chunk_chars=20000)

        def chat_json(self, messages, validate=lambda data: data):
            self.messages = messages
            return validate(payload)

    return FakeLLM()


def _slide(**overrides):
    data = {"layout": "bullets", "heading": "Заголовок",
            "bullets": ["Пункт один", "Пункт два"], "notes": "Сказать вслух"}
    data.update(overrides)
    return data


def test_build_rules_contains_count_theme_and_audience():
    rules = slides.build_rules(slides.SlidesOptions(count=7, theme="midnight", audience="студенты"))
    assert "ровно 7" in rules
    assert "midnight" in rules
    assert "студенты" in rules
    assert "4-6 пунктов" in rules
    assert "{{TEXT}}" not in rules


def test_build_rules_auto_lets_model_pick_theme():
    rules = slides.build_rules(slides.SlidesOptions())
    assert "выбери сам" in rules
    for key in slides.THEMES:
        assert key in rules


def test_generate_slides_normalizes_unknown_values_and_limits():
    payload = {
        "title": "  Презентация  ",
        "theme": "неизвестная",
        "slides": [
            _slide(layout="неизвестно", bullets=[f"Пункт {i}" for i in range(10)]),
            _slide(layout="statement", heading="Одна мысль"),
            _slide(layout="stats", heading="Цифры",
                   stats=[{"value": str(i), "label": "метка"} for i in range(8)]),
        ],
    }
    deck = slides.generate_slides("Текст", slides.SlidesOptions(count=3), _fake_llm(payload))

    assert deck.title == "Презентация"
    assert deck.theme == slides.DEFAULT_THEME
    assert deck.slides[0].layout == "bullets"
    assert len(deck.slides[0].bullets) == slides.MAX_BULLETS
    assert len(deck.slides[2].stats) == slides.MAX_STATS


def test_generate_slides_raises_when_too_few_slides():
    payload = {"title": "Мало", "slides": [_slide(), _slide()]}
    with pytest.raises(ValueError, match="слайдов"):
        slides.generate_slides("Текст", slides.SlidesOptions(count=5), _fake_llm(payload))


def test_slide_without_heading_is_rejected():
    payload = {"title": "Пусто", "slides": [_slide(heading=""), _slide(), _slide()]}
    with pytest.raises(ValueError, match="heading"):
        slides.generate_slides("Текст", slides.SlidesOptions(count=3), _fake_llm(payload))


def test_code_is_limited_by_lines():
    payload = {"title": "Код", "slides": [
        _slide(layout="code", code="\n".join(f"line {i}" for i in range(20))),
        _slide(), _slide(),
    ]}
    deck = slides.generate_slides("Текст", slides.SlidesOptions(count=3), _fake_llm(payload))
    assert len(deck.slides[0].code.splitlines()) == slides.MAX_CODE_LINES


def test_default_slides_template_renders():
    template = render.load_template("slides", "default")
    html = render.render_html(template, {
        "title": "Проверка шаблона",
        "theme": "nordic",
        "slides": [{"layout": "bullets", "heading": "Первый", "bullets": ["Тезис"]}],
    })
    assert "{{DATA}}" not in html
    assert "Проверка шаблона" in html
    assert "slides_default" not in html
    assert "Первый" in html


def test_slides_template_is_listed_and_editable(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "USER_TEMPLATES_DIR", tmp_path)
    assert "default" in render.list_templates("slides")
    render.save_user_template("slides", "night", "<p>{{DATA}}</p>")
    assert "night" in render.list_templates("slides")
