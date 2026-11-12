import json

import pytest

from app import render


def test_render_injects_json_and_escapes_script_tags():
    template = '<script id="data" type="application/json">{{DATA}}</script>'
    data = {"title": "Опасно </script><b>x</b> <!-- комментарий"}
    html = render.render_html(template, data)

    # Внутри тега script не должно остаться закрывающего тега из данных
    assert "</script><b>" not in html
    payload = html.split('type="application/json">', 1)[1].rsplit("</script>", 1)[0]
    assert json.loads(payload) == data


def test_render_requires_placeholder():
    with pytest.raises(ValueError):
        render.render_html("<html></html>", {})


def test_html_to_text_reads_embedded_data():
    template = '<script id="data" type="application/json">{{DATA}}</script>'
    html = render.render_html(template, {"title": "Заголовок", "sections": [{"heading": "Раздел", "points": ["Пункт"]}]})
    text = render.html_to_text(html)
    assert "Заголовок" in text
    assert "Раздел" in text
    assert "Пункт" in text


def test_html_to_text_strips_tags_for_plain_html():
    text = render.html_to_text("<html><head><style>p{}</style></head><body><p>Привет</p><script>x()</script></body></html>")
    assert "Привет" in text
    assert "x()" not in text
    assert "p{}" not in text


def test_user_template_requires_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "USER_TEMPLATES_DIR", tmp_path)
    with pytest.raises(ValueError):
        render.save_user_template("quiz", "mine", "<html>без метки</html>")


def test_user_template_saved_and_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "USER_TEMPLATES_DIR", tmp_path)
    render.save_user_template("quiz", "mine", "<p>{{DATA}}</p>")
    assert "mine" in render.list_templates("quiz")
    assert render.load_template("quiz", "mine") == "<p>{{DATA}}</p>"


def test_default_name_is_reserved(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "USER_TEMPLATES_DIR", tmp_path)
    with pytest.raises(ValueError):
        render.save_user_template("quiz", "default", "{{DATA}}")


def test_bad_template_name_rejected():
    with pytest.raises(ValueError):
        render.template_path("quiz", "../evil")
