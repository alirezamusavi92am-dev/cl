"""Glue between chat messages, MetaTrader 5 and the forex desk.

Used by the normal chat (a pair name in the message switches the analyst on) and shares
every calculation with the separate forex page (see forex_desk).
"""
from datetime import datetime, timezone

if __package__:
    from . import forex_desk, forex_intent, mt5_data
    from . import settings as app_settings
else:
    import forex_desk
    import forex_intent
    import mt5_data
    import settings as app_settings

# Added to the system prompt (never truncated) whenever forex data is attached.
FOREX_PROMPT = (
    "حالت تحلیلگر فارکس فعال است. تحلیل کمّی زندهٔ MetaTrader 5 که با کد (اندیکاتور، ساختار، سطوح و احتمال تجربی) "
    "ساخته شده در یک پیام system آمده است. "
    "قواعد: (۱) هر قیمت، سطح، اندیکاتور، درصد یا زمانی که می‌نویسی باید عیناً از همان داده باشد؛ چیزی از خودت نساز، "
    "درصد حدس نزن و اگر داده‌ای نیست بگو نیست. "
    "(۲) اول زمان داده و وضعیت بازار را بگو؛ اگر بازار بسته یا تیک قدیمی است حتماً هشدار بده. "
    "(۳) تایم‌فریم معامله‌ی کاربر در سطر اول تحلیل آمده است؛ آن را فراموش نکن و پاسخ را روی آن بنا کن: جهت کلی از "
    "تایم‌فریم‌های بالاتر، ساختار و ورود از تایم‌فریم معامله، تریگر از تایم‌فریم پایین‌تر. "
    "(۴) ساختار پاسخ: جمع‌بندی؛ تصویر چندتایم‌فریمی و تناقض‌ها؛ سناریوها از محتمل‌ترین، هرکدام با درصد تجربی و بازهٔ "
    "۸۰٪، شرط تأیید، ورود، ابطال، اهداف و R:R؛ اعتبار آماری مدل؛ ریسک‌ها. اگر اعتبار «ضعیف» یا «بدون لبه» است صریح بگو. "
    "(۵) قطعی‌گویی و پیش‌بینی تضمینی نکن و دستور مستقیم خرید/فروش یا حجم معامله نده؛ سناریوها شرطی‌اند. "
    "(۶) تقویم اقتصادی فقط همان رویدادهایی است که در تحلیل آمده؛ اگر خبر مهمی نزدیک است حتماً هشدار بده و اگر تقویم "
    "در دسترس نیست چیزی حدس نزن و به کاربر بگو خودش بررسی کند. "
    "(۷) قیمت‌ها و اندیکاتورها فقط از MT5 است؛ منبع وب، اگر وجود دارد، فقط برای خبر و زمینهٔ بنیادی است. "
    "(۸) اگر کاربر فقط یک پرسش کوتاه دارد فقط همان را جواب بده و گزارش کامل را تکرار نکن. "
    "(۹) نام نمادها و اندیکاتورها را انگلیسی بنویس. در پایان یک جمله بگو این تحلیل آموزشی است و توصیهٔ مالی نیست."
)
FOREX_TEMPERATURE = 0.2
FATAL_CODES = {"not_installed", "init_failed", "not_connected", "timeout"}
DEEP_SYMBOLS = 2    # more pairs in one message get the lighter, shallow analysis


class ForexResult:
    def __init__(self, context, notices, ok, profile=None):
        self.context = context      # text for the system message (never None when forex was requested)
        self.notices = notices      # Persian strings shown under the answer
        self.ok = ok                # at least one symbol was analysed
        self.profile = profile      # trading profile used (timeframe, style)


def _last_user_text(messages):
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def wants_forex(messages):
    """True when forex mode is on and the newest message is about a currency pair."""
    if not app_settings.forex_settings()["enabled"]:
        return False
    symbols, _carried = forex_intent.symbols_for_messages(messages)
    return bool(symbols)


def compact_history(messages, last_chars=1400, older_chars=450):
    """Shorten old assistant answers of a forex conversation.

    The data they quoted is re-sent fresh with every question, so keeping the full text of
    earlier long reports only wastes context. The newest answer is kept almost whole.
    """
    last_assistant = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    result = []
    for index, message in enumerate(messages):
        content = message["content"]
        if message["role"] == "assistant":
            limit = last_chars if index == last_assistant else older_chars
            if len(content) > limit:
                content = content[:limit].rstrip() + " … [ادامهٔ تحلیل قبلی برای صرفه‌جویی در زمینه حذف شد؛ داده‌ی تازه در پیام system است]"
        result.append({**message, "content": content})
    return result


