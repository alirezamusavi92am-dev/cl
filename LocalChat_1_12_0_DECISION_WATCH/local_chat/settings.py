"""Persistent app settings for LocalChat (Ollama models directory, web-search engine)."""
import json
import os
import re
import threading
from pathlib import Path

_LOCK = threading.Lock()


def app_data_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LocalChat"


def settings_path():
    return app_data_dir() / "settings.json"


def load_settings():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def default_models_dir():
    return Path.home() / ".ollama" / "models"


def configured_models_dir():
    """Directory the user chose in the app, or '' when none was chosen."""
    value = load_settings().get("models_dir")
    return value.strip() if isinstance(value, str) else ""


def effective_models_dir():
    """Directory Ollama should use: app setting, else OLLAMA_MODELS, else default."""
    chosen = configured_models_dir()
    if chosen:
        return Path(chosen)
    env_value = os.environ.get("OLLAMA_MODELS", "").strip()
    return Path(env_value) if env_value else default_models_dir()


def inspect_models_dir(path):
    """Describe a candidate models directory without modifying it."""
    folder = Path(path)
    info = {
        "path": str(folder),
        "exists": folder.is_dir(),
        "looks_like_ollama": False,
        "model_count": 0,
    }
    if not info["exists"]:
        return info
    manifests = folder / "manifests"
    info["looks_like_ollama"] = manifests.is_dir() and (folder / "blobs").is_dir()
    if manifests.is_dir():
        try:
            info["model_count"] = sum(1 for p in manifests.rglob("*") if p.is_file())
        except OSError:
            pass
    return info


def set_models_dir(raw_path):
    """Validate and store the models directory. Empty string resets to default.

    Raises ValueError with a Persian message for invalid input.
    """
    if not isinstance(raw_path, str):
        raise ValueError("مسیر معتبر نیست.")
    value = raw_path.strip().strip('"')
    with _LOCK:
        data = load_settings()
        if not value:
            data.pop("models_dir", None)
            _save_settings(data)
            return inspect_models_dir(effective_models_dir())
        if len(value) > 500 or "\x00" in value:
            raise ValueError("مسیر معتبر نیست.")
        folder = Path(os.path.expandvars(value)).expanduser()
        if not folder.is_absolute():
            raise ValueError("مسیر باید کامل باشد، مثلاً D:\\OllamaModels")
        if not folder.is_dir():
            raise ValueError("این پوشه وجود ندارد. ابتدا پوشه را بسازید یا مسیر درست را وارد کنید.")
        data["models_dir"] = str(folder)
        _save_settings(data)
        return inspect_models_dir(folder)


def ollama_environment():
    """Environment for launching `ollama serve` with the chosen models directory."""
    env = os.environ.copy()
    chosen = configured_models_dir()
    if chosen:
        env["OLLAMA_MODELS"] = chosen
    return env


# --- web search -------------------------------------------------------------------

SEARCH_ENGINES = ("google", "duckduckgo")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-.]{8,200}$")
_CX_PATTERN = re.compile(r"^[A-Za-z0-9:_\-.]{3,120}$")
SEARCH_KEY_FIELDS = {
    "serper_api_key": _KEY_PATTERN,
    "google_api_key": _KEY_PATTERN,
    "google_cx": _CX_PATTERN,
}


def search_settings():
    """Full search settings including secrets (server-side use only)."""
    data = load_settings().get("search", {})
    data = data if isinstance(data, dict) else {}
    engine = data.get("engine")
    result = {"engine": engine if engine in SEARCH_ENGINES else "google"}
    for field in SEARCH_KEY_FIELDS:
        value = data.get(field)
        result[field] = value.strip() if isinstance(value, str) else ""
    return result


def _hint(value):
    return ("\u2022\u2022\u2022\u2022" + value[-4:]) if value else ""


def search_settings_public():
    """What the browser may see: never the secrets themselves."""
    full = search_settings()
    return {
        "engine": full["engine"],
        "has_serper_key": bool(full["serper_api_key"]),
        "serper_hint": _hint(full["serper_api_key"]),
        "has_google_cse": bool(full["google_api_key"] and full["google_cx"]),
        "google_api_hint": _hint(full["google_api_key"]),
        "google_cx": full["google_cx"],
    }


def set_search_settings(update):
    """Apply a partial update. Missing field = keep, empty string = clear.

    Raises ValueError with a Persian message for invalid input.
    """
    if not isinstance(update, dict):
        raise ValueError("تنظیمات جست‌وجو معتبر نیست.")
    with _LOCK:
        data = load_settings()
        search = data.get("search", {})
        search = dict(search) if isinstance(search, dict) else {}
        if "engine" in update:
            if update["engine"] not in SEARCH_ENGINES:
                raise ValueError("موتور جست‌وجو معتبر نیست.")
            search["engine"] = update["engine"]
        for field, pattern in SEARCH_KEY_FIELDS.items():
            if field not in update:
                continue
            value = update[field]
            if not isinstance(value, str):
                raise ValueError("مقدار کلید معتبر نیست.")
            value = value.strip()
            if not value:
                search.pop(field, None)
            elif not pattern.fullmatch(value):
                raise ValueError("قالب کلید یا شناسهٔ موتور جست‌وجو معتبر نیست.")
            else:
                search[field] = value
        data["search"] = search
        _save_settings(data)
    return search_settings_public()


