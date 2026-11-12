"""HTTP API для фронтенда. Длинные операции запускаются как фоновые задачи."""
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import jobs, quiz, render, storage, summarizer, transcriber
from app.config import AppConfig, LLMConfig, load_config, lectures_root, save_config
from app.llm import LLMClient

router = APIRouter(prefix="/api")

DOWNLOADABLE = {
    "transcript.txt": "text/plain; charset=utf-8",
    "summary.html": "text/html; charset=utf-8",
    "summary.json": "application/json; charset=utf-8",
    "quiz.html": "text/html; charset=utf-8",
    "quiz.json": "application/json; charset=utf-8",
}


class LectureIn(BaseModel):
    title: str = ""
    group: str = ""
    subject: str = ""


class TranscriptIn(BaseModel):
    text: str


class SourceIn(BaseModel):
    type: Literal["transcript", "summary", "text"] = "transcript"
    text: str = ""
    is_html: bool = False


class SummaryIn(BaseModel):
    source: SourceIn = Field(default_factory=SourceIn)
    options: summarizer.SummaryOptions = Field(default_factory=summarizer.SummaryOptions)
    template: str = "default"


class QuizIn(BaseModel):
    source: SourceIn = Field(default_factory=lambda: SourceIn(type="summary"))
    options: quiz.QuizOptions = Field(default_factory=quiz.QuizOptions)
    template: str = "default"


class RenderIn(BaseModel):
    template: str = "default"


class TemplateIn(BaseModel):
    name: str
    content: str


def _folder(lecture_id: str) -> Path:
    try:
        return storage.get_folder(lectures_root(load_config()), lecture_id)
    except FileNotFoundError:
        raise HTTPException(404, "Тема не найдена")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _template(kind: str, name: str) -> str:
    try:
        return render.load_template(kind, name)
    except (FileNotFoundError, ValueError):
        raise HTTPException(400, f"Шаблон «{name}» не найден")


def _source_text(folder: Path, source: SourceIn) -> str:
    if source.type == "transcript":
        text = _read_text(folder / "transcript.txt")
        if not text.strip():
            raise HTTPException(400, "Текст лекции пуст. Сначала распознайте аудио")
        return text
    if source.type == "summary":
        path = folder / "summary.json"
        if not path.exists():
            raise HTTPException(400, "Конспект этой лекции ещё не создан")
        return summarizer.summary_to_text(summarizer.Summary.model_validate(_read_json(path)))
    text = render.html_to_text(source.text) if source.is_html else source.text
    if not text.strip():
        raise HTTPException(400, "Текст пуст")
    return text


# ---------- настройки и проверка LLM ----------

@router.get("/settings")
def get_settings() -> AppConfig:
    return load_config()


@router.put("/settings")
def put_settings(cfg: AppConfig) -> AppConfig:
    save_config(cfg)
    return cfg


