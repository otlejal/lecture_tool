"""Хранение лекций: папка на каждую лекцию, файлы и статусы этапов."""
import json
import re
from datetime import datetime
from pathlib import Path

META_FILE = "meta.json"
ID_PATTERN = re.compile(r"[\w\-]+")


def _write_meta(folder: Path, meta: dict) -> None:
    (folder / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_lecture_id(base: Path) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    lecture_id, counter = stamp, 1
    while (base / lecture_id).exists():
        counter += 1
        lecture_id = f"{stamp}_{counter}"
    return lecture_id


def create_lecture(base: Path, title: str) -> dict:
    base.mkdir(parents=True, exist_ok=True)
    lecture_id = _new_lecture_id(base)
    folder = base / lecture_id
    folder.mkdir()
    meta = {
        "id": lecture_id,
        "title": title.strip() or lecture_id,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _write_meta(folder, meta)
    return meta


def get_folder(base: Path, lecture_id: str) -> Path:
    if not ID_PATTERN.fullmatch(lecture_id):
        raise FileNotFoundError(lecture_id)
    folder = base / lecture_id
    if not (folder / META_FILE).is_file():
        raise FileNotFoundError(lecture_id)
    return folder


def audio_path(folder: Path) -> Path | None:
    return next(folder.glob("audio.*"), None)


def clear_audio(folder: Path) -> None:
    for path in folder.glob("audio.*"):
        path.unlink()


def lecture_info(folder: Path) -> dict:
    meta = json.loads((folder / META_FILE).read_text(encoding="utf-8-sig"))
    meta["stages"] = {
        "audio": audio_path(folder) is not None,
        "transcript": (folder / "transcript.txt").exists(),
        "summary": (folder / "summary.json").exists(),
        "quiz": (folder / "quiz.json").exists(),
    }
    return meta


def list_lectures(base: Path) -> list[dict]:
    if not base.exists():
        return []
    items = [lecture_info(f) for f in base.iterdir() if (f / META_FILE).is_file()]
    return sorted(items, key=lambda m: m["id"], reverse=True)
