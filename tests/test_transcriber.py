from pathlib import Path

import pytest

from app.config import RemoteWhisperConfig, TranscriptionConfig
from app.storage import create_lecture
from app.transcriber import fmt_time, make_transcriber, save_transcript, transcribe_lecture


def _silent(_):
    pass


def test_fmt_time():
    assert fmt_time(0) == "00:00:00"
    assert fmt_time(3725.9) == "01:02:05"


def _lecture_folder(lectures: Path, meta: dict) -> Path:
    return lectures / meta["group"] / meta["subject"] / meta["id"]


def test_remote_transcription(app_env):
    cfg = TranscriptionConfig(
        backend="remote",
        remote=RemoteWhisperConfig(base_url=app_env["base_url"], model="fake-whisper", timeout=30),
    )
    lectures = app_env["lectures"]
    meta = create_lecture(lectures, "Тест", group="Группа 1", subject="Предмет 1")
    folder = _lecture_folder(lectures, meta)
    assert folder.is_dir()
    (folder / "audio.webm").write_bytes(b"fake audio bytes")

    count = transcribe_lecture(folder, cfg, "ru", _silent)

    assert count == 2
    text = (folder / "transcript.txt").read_text(encoding="utf-8")
    assert "[00:00:00] Здравствуйте, коллеги." in text
    assert "[00:00:03] Сегодня поговорим о тестах." in text
    assert (folder / "transcript.json").exists()


def test_remote_error_is_reported(app_env):
    cfg = TranscriptionConfig(
        backend="remote",
        remote=RemoteWhisperConfig(base_url=app_env["base_url"] + "/fail", timeout=30),
    )
    lectures = app_env["lectures"]
    meta = create_lecture(lectures, "Тест", group="Группа 1", subject="Предмет 1")
    folder = _lecture_folder(lectures, meta)
    (folder / "audio.wav").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="500"):
        transcribe_lecture(folder, cfg, "ru", _silent)


def test_decode_webm_recording_to_16k_mono(tmp_path: Path):
    """Браузер присылает запись в webm/opus: декодер должен отдать 16 кГц mono."""
    import av
    import numpy as np

    from app.transcriber import decode_audio

    path = tmp_path / "audio.webm"
    try:
        container = av.open(str(path), mode="w")
        stream = container.add_stream("libopus", rate=48000)
    except Exception:
        pytest.skip("в этой сборке PyAV нет кодека libopus")
    stream.layout = "mono"
    sine = (0.2 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)).astype(np.float32)
    frame = av.AudioFrame.from_ndarray(sine.reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = 48000
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()

    samples = decode_audio(path)
    assert samples.dtype == np.float32
    # Примерно 1 секунда при частоте 16 кГц (небольшой запас на кодек)
    assert 15000 < len(samples) < 17000


def test_make_transcriber_dispatch():
    remote = make_transcriber(TranscriptionConfig(backend="remote"), _silent)
    assert type(remote).__name__ == "RemoteWhisper"


def test_float16_falls_back_to_int8(monkeypatch):
    """Старые GPU (например, GTX 1050 Ti) не умеют float16: загрузка должна сама перейти на int8."""
    from app import transcriber
    from app.config import LocalWhisperConfig

    attempts = []

    class FakeWhisperModel:
        def __init__(self, model_name, device, compute_type):
            attempts.append(compute_type)
            if compute_type == "float16":
                raise ValueError(
                    "Requested float16 compute type, but the target device or backend "
                    "do not support efficient float16 computation."
                )

    monkeypatch.setattr(transcriber, "_load_model", FakeWhisperModel)
    monkeypatch.setattr(transcriber, "_local_models", {})
    monkeypatch.setattr(transcriber, "_local_pipelines", {})
    monkeypatch.setattr(transcriber, "_unsupported_compute", set())

    messages = []
    cfg = LocalWhisperConfig(model="tiny", device="cuda", compute_type="float16", batch_size=1)
    transcriber.LocalWhisper(cfg, messages.append)

    assert attempts == ["float16", "int8"]
    assert ("cuda", "float16") in transcriber._unsupported_compute
    assert any("int8" in message for message in messages)

    # Повторная загрузка уже не тратит время на несовместимый режим
    transcriber.unload_local_model()
    transcriber.LocalWhisper(cfg, messages.append)
    assert attempts == ["float16", "int8", "int8"]


def test_save_transcript_skips_empty_segments(tmp_path: Path):
    save_transcript([{"start": 1, "end": 2, "text": ""}, {"start": 61, "end": 62, "text": "Текст"}], tmp_path)
    assert (tmp_path / "transcript.txt").read_text(encoding="utf-8") == "[00:01:01] Текст"
