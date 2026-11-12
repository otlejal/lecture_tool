"""Проверка сохранения настроек через API на временной копии config.yaml."""
import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import app.config as config_module
from app.main import create_app

tmp = Path(tempfile.mkdtemp()) / "config.yaml"
shutil.copy(config_module.CONFIG_PATH, tmp)
config_module.CONFIG_PATH = tmp

client = TestClient(create_app())
settings = client.get("/api/settings").json()
print("до:", settings["transcription"]["local"])

settings["transcription"]["local"].update(model="small", device="cpu", compute_type="int8")
settings["transcription"]["backend"] = "local"
resp = client.put("/api/settings", json=settings)
print("PUT:", resp.status_code)

after = client.get("/api/settings").json()
print("после GET:", after["transcription"]["local"])
print("--- файл на диске ---")
print(tmp.read_text(encoding="utf-8"))
