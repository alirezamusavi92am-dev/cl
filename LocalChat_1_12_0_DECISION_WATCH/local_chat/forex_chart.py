"""Candlestick chart as a PNG, drawn in pure Python (no matplotlib / PIL needed).

The picture is made for the local vision model *and* for the person: dark background,
green/red candles, EMA20 (blue) and EMA50 (purple) lines, support lines (green dashed),
resistance lines (red dashed), pivot (amber dotted) and the current price (white).
Price labels on the right edge are drawn with a tiny built-in digit font, so the model
can read the scale. Words are deliberately not drawn; the colour key is part of the
prompt (see forex_vision).
"""
import struct
import zlib

BG = (17, 20, 26)
GRID = (36, 41, 51)
UP = (38, 166, 126)
DOWN = (226, 78, 82)
EMA_FAST = (70, 150, 255)
EMA_SLOW = (176, 110, 255)
SUPPORT = (60, 200, 120)
RESISTANCE = (240, 90, 90)
PIVOT = (240, 180, 60)
PRICE = (235, 235, 235)
TEXT = (170, 176, 190)

# 3x5 glyphs, one string of 15 bits per character (row by row, 1 = pixel)
_GLYPHS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111",
    "3": "111001111001111", "4": "101101111001001", "5": "111100111001111",
    "6": "111100111101111", "7": "111001001001001", "8": "111101111101111",
    "9": "111101111001111", ".": "000000000000010", "-": "000000111000000",
    " ": "000000000000000",
}


class Canvas:
    def __init__(self, width, height, color=BG):
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(color) * (width * height))

    def put(self, x, y, color):
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset:offset + 3] = bytes(color)

    def rect(self, x0, y0, x1, y1, color):
        x0, x1 = max(0, min(x0, x1)), min(self.width - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(self.height - 1, max(y0, y1))
        if x1 < x0 or y1 < y0:
            return
        row = bytes(color) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            start = (y * self.width + x0) * 3
            self.pixels[start:start + len(row)] = row

    def hline(self, y, x0, x1, color, dash=0):
        if not 0 <= y < self.height:
            return
        for x in range(max(0, x0), min(self.width - 1, x1) + 1):
            if dash and (x // dash) % 2:
                continue
            self.put(x, y, color)

    def line(self, x0, y0, x1, y1, color):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            self.put(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += sx
            if doubled <= dx:
                error += dx
                y0 += sy

    def text(self, x, y, value, color, scale=2):
        for char in value:
            glyph = _GLYPHS.get(char, _GLYPHS[" "])
            for index, bit in enumerate(glyph):
                if bit == "1":
                    gx, gy = index % 3, index // 3
                    self.rect(x + gx * scale, y + gy * scale,
                              x + gx * scale + scale - 1, y + gy * scale + scale - 1, color)
            x += 4 * scale

    def png(self):
        stride = self.width * 3
        raw = bytearray()
        for y in range(self.height):
            raw.append(0)
            raw.extend(self.pixels[y * stride:(y + 1) * stride])

        def chunk(kind, data):
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def render(rates, ema20=None, ema50=None, supports=(), resistances=(), pivot=None,
           price=None, digits=5, bars=110, width=960, height=540):
    """PNG bytes of the last ``bars`` candles.

    ``rates``: dict of lists (time, open, high, low, close), oldest first, closed candles.
    ``ema20`` / ``ema50``: lists as long as ``rates`` (None where undefined).
    ``supports`` / ``resistances``: prices; ``pivot``: price or None.
    """
    n = len(rates["close"])
    first = max(0, n - bars)
    o, h, l, c = (rates[key][first:] for key in ("open", "high", "low", "close"))
    count = len(c)
    if count < 5:
        raise ValueError("not enough candles to draw")
    fast = (ema20 or [None] * n)[first:]
    slow = (ema50 or [None] * n)[first:]
    extras = [p for p in list(supports) + list(resistances) + ([pivot] if pivot else []) if p]
    lo, hi = min(l), max(h)
    # keep levels that are near the visible range, ignore far-away ones
    span = hi - lo
    near = [p for p in extras if lo - 0.35 * span <= p <= hi + 0.35 * span]
    lo, hi = min([lo] + near), max([hi] + near)
    pad = (hi - lo) * 0.06 or 1e-9
    lo, hi = lo - pad, hi + pad

    left, right, top, bottom = 10, 96, 12, 14
    plot_w, plot_h = width - left - right, height - top - bottom
    canvas = Canvas(width, height)

    def y_of(price_value):
        return int(top + (hi - price_value) / (hi - lo) * (plot_h - 1))

    step = plot_w / count
    # grid and price scale
    for i in range(6):
        value = lo + (hi - lo) * i / 5
        y = y_of(value)
        canvas.hline(y, left, left + plot_w, GRID)
        canvas.text(left + plot_w + 8, y - 5, f"{value:.{digits}f}", TEXT, scale=2)
    # levels first, candles over them
    for p in supports:
        if p in near:
            for dy in (0, 1):
                canvas.hline(y_of(p) + dy, left, left + plot_w, SUPPORT, dash=8)
    for p in resistances:
        if p in near:
            for dy in (0, 1):
                canvas.hline(y_of(p) + dy, left, left + plot_w, RESISTANCE, dash=8)
    if pivot and pivot in near:
        for dy in (0, 1):
            canvas.hline(y_of(pivot) + dy, left, left + plot_w, PIVOT, dash=3)
    body_w = max(1, int(step * 0.62))
    for i in range(count):
        x = int(left + (i + 0.5) * step)
        color = UP if c[i] >= o[i] else DOWN
        canvas.rect(x, y_of(h[i]), x, y_of(l[i]), color)
        top_body, bottom_body = y_of(max(o[i], c[i])), y_of(min(o[i], c[i]))
        if bottom_body - top_body < 1:
            bottom_body = top_body + 1
        canvas.rect(x - body_w // 2, top_body, x - body_w // 2 + body_w, bottom_body, color)
    for series, color in ((slow, EMA_SLOW), (fast, EMA_FAST)):
        previous = None
        for i, value in enumerate(series):
            if value is None or not lo <= value <= hi:
                previous = None
                continue
            point = (int(left + (i + 0.5) * step), y_of(value))
            if previous:
                canvas.line(previous[0], previous[1], point[0], point[1], color)
            previous = point
    last_price = price if price is not None else c[-1]
    if lo <= last_price <= hi:
        y = y_of(last_price)
        canvas.hline(y, left, left + plot_w, PRICE, dash=3)
        canvas.rect(left + plot_w + 4, y - 8, width - 2, y + 8, (50, 56, 70))
        canvas.text(left + plot_w + 8, y - 5, f"{last_price:.{digits}f}", PRICE, scale=2)
    return canvas.png()
