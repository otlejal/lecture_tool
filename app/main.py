from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import ROOT


def create_app() -> FastAPI:
    app = FastAPI(title="Лекционный помощник")
    app.include_router(router)
    # Фронтенд отдаём из папки static; маршруты API подключены раньше и имеют приоритет
    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
    return app


app = create_app()