# --- forex mode (MetaTrader 5) ------------------------------------------------------

_SUFFIX_PATTERN = re.compile(r"^[A-Za-z0-9._#\-+]{0,12}$")
_TERMINAL_NAMES = {"terminal64.exe"}
_NEWS_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{2,5})?/[A-Za-z0-9._~/%?=&+-]{0,250}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._:/\-]{1,80}$")
FOREX_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")


def forex_settings():
    """Forex-mode settings. No secrets: the MT5 terminal keeps its own broker login."""
    data = load_settings().get("forex", {})
    data = data if isinstance(data, dict) else {}
    enabled = data.get("enabled")
    path = data.get("terminal_path")
    suffix = data.get("symbol_suffix")
    timeframe = data.get("default_timeframe")
    use_vision = data.get("use_vision")
    vision_model = data.get("vision_model")
    text_model = data.get("text_model")
    news_enabled = data.get("news_enabled")
    news_url = data.get("news_url")
    return {
        "news_enabled": news_enabled if isinstance(news_enabled, bool) else True,
        "news_url": news_url.strip() if isinstance(news_url, str) and _NEWS_URL.fullmatch(news_url.strip()) else "",
        "enabled": enabled if isinstance(enabled, bool) else True,
        "terminal_path": path.strip() if isinstance(path, str) else "",
        "symbol_suffix": suffix.strip() if isinstance(suffix, str) and _SUFFIX_PATTERN.fullmatch(suffix.strip()) else "",
        "default_timeframe": timeframe if timeframe in FOREX_TIMEFRAMES else "M15",
        "use_vision": use_vision if isinstance(use_vision, bool) else True,
        "vision_model": vision_model if isinstance(vision_model, str) and _MODEL_NAME.fullmatch(vision_model) else "",
        "text_model": text_model if isinstance(text_model, str) and _MODEL_NAME.fullmatch(text_model) else "",
    }


def set_forex_settings(update):
    """Apply a partial update. Raises ValueError with a Persian message for invalid input."""
    if not isinstance(update, dict):
        raise ValueError("تنظیمات فارکس معتبر نیست.")
    with _LOCK:
        data = load_settings()
        forex = data.get("forex", {})
        forex = dict(forex) if isinstance(forex, dict) else {}
        if "enabled" in update:
            if not isinstance(update["enabled"], bool):
                raise ValueError("مقدار فعال/غیرفعال معتبر نیست.")
            forex["enabled"] = update["enabled"]
        if "symbol_suffix" in update:
            value = update["symbol_suffix"]
            if not isinstance(value, str) or not _SUFFIX_PATTERN.fullmatch(value.strip()):
                raise ValueError("پسوند نماد فقط می‌تواند حرف، عدد و . _ # - + باشد (حداکثر ۱۲ نویسه).")
            forex["symbol_suffix"] = value.strip()
        if "news_enabled" in update:
            if not isinstance(update["news_enabled"], bool):
                raise ValueError("مقدار تقویم خبری معتبر نیست.")
            forex["news_enabled"] = update["news_enabled"]
        if "news_url" in update:
            value = update["news_url"]
            if not isinstance(value, str) or (value.strip() and not _NEWS_URL.fullmatch(value.strip())):
                raise ValueError("آدرس تقویم اقتصادی باید یک آدرس https معتبر باشد.")
            if value.strip():
                forex["news_url"] = value.strip()
            else:
                forex.pop("news_url", None)
        if "default_timeframe" in update:
            if update["default_timeframe"] not in FOREX_TIMEFRAMES:
                raise ValueError("تایم‌فریم پیش‌فرض معتبر نیست.")
            forex["default_timeframe"] = update["default_timeframe"]
        if "use_vision" in update:
            if not isinstance(update["use_vision"], bool):
                raise ValueError("مقدار تحلیل تصویری معتبر نیست.")
            forex["use_vision"] = update["use_vision"]
        for key in ("vision_model", "text_model"):
            if key in update:
                value = update[key]
                if not isinstance(value, str) or (value.strip() and not _MODEL_NAME.fullmatch(value.strip())):
                    raise ValueError("نام مدل معتبر نیست.")
                if value.strip():
                    forex[key] = value.strip()
                else:
                    forex.pop(key, None)
        if "terminal_path" in update:
            value = update["terminal_path"]
            if not isinstance(value, str):
                raise ValueError("مسیر ترمینال معتبر نیست.")
            value = value.strip().strip('"')
            if not value:
                forex.pop("terminal_path", None)
            else:
                if len(value) > 500 or "\x00" in value:
                    raise ValueError("مسیر ترمینال معتبر نیست.")
                path = Path(os.path.expandvars(value)).expanduser()
                if not path.is_absolute():
                    raise ValueError("مسیر باید کامل باشد، مثلاً C:\\Program Files\\MetaTrader 5\\terminal64.exe")
                if path.name.lower() not in _TERMINAL_NAMES:
                    raise ValueError("فایل باید terminal64.exe باشد.")
                if not path.is_file():
                    raise ValueError("این فایل وجود ندارد.")
                forex["terminal_path"] = str(path)
        data["forex"] = forex
        _save_settings(data)
    return forex_settings()
