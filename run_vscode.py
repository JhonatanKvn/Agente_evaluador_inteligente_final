import os
import subprocess
import threading
from pathlib import Path

from dotenv import load_dotenv

from app.web.server import app


def open_browser(url: str) -> None:
    chrome_paths = [
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LocalAppData", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for chrome in chrome_paths:
        if chrome.exists():
            subprocess.Popen([str(chrome), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    subprocess.Popen(["cmd", "/c", "start", "", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    load_dotenv()
    port = int(os.getenv("APP_PORT", "5001"))
    url = f"http://127.0.0.1:{port}/students"
    print(f"Abriendo interfaz en {url}", flush=True)
    threading.Timer(1.2, lambda: open_browser(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