@router.post("/llm/check")
def check_llm(cfg: LLMConfig):
    try:
        return {"ok": True, "models": LLMClient(cfg).list_models()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------- статус whisper ----------

@router.get("/whisper/status")
def whisper_status():
    return {"disk": transcriber.downloaded_models(), "memory": transcriber.loaded_models()}


@router.post("/whisper/unload")
def whisper_unload():
    removed = transcriber.unload_local_model()
    return {"ok": True, "removed": removed}


# ---------- лекции ----------

@router.get("/lectures")
def list_lectures():
    return storage.list_lectures(lectures_root(load_config()))


@router.post("/lectures")
def create_lecture(body: LectureIn):
    return storage.create_lecture(lectures_root(load_config()), body.title, body.group, body.subject)


@router.delete("/lectures/{lecture_id}")
def delete_lecture(lecture_id: str):
    storage.delete_lecture(lectures_root(load_config()), lecture_id)
    return {"ok": True}


@router.get("/lectures/{lecture_id}")
def get_lecture(lecture_id: str):
    return storage.lecture_info(_folder(lecture_id))


@router.post("/lectures/{lecture_id}/audio/chunk")
async def append_audio(lecture_id: str, request: Request, first: bool = False):
    """Принимает куски записи из браузера и дописывает их в audio.webm."""
    folder = _folder(lecture_id)
    data = await request.body()
    if first:
        storage.clear_audio(folder)
    with open(folder / "audio.webm", "ab") as f:
        f.write(data)
    return {"ok": True}


@router.post("/lectures/{lecture_id}/audio")
async def upload_audio(lecture_id: str, file: UploadFile = File(...)):
    folder = _folder(lecture_id)
    suffix = Path(file.filename or "").suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
        suffix = ".wav"
    storage.clear_audio(folder)
    with open(folder / f"audio{suffix}", "wb") as out:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return {"ok": True, "file": f"audio{suffix}"}


# ---------- транскрибация ----------

def _job_transcribe(progress, folder: Path, tcfg, language: str):
    count = transcriber.transcribe_lecture(folder, tcfg, language, progress)
    return {"segments": count}


@router.post("/lectures/{lecture_id}/transcribe")
def start_transcribe(lecture_id: str):
    folder = _folder(lecture_id)
    if storage.audio_path(folder) is None:
        raise HTTPException(400, "В лекции нет аудиозаписи")
    cfg = load_config()
    return {"job_id": jobs.start_job("transcribe", _job_transcribe, folder, cfg.transcription, cfg.language)}


@router.get("/lectures/{lecture_id}/transcript")
def get_transcript(lecture_id: str):
    return {"text": _read_text(_folder(lecture_id) / "transcript.txt")}


@router.put("/lectures/{lecture_id}/transcript")
def put_transcript(lecture_id: str, body: TranscriptIn):
    (_folder(lecture_id) / "transcript.txt").write_text(body.text, encoding="utf-8")
    return {"ok": True}


# ---------- конспект ----------

def _job_summary(progress, folder: Path, text: str, options, llm_cfg: LLMConfig, template: str):
    summary = summarizer.summarize(text, options, LLMClient(llm_cfg), progress)
    progress("Сохраняю конспект...")
    data = summary.model_dump()
    _write_json(folder / "summary.json", data)
    (folder / "summary.html").write_text(render.render_html(template, data), encoding="utf-8")
    return {"sections": len(summary.sections)}


@router.post("/lectures/{lecture_id}/summary")
def start_summary(lecture_id: str, body: SummaryIn):
    folder = _folder(lecture_id)
    if body.source.type == "summary":
        raise HTTPException(400, "Для конспекта источником может быть текст лекции или свой текст")
    text = _source_text(folder, body.source)
    template = _template("summary", body.template)
    cfg = load_config()
    job_id = jobs.start_job("summary", _job_summary, folder, text, body.options, cfg.llm, template)
    return {"job_id": job_id}


@router.get("/lectures/{lecture_id}/summary")
def get_summary(lecture_id: str):
    path = _folder(lecture_id) / "summary.json"
    if not path.exists():
        raise HTTPException(404, "Конспект ещё не создан")
    return _read_json(path)


@router.post("/lectures/{lecture_id}/summary/render")
def rerender_summary(lecture_id: str, body: RenderIn):
    folder = _folder(lecture_id)
    path = folder / "summary.json"
    if not path.exists():
        raise HTTPException(404, "Конспект ещё не создан")
    template = _template("summary", body.template)
    (folder / "summary.html").write_text(render.render_html(template, _read_json(path)), encoding="utf-8")
    return {"ok": True}


# ---------- тест ----------

def _job_quiz(progress, folder: Path, text: str, options, llm_cfg: LLMConfig, template: str):
    data = quiz.generate_quiz(text, options, LLMClient(llm_cfg), progress).model_dump()
    progress("Сохраняю тест...")
    _write_json(folder / "quiz.json", data)
    (folder / "quiz.html").write_text(render.render_html(template, data), encoding="utf-8")
    return {"questions": len(data["questions"])}


@router.post("/lectures/{lecture_id}/quiz")
def start_quiz(lecture_id: str, body: QuizIn):
    folder = _folder(lecture_id)
    text = _source_text(folder, body.source)
    template = _template("quiz", body.template)
    cfg = load_config()
    job_id = jobs.start_job("quiz", _job_quiz, folder, text, body.options, cfg.llm, template)
    return {"job_id": job_id}


@router.get("/lectures/{lecture_id}/quiz")
def get_quiz(lecture_id: str):
    path = _folder(lecture_id) / "quiz.json"
    if not path.exists():
        raise HTTPException(404, "Тест ещё не создан")
    return _read_json(path)


@router.post("/lectures/{lecture_id}/quiz/render")
def rerender_quiz(lecture_id: str, body: RenderIn):
    folder = _folder(lecture_id)
    path = folder / "quiz.json"
    if not path.exists():
        raise HTTPException(404, "Тест ещё не создан")
    template = _template("quiz", body.template)
    (folder / "quiz.html").write_text(render.render_html(template, _read_json(path)), encoding="utf-8")
    return {"ok": True}


# ---------- файлы, задачи, шаблоны ----------

@router.get("/lectures/{lecture_id}/files/{name}")
def get_file(lecture_id: str, name: str):
    if name not in DOWNLOADABLE:
        raise HTTPException(404, "Файл недоступен")
    path = _folder(lecture_id) / name
    if not path.exists():
        raise HTTPException(404, "Файл ещё не создан")
    return FileResponse(path, media_type=DOWNLOADABLE[name])


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job


@router.get("/templates/{kind}")
def list_templates(kind: str):
    try:
        return {"names": render.list_templates(kind)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/templates/{kind}")
def save_template(kind: str, body: TemplateIn):
    try:
        render.save_user_template(kind, body.name, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"names": render.list_templates(kind)}
