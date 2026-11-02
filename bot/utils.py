import html
import io
import math
import re
from datetime import time

import qrcode
from PIL import Image, ImageDraw, ImageFont

WEEKDAYS_UZ = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
WEEKDAYS_SHORT = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]

QR_PREFIX = "DAVOMAT:"


def qr_payload(secret: str) -> str:
    return QR_PREFIX + secret


def normalize_phone(raw: str | None) -> str:
    """Faqat raqamlarni qoldiradi; 9 xonali O'zbekiston raqamiga 998 qo'shadi."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    return digits


def fmt_phone(digits: str) -> str:
    return "+" + digits if digits else ""


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_hhmm(text: str) -> time | None:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return time(h, mi)


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Telegram 4096 belgi chegarasiga sig'dirish uchun matnni qatorlar bo'yicha bo'ladi."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def make_qr_png(payload: str, caption: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=20, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    w, h = qr_img.size
    pad = 60
    try:
        title_font = ImageFont.load_default(size=56)
        small_font = ImageFont.load_default(size=34)
    except TypeError:  # eski Pillow
        title_font = small_font = ImageFont.load_default()

    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_w = max(measure.textlength(line, font=small_font) for line in caption.split("\n"))
    width = int(max(w, text_w) + pad * 2)
    lines_h = 44 * len(caption.split("\n"))
    canvas = Image.new("RGB", (width, h + pad * 2 + 140 + lines_h), "white")
    draw = ImageDraw.Draw(canvas)
    title = "MAJLISGA KIRISH"
    tw = draw.textlength(title, font=title_font)
    draw.text(((canvas.width - tw) / 2, pad), title, fill="black", font=title_font)
    canvas.paste(qr_img, ((width - w) // 2, pad + 100))
    y = pad + 100 + h + 20
    for line in caption.split("\n"):
        lw = draw.textlength(line, font=small_font)
        draw.text(((canvas.width - lw) / 2, y), line, fill="#333333", font=small_font)
        y += 44

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
