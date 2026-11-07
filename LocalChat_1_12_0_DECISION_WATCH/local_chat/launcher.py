import ctypes
import json
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


try:
    from . import settings as app_settings
except ImportError:  # run as a plain script
    import settings as app_settings

APP_DIR = Path(__file__).resolve().parent
SERVER_FILE = APP_DIR / "server.py"
CHAT_URL = "http://127.0.0.1:8000/"
STATUS_URL = "http://127.0.0.1:8000/api/status"
OLLAMA_URL = "http://127.0.0.1:11434/api/tags"
APP_VERSION = "1.9.0"
STARTUP_TIMEOUT = 60


def show_error(message):
    ctypes.windll.user32.MessageBoxW(None, message, "گفت‌وگوی محلی", 0x10)


def chat_server_is_running():
    try:
        with urlopen(STATUS_URL, timeout=2) as response:
            return json.loads(response.read().decode("utf-8")).get("version") == APP_VERSION
    except (OSError, URLError, TimeoutError, ValueError):
        return False


def port_is_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        return connection.connect_ex(("127.0.0.1", port)) != 0


def find_ollama():
    executable = shutil.which("ollama")
    if executable:
        return executable
    candidates = [
        Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]
    return next((str(path) for path in candidates if path.is_file()), None)


def ollama_is_running():
    try:
        with urlopen(OLLAMA_URL, timeout=2) as response:
            return response.status == 200
    except (OSError, URLError, TimeoutError):
        return False


def wait_for_ollama():
    if ollama_is_running():
        return

    executable = find_ollama()
    if not executable:
        raise RuntimeError(
            "Ollama پیدا نشد. ابتدا Ollama را نصب کنید یا برنامهٔ Ollama را باز کنید."
        )

    subprocess.Popen(
        [executable, "serve"],
        env=app_settings.ollama_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if ollama_is_running():
            return
        time.sleep(1)
    raise RuntimeError(
        "Ollama در زمان مناسب اجرا نشد. برنامهٔ Ollama را باز کنید و دوباره تلاش کنید."
    )


def start_chat_server():
    if chat_server_is_running():
        return
    if not port_is_available(8000):
        raise RuntimeError(
            "درگاه ۸۰۰۰ توسط برنامهٔ دیگری استفاده شده است. آن برنامه را ببندید و دوباره تلاش کنید."
        )

    subprocess.Popen(
        [sys.executable, str(SERVER_FILE)],
        cwd=APP_DIR,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if chat_server_is_running():
            return
        time.sleep(0.25)
    raise RuntimeError(
        "سرور چت اجرا نشد. مطمئن شوید فایل‌های برنامه کامل از ZIP استخراج شده‌اند."
    )


def main():
    try:
        wait_for_ollama()
        start_chat_server()
        webbrowser.open(CHAT_URL)
    except (OSError, RuntimeError) as error:
        show_error(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
