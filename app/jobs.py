"""Фоновые задачи (транскрибация, конспект, тест) с отслеживанием статуса."""
import logging
import threading
import time
import traceback
import uuid
from typing import Callable

log = logging.getLogger(__name__)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} сек".replace(".", ",")
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    return f"{minutes} мин {secs:02d} сек"


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
        started = time.monotonic()
        try:
            result = fn(progress, *args)
            elapsed = time.monotonic() - started
            _update(job_id, status="done", message=f"Готово за {_fmt_duration(elapsed)}",
                    result=result, seconds=round(elapsed, 1))
        except Exception as exc:
            log.error("Задача %s завершилась ошибкой:\n%s", kind, traceback.format_exc())
            _update(job_id, status="error", message="Ошибка", error=str(exc))

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
