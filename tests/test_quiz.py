import pytest
from pydantic import ValidationError

from app.config import LLMConfig
from app.llm import LLMClient
from app.quiz import Question, QuizOptions, generate_quiz


def test_single_needs_exactly_one_correct():
    with pytest.raises(ValidationError):
        Question(type="single", question="?", options=["a", "b", "c"], correct=[0, 1])


def test_multiple_needs_at_least_two_correct():
    with pytest.raises(ValidationError):
        Question(type="multiple", question="?", options=["a", "b", "c"], correct=[0])


def test_index_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Question(type="single", question="?", options=["a", "b", "c"], correct=[5])


def test_true_false_forces_options():
    q = Question(type="true_false", question="Утверждение", options=["x"], correct=[1])
    assert q.options == ["Верно", "Неверно"]


def test_true_false_invalid_answer_rejected():
    with pytest.raises(ValidationError):
        Question(type="true_false", question="Утверждение", correct=[2])


def test_options_limit_defaults():
    opts = QuizOptions()
    assert opts.count == 10
    assert opts.types == ["single"]


def test_generate_quiz_returns_requested_count(fake_server):
    base_url, _ = fake_server
    llm = LLMClient(LLMConfig(base_url=base_url, model="m"))
    quiz = generate_quiz("Текст про тесты", QuizOptions(count=1, types=["single", "true_false"]), llm)
    assert len(quiz.questions) == 1
    assert quiz.questions[0].type == "single"


def test_generate_quiz_fails_when_too_few_questions(fake_server):
    base_url, _ = fake_server
    llm = LLMClient(LLMConfig(base_url=base_url, model="m"))
    # В фейковом сервере 2 вопроса, просим 5 - проверка должна упасть после повторов
    with pytest.raises(Exception, match="вопрос"):
        generate_quiz("Текст", QuizOptions(count=5), llm)
