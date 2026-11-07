"""Windows entry point for the installed LocalChat app (no Python needed).

PyInstaller bundles this file with the interpreter and all dependencies.
It starts Ollama (with the chosen models folder), runs the chat server in
this same process and shows the UI in a native window (pywebview/WebView2).
"""
import ctypes
import sys
import threading
import webbrowser

from local_chat import launcher
from local_chat import server as chat_server
from local_chat.server import ChatHandler, ThreadingHTTPServer

HOST_PORT = ("127.0.0.1", 8000)


def _message(text, error=True):
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, "LocalChat", 0x10 if error else 0x40)
    else:
        print(text)


def _finish(server):
    """Closing the app also stops Ollama, as requested."""
    chat_server._stop_ollama()
    server.shutdown()
    server.server_close()


def main():
    if launcher.chat_server_is_running():  # another instance is already running
        _message("LocalChat از قبل در حال اجراست.", error=False)
        return 0
    if not launcher.port_is_available(HOST_PORT[1]):
        _message("درگاه ۸۰۰۰ توسط برنامهٔ دیگری استفاده شده است. آن برنامه را ببندید و دوباره تلاش کنید.")
        return 1
    try:
        launcher.wait_for_ollama()
    except (OSError, RuntimeError) as error:
        # Ollama problems must not block the app: models list will show an error.
        _message(str(error) + "\n\nبرنامه باز می‌شود ولی تا اجرای Ollama مدلی در دسترس نیست.")
    try:
        server = ThreadingHTTPServer(HOST_PORT, ChatHandler)
    except OSError as error:
        _message(f"سرور چت اجرا نشد: {error}")
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        import webview  # pywebview: native window using Edge WebView2
    except ImportError:
        webview = None
    if webview is not None:
        try:
            window = webview.create_window(
                "LocalChat", launcher.CHAT_URL, width=1180, height=800, min_size=(760, 520)
            )
            chat_server.SHUTDOWN_HOOK = window.destroy
            webview.start()  # blocks until the window is closed
            _finish(server)
            return 0
        except Exception as error:  # e.g. WebView2 runtime missing
            _message(
                "پنجرهٔ برنامه باز نشد (احتمالاً Microsoft Edge WebView2 Runtime نصب نیست).\n"
                f"{error}\n\nبرنامه در مرورگر باز می‌شود."
            )

    # Fallback: browser + tiny control window
    webbrowser.open(launcher.CHAT_URL)
    try:
        import tkinter as tk
    except ImportError:
        threading.Event().wait()
        return 0
    window = tk.Tk()
    window.title("LocalChat")
    window.geometry("300x130")
    window.resizable(False, False)
    tk.Label(window, text="LocalChat is running\n127.0.0.1:8000", pady=10).pack()
    tk.Button(window, text="Open in browser", width=20,
              command=lambda: webbrowser.open(launcher.CHAT_URL)).pack(pady=2)
    tk.Button(window, text="Quit", width=20, command=window.destroy).pack(pady=2)
    chat_server.SHUTDOWN_HOOK = lambda: window.after(0, window.destroy)
    window.mainloop()
    _finish(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
