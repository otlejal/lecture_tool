"""Загрузка и сохранение настроек приложения (config.yaml)."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


class LocalWhisperConfig(BaseModel):
    model: str = "medium"
    device: str = "cuda"
    compute_type: str = "float16"


class RemoteWhisperConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    model: str = "large-v3"
    api_key: str = ""
    timeout: int = 3600


class TranscriptionConfig(BaseModel):
    backend: Literal["local", "remote"] = "local"
    local: LocalWhisperConfig = Field(default_factory=LocalWhisperConfig)
    remote: RemoteWhisperConfig = Field(default_factory=RemoteWhisperConfig)


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "none"
    model: str = ""
    timeout: int = 900
    temperature: float = 0.3
    max_chunk_chars: int = 20000


class AppConfig(BaseModel):
    language: str = "ru"
    lectures_dir: str = "lectures"
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    return AppConfig.model_validate(data)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    data = cfg.model_dump()
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def lectures_root(cfg: AppConfig) -> Path:
    # Если в lectures_dir указан абсолютный путь, Path вернёт его как есть
    return ROOT / cfg.lectures_dir
