"""Распознавание речи: локальный faster-whisper или OpenAI-совместимый удалённый сервер."""
import json
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


class LocalWhisper:
    def __init__(self, cfg: LocalWhisperConfig, progress: Progress):
        key = (cfg.model, cfg.device, cfg.compute_type)
        if key not in _local_models:
            progress(f"Загрузка модели whisper «{cfg.model}» (первый раз может занять несколько минут)...")
            from faster_whisper import WhisperModel

            _local_models[key] = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        self.model = _local_models[key]

    def transcribe(self, audio: Path, language: str, progress: Progress) -> list[dict]:
        progress("Декодирую аудио...")
        samples = decode_audio(audio)
        duration = len(samples) / SAMPLE_RATE
        segments, _ = self.model.transcribe(samples, language=language, beam_size=5, vad_filter=True)
        result = []
        for seg in segments:
            result.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
            if duration:
                progress(f"Распознано {min(100, int(100 * seg.end / duration))}% (до {fmt_time(seg.end)})")
        return result


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
