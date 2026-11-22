"""Распознавание речи: локальный faster-whisper или OpenAI-совместимый удалённый сервер."""
import gc
import json
import os
import sys
from pathlib import Path
from typing import Callable

import av
import numpy as np
import requests

from app.config import LocalWhisperConfig, RemoteWhisperConfig, TranscriptionConfig
from app.storage import audio_path

Progress = Callable[[str], None]
SAMPLE_RATE = 16000

# Загруженные модели whisper кэшируются, чтобы не грузить их заново при каждой лекции
_local_models: dict = {}
_local_pipelines: dict = {}  # BatchedInferencePipeline для батчинга (когда batch_size > 1)
_cuda_dlls_ready = False
# Точности, которые уже не подошли видеокарте: повторно их не пробуем
_unsupported_compute: set[tuple[str, str]] = set()

SAFE_COMPUTE_TYPE = "int8"  # работает и на CPU, и на старых GPU (Pascal, GTX 10xx)

# Признаки сломанного CUDA-контекста: после такого сбоя модель нужно выгрузить,
# иначе следующие попытки падают с той же ошибкой.
_CUDA_FAILURES = ("out of memory", "invalid device ordinal", "cudaerrorinvaliddevice")


def _is_cuda_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _CUDA_FAILURES)


def setup_cuda_dlls() -> None:
    """CUDA-версии CTranslate2 нужны cuBLAS и cuDNN 12 (cublas64_12.dll, cudnn64_*.dll).

    Если библиотеки поставлены через pip (nvidia-cublas-cu12, nvidia-cudnn-cu12),
    добавляем их каталоги bin в поиск DLL Windows — иначе "Library cublas64_12.dll is not found".
    """
    global _cuda_dlls_ready
    if _cuda_dlls_ready:
        return
    _cuda_dlls_ready = True
    if sys.platform != "win32":
        return
    roots = {Path(sys.prefix) / "Lib" / "site-packages"}
    roots.update(Path(p).resolve() for p in sys.path if p and (Path(p) / "nvidia").is_dir())
    for root in roots:
        for sub in ("cublas", "cudnn"):
            dll_dir = root / "nvidia" / sub / "bin"
            if not dll_dir.is_dir():
                continue
            try:
                os.add_dll_directory(str(dll_dir))
            except OSError:
                pass
            os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")


def fmt_time(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _frames(out) -> list:
    if out is None:
        return []
    return out if isinstance(out, list) else [out]


def decode_audio(path: Path) -> np.ndarray:
    """Декодирует любой аудио/видео файл (webm, mp3, wav, m4a...) в mono float32 16 кГц.

    Делаем это сами через PyAV: встроенный декодер faster-whisper 1.2 несовместим с PyAV 19.
    """
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for out in _frames(resampler.resample(frame)):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in _frames(resampler.resample(None)):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)


def downloaded_models() -> list[dict]:
    """Модели whisper, уже скачанные на диск (кэш HuggingFace)."""
    try:
        from huggingface_hub import scan_cache_dir

        repos = scan_cache_dir().repos
    except Exception:
        return []
    out = []
    for repo in repos:
        short = repo.repo_id.split("/")[-1]
        for prefix in ("faster-distil-whisper-", "faster-whisper-"):
            if short.startswith(prefix):
                out.append({
                    "name": short[len(prefix):],
                    "size_gb": round(repo.size_on_disk / 1e9, 1),
                })
                break
    return sorted(out, key=lambda d: d["name"])


def loaded_models() -> list[dict]:
    """Модели whisper, которые сейчас держатся в памяти."""
    return [{"model": k[0], "device": k[1], "compute_type": k[2]} for k in _local_models]


def unload_local_model() -> bool:
    """Выгружает модель из памяти. Возвращает True, если что-то было выгружено."""
    if not _local_models:
        return False
    _local_models.clear()
    _local_pipelines.clear()
    gc.collect()
    return True


def _load_model(model_name: str, device: str, compute_type: str):
    """Загрузка faster-whisper. Вынесена отдельно, чтобы её было проще подменить в тестах."""
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=device, compute_type=compute_type)


