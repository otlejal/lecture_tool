"""Хранение лекций: папка на каждую лекцию, файлы и статусы этапов.

Структура на диске: lectures / Группа / Предмет / id_лекции.
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

META_FILE = "meta.json"
ID_PATTERN = re.compile(r"[\w\-]+")
UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]+')

NO_GROUP = "Без группы"
NO_SUBJECT = "Без предмета"


def _safe_name(value: str, default: str = NO_GROUP) -> str:
    """Имя папки для группы/предмета: без символов, запрещённых в путях Windows."""
    value = UNSAFE_CHARS.sub("_", (value or "").strip()).strip(" .")
    return value or default


def _write_meta(folder: Path, meta: dict) -> None:
    (folder / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_lecture_id(base: Path, subject_dir: Path) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    lecture_id, counter = stamp, 1
    while (
        (subject_dir / lecture_id).exists()
        or (base / lecture_id).exists()
        or any(base.glob(f"*/*/{lecture_id}"))
    ):
        counter += 1
        lecture_id = f"{stamp}_{counter}"
    return lecture_id


def create_lecture(base: Path, title: str, group: str = "", subject: str = "") -> dict:
    base.mkdir(parents=True, exist_ok=True)
    group_name = _safe_name(group)
    subject_name = _safe_name(subject, NO_SUBJECT)
    subject_dir = base / group_name / subject_name
    subject_dir.mkdir(parents=True, exist_ok=True)
    lecture_id = _new_lecture_id(base, subject_dir)
    folder = subject_dir / lecture_id
    folder.mkdir()
    meta = {
        "id": lecture_id,
        "title": title.strip() or lecture_id,
        "group": group_name,
        "subject": subject_name,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _write_meta(folder, meta)
    return meta


def get_folder(base: Path, lecture_id: str) -> Path:
    if not ID_PATTERN.fullmatch(lecture_id):
        raise FileNotFoundError(lecture_id)
    candidates = []
    if (base / lecture_id / META_FILE).is_file():
        candidates.append(base / lecture_id)
    candidates.extend(p for p in base.glob(f"*/*/{lecture_id}") if (p / META_FILE).is_file())
    if candidates:
        return candidates[0]
    raise FileNotFoundError(lecture_id)


def _migrate_legacy(base: Path) -> None:
    """Старые лекции лежали прямо в base — переносим в группа/предмет/id."""
    if not base.exists():
        return
    for folder in sorted(base.iterdir()):
        if not (folder / META_FILE).is_file():
            continue
        try:
            meta = json.loads((folder / META_FILE).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        group = _safe_name(meta.get("group") or "")
        subject = _safe_name(meta.get("subject") or "", NO_SUBJECT)
        dest = base / group / subject / folder.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            continue
        shutil.move(str(folder), str(dest))
        meta["group"], meta["subject"] = group, subject
        _write_meta(dest, meta)


def audio_path(folder: Path) -> Path | None:
    return next(folder.glob("audio.*"), None)


def delete_lecture(base: Path, lecture_id: str) -> None:
    folder = get_folder(base, lecture_id)  # FileNotFoundError, если лекции нет
    shutil.rmtree(folder)
    # Убираем опустевшие папки группы/предмета
    parent = folder.parent
    while parent != base and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def clear_audio(folder: Path) -> None:
    for path in folder.glob("audio.*"):
        path.unlink()


def lecture_info(folder: Path) -> dict:
    meta = json.loads((folder / META_FILE).read_text(encoding="utf-8-sig"))
    meta["stages"] = {
        "audio": audio_path(folder) is not None,
        "transcript": (folder / "transcript.txt").exists(),
        "summary": (folder / "summary.json").exists(),
        "slides": (folder / "slides.json").exists(),
        "quiz": (folder / "quiz.json").exists(),
    }
    return meta


def list_lectures(base: Path) -> list[dict]:
    if not base.exists():
        return []
    _migrate_legacy(base)
    items = [lecture_info(f) for f in base.glob("*/*/*") if (f / META_FILE).is_file()]
    return sorted(items, key=lambda m: m["id"], reverse=True)