def _freshness_notice(snapshot):
    symbol = snapshot["symbol"]
    market = snapshot["market"]
    if market == "open":
        age = snapshot["tick_age"]
        return f"داده‌ی زنده‌ی MT5 برای {symbol} دریافت شد (آخرین تیک {age:.0f} ثانیه پیش)."
    if market == "closed":
        return f"بازار فارکس آخر هفته بسته است؛ برای {symbol} آخرین قیمت‌های موجود در MT5 استفاده شد."
    if market == "stale":
        return f"هشدار: آخرین تیک {symbol} قدیمی است ({snapshot['tick_age'] / 60:.0f} دقیقه پیش)."
    return f"داده‌ی {symbol} از MT5 گرفته شد ولی تازگی آن قابل تأیید نبود."


def _failure_context(errors):
    lines = ["[داده‌ی MetaTrader 5 در دسترس نیست]"]
    lines.extend(f"- {symbol}: {message}" for symbol, message in errors)
    lines.append("قانون: هیچ قیمت، سطح یا مقدار اندیکاتوری از خودت نساز. به کاربر بگو داده‌ی زنده نداری، "
                 "دلیلش را در یک جمله بگو و فقط توضیح مفهومی بده.")
    return "\n".join(lines)


def build_context(messages, max_chars, now=None):
    """Fetch MT5 data for the pair(s) in the conversation and build the digest for the model.

    The trading timeframe comes from what the user said earlier in the conversation
    (see ``forex_intent.trading_profile``). ``now`` is only injected by tests. Never
    raises: every problem becomes a notice and a context that tells the model not to
    invent numbers.
    """
    config = app_settings.forex_settings()
    symbols, carried = forex_intent.symbols_for_messages(messages)
    profile = forex_intent.trading_profile(messages)
    if not profile["explicit"]:
        profile = {**profile, "timeframe": config["default_timeframe"],
                   "style": forex_intent.STYLE_OF[config["default_timeframe"]]}
    trading_tf = profile["timeframe"]
    extra = forex_intent.detect_timeframes(_last_user_text(messages))
    reports, errors, notices = [], [], []
    for index, item in enumerate(symbols):
        try:
            report = forex_desk.analyze_symbol(
                item["symbol"], trading_tf, config, now=now or datetime.now(timezone.utc),
                deep=index < DEEP_SYMBOLS, extra_frames=extra)
        except mt5_data.MT5Error as exc:
            errors.append((item["symbol"], exc.message))
            if exc.code in FATAL_CODES:  # the terminal itself is the problem: other symbols fail the same way
                errors.extend((later["symbol"], exc.message) for later in symbols[index + 1:])
                break
            continue
        except forex_desk.DeskError as exc:
            errors.append((item["symbol"], exc.message))
            continue
        except Exception as exc:  # noqa: BLE001 - a bug or odd MT5 answer must not break the chat
            errors.append((item["symbol"], f"خطای غیرمنتظره در دریافت یا تحلیل داده ({type(exc).__name__})."))
            continue
        snapshot = report["snapshot"]
        reports.append(report)
        notices.append(_freshness_notice(snapshot))
        if item["reversed"]:
            notices.append(f"جفت‌ارز با ترتیب استاندارد بازار ({item['symbol']}) تحلیل شد.")
        if snapshot["missing_timeframes"]:
            notices.append("بعضی تایم‌فریم‌ها داده‌ی کافی نداشتند: " + ", ".join(snapshot["missing_timeframes"]) + ".")
        for warning in report["warnings"]:
            if warning.startswith("تاریخچهٔ"):
                notices.append(warning)
    if reports:
        label = forex_intent.STYLE_LABEL[profile["style"]]
        origin = "طبق گفتهٔ شما" if profile["explicit"] else "پیش‌فرض"
        notices.append(f"تایم‌فریم معامله: {trading_tf} ({label}) — {origin}؛ تا وقتی تغییرش ندهید حفظ می‌شود.")
    if carried and reports:
        notices.append("نماد از پیام قبلی گرفته شد: " + "، ".join(r["snapshot"]["symbol"] for r in reports) + ".")
    for symbol, message in errors:
        notices.append(f"داده‌ی MT5 برای {symbol} دریافت نشد: {message}")
    if not reports:
        return ForexResult(_failure_context(errors), notices, False, profile)
    failure_text = ""
    if errors:
        failure_text = "\n\n" + "\n".join(f"[بدون داده] {symbol}: {message}" for symbol, message in errors)
    budget = max(600, max_chars - failure_text.__len__())
    share = max(500, budget // len(reports))
    digests = [forex_desk.format_digest(report, share if len(reports) > 1 else budget) for report in reports]
    text = "\n\n".join(digests)
    if len(text) > budget:
        text = text[:budget]
    return ForexResult(text + failure_text, notices, True, profile)
