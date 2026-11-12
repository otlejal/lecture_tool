"""Запуск приложения: поднимает сервер и открывает браузер."""
import logging
import os
import threading
import webbrowser

import uvicorn

HOST = os.environ.get("LECTURE_HOST", "127.0.0.1")
PORT = int(os.environ.get("LECTURE_PORT", "8000"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://127.0.0.1:{PORT}"
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    print(f"Приложение запущено: {url}  (остановка: Ctrl+C)")
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
