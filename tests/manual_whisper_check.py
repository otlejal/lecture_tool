"""Ручная проверка локального whisper: нужна загрузка модели из интернета, поэтому не входит в автотесты.

Запуск из папки lecture_tool:  python tests/manual_whisper_check.py tiny
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

from app.config import LocalWhisperConfig
from app.transcriber import LocalWhisper, decode_audio

model_name = sys.argv[1] if len(sys.argv) > 1 else "tiny"
sample_rate = 16000
tone = 0.1 * np.sin(2 * np.pi * 440 * np.arange(sample_rate * 3) / sample_rate)
wav = Path(__file__).parent / "tone.wav"
with wave.open(str(wav), "wb") as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(sample_rate)
    f.writeframes((tone * 32767).astype("<i2").tobytes())

samples = decode_audio(wav)
print(f"декодировано {len(samples)} сэмплов, {len(samples) / sample_rate:.2f} с")

started = time.time()
whisper = LocalWhisper(LocalWhisperConfig(model=model_name, device="cpu", compute_type="int8"), print)
print(f"модель {model_name} загружена за {time.time() - started:.1f} с")
segments = whisper.transcribe(wav, "ru", print)
print("сегментов:", len(segments))
wav.unlink()
