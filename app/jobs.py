"""Фоновые задачи (транскрибация, конспект, тест) с отслеживанием статуса."""
import logging
import threading
import traceback
import uuid
from typing import Callable

log = logging.getLogger(__name__)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def start_job(kind: str, fn: Callable, *args) -> str:
    """Запускает fn(progress, *args) в отдельном потоке и возвращает id задачи."""
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "message": "Запуск...",
            "result": None,
            "error": None,
        }

    def progress(message: str) -> None:
        _update(job_id, message=message)

    def run() -> None:
        try:
            result = fn(progress, *args)
            _update(job_id, status="done", message="Готово", result=result)
        except Exception as exc:
            log.error("Задача %s завершилась ошибкой:\n%s", kind, traceback.format_exc())
            _update(job_id, status="error", message="Ошибка", error=str(exc))

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