class LocalWhisper:
    def __init__(self, cfg: LocalWhisperConfig, progress: Progress):
        compute_type = cfg.compute_type
        if (cfg.device, compute_type) in _unsupported_compute:
            compute_type = SAFE_COMPUTE_TYPE
            progress(f"Видеокарта не поддерживает {cfg.compute_type}, использую {compute_type}.")
        key = (cfg.model, cfg.device, compute_type)
        if key not in _local_models:
            if _local_models:
                # В памяти держим только одну модель: перед загрузкой новой выгружаем старую
                progress("Выгружаю предыдущую модель из памяти...")
                unload_local_model()
            if cfg.device == "cuda":
                setup_cuda_dlls()
            progress(f"Загрузка модели whisper «{cfg.model}» (первый раз может занять несколько минут)...")
            try:
                _local_models[key] = _load_model(cfg.model, cfg.device, compute_type)
            except (RuntimeError, ValueError) as exc:
                text = str(exc).lower()
                if "cublas" in text or "cudnn" in text:
                    raise RuntimeError(
                        "Не найдены CUDA-библиотеки (cublas64_12.dll / cudnn64_*.dll). "
                        "Установите их: .venv\\Scripts\\python.exe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 "
                        "и перезапустите приложение, либо выберите устройство CPU в настройках."
                    ) from exc
                if cfg.device == "cuda" and _is_cuda_failure(exc):
                    unload_local_model()
                    raise RuntimeError(
                        f"Видеопамять переполнена при загрузке модели «{cfg.model}». "
                        "Выберите модель поменьше (например, medium) или точность int8."
                    ) from exc
                if compute_type in ("float16", "int8_float16") and (
                    "float16" in text or "compute type" in text or "compute_type" in text
                ):
                    # Pascal и более старые карты (например, GTX 1050 Ti) не умеют float16:
                    # вместо ошибки переключаемся на универсальный int8.
                    progress(f"Видеокарта не поддерживает {compute_type}. Переключаюсь на int8 — "
                             "его можно выбрать в настройках, чтобы убрать это сообщение.")
                    key = (cfg.model, cfg.device, SAFE_COMPUTE_TYPE)
                    _local_models[key] = _load_model(cfg.model, cfg.device, SAFE_COMPUTE_TYPE)
                    _unsupported_compute.add((cfg.device, compute_type))
                else:
                    raise
        self.model = _local_models[key]
        # Батчинг в faster-whisper делается отдельным классом: ускорение в 2-3 раза на GPU/CPU.
        # batch_size не входит в ключ кэша модели, поэтому конвейер создаём/убираем при каждом использовании.
        if cfg.batch_size > 1:
            if key not in _local_pipelines:
                from faster_whisper import BatchedInferencePipeline

                _local_pipelines[key] = BatchedInferencePipeline(model=_local_models[key])
        else:
            _local_pipelines.pop(key, None)
        self.pipeline = _local_pipelines.get(key)
        self.cfg = cfg

    def transcribe(self, audio: Path, language: str, progress: Progress) -> list[dict]:
        progress("Декодирую аудио...")
        samples = decode_audio(audio)
        duration = len(samples) / SAMPLE_RATE
        if self.cfg.vad_filter:
            # VAD проходит по всей записи до первого сегмента: на длинных лекциях это
            # десятки секунд, поэтому сообщаем, что приложение не зависло.
            progress("Ищу речь в записи...")
        try:
            if self.pipeline is not None:
                segments, _ = self.pipeline.transcribe(
                    samples,
                    language=language,
                    vad_filter=self.cfg.vad_filter,
                    batch_size=self.cfg.batch_size,
                )
            else:
                segments, _ = self.model.transcribe(
                    samples,
                    language=language,
                    beam_size=5,
                    vad_filter=self.cfg.vad_filter,
                )
            result = []
            for seg in segments:
                result.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
                if duration:
                    progress(f"Распознано {min(100, int(100 * seg.end / duration))}% (до {fmt_time(seg.end)})")
            return result
        except Exception as exc:
            if self.cfg.device == "cuda" and _is_cuda_failure(exc):
                # Сломанный CUDA-контекст не даст распознать ничего до перезапуска:
                # выгружаем модель и просим повторить с меньшим батчем.
                self.model = None
                self.pipeline = None
                unload_local_model()
                raise RuntimeError(
                    "Видеопамять переполнена. Модель выгружена — попробуйте снова "
                    "с batch_size 2–4 или моделью поменьше."
                ) from exc
            raise


class RemoteWhisper:
    def __init__(self, cfg: RemoteWhisperConfig):
        self.cfg = cfg

    def transcribe(self, audio: Path, language: str, progress: Progress) -> list[dict]:
        url = self.cfg.base_url.rstrip("/") + "/audio/transcriptions"
        progress("Отправка аудио на удалённый whisper-сервер...")
        headers = {"Authorization": f"Bearer {self.cfg.api_key or 'none'}"}
        with open(audio, "rb") as f:
            resp = requests.post(
                url,
                headers=headers,
                timeout=self.cfg.timeout,
                files={"file": (audio.name, f)},
                data={"model": self.cfg.model, "language": language, "response_format": "verbose_json"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Whisper-сервер вернул {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        segments = payload.get("segments")
        if not segments:
            # Сервер без сегментов отдаёт только общий текст
            return [{"start": 0.0, "end": 0.0, "text": payload.get("text", "").strip()}]
        return [
            {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in segments
        ]


def make_transcriber(cfg: TranscriptionConfig, progress: Progress):
    if cfg.backend == "local":
        return LocalWhisper(cfg.local, progress)
    return RemoteWhisper(cfg.remote)


def save_transcript(segments: list[dict], folder: Path) -> None:
    (folder / "transcript.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [f"[{fmt_time(s['start'])}] {s['text']}" for s in segments if s["text"]]
    (folder / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")


def transcribe_lecture(folder: Path, cfg: TranscriptionConfig, language: str, progress: Progress) -> int:
    audio = audio_path(folder)
    if audio is None:
        raise FileNotFoundError("В лекции нет аудиофайла")
    transcriber = make_transcriber(cfg, progress)
    progress("Распознаю речь...")
    segments = transcriber.transcribe(audio, language, progress)
    save_transcript(segments, folder)
    return len(segments)
