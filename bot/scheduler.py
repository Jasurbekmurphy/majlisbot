"""Vaqtga bog'liq ishlar: kunlik majlisni yaratish, e'lon va eslatma yuborish,
majlisdan keyin hisobot, QR kodni har N kunda yangilash."""
import asyncio
import logging
import math
import secrets
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile

from .checkin import Rules, meeting_name, meeting_start
from .config import Config
from .db import Database
from .roles import all_admin_ids
from .keyboards import checkin_keyboard, ikb
from .reports import meeting_report_text
from .utils import WEEKDAYS_UZ, esc, make_qr_png, parse_hhmm, qr_payload

log = logging.getLogger(__name__)
QR_CAPTION = "Telegram botdagi «Majlisga kirish» tugmasini bosing\nva shu kodni skanerlang"


async def broadcast(bot: Bot, db: Database, text: str, reply_markup=None) -> tuple[int, int]:
    sent = failed = 0
    for e in await db.linked_employees():
        for attempt in range(3):
            try:
                await bot.send_message(e["telegram_id"], text, reply_markup=reply_markup)
                sent += 1
                break
            except TelegramRetryAfter as err:  # Telegram "sekinroq" desa — kutib, qayta yuboramiz
                await asyncio.sleep(err.retry_after + 0.5)
            except Exception:
                failed += 1
                break
        else:
            failed += 1
        await asyncio.sleep(0.05)  # Telegram cheklovi: ~30 xabar/soniya
    return sent, failed


async def notify_admins(bot: Bot, db: Database, cfg: Config, text: str, reply_markup=None):
    for admin_id in await all_admin_ids(db, cfg):
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception:
            log.warning("Adminga (%s) xabar yuborilmadi", admin_id)


def _when(m) -> str:
    st = meeting_start(m)
    return f"{st:%d.%m.%Y} ({WEEKDAYS_UZ[st.weekday()]}) soat {st:%H:%M}"


async def announce_meeting(bot: Bot, db: Database, m) -> tuple[int, int]:
    title = f"\nMavzu: <b>{esc(m['title'])}</b>" if m["title"] else ""
    text = (f"Hurmatli hodimlar!\n\n📢 <b>Majlis e'lon qilindi</b>\n\n🗓 {_when(m)}{title}\n\n"
            "⚠️ Iltimos, majlisga kech qolmang!\n\n"
            "Majlisdan oldin botdan kirish uchun xabar keladi.")
    res = await broadcast(bot, db, text)
    await db.set_meeting_flag(m["id"], "announced")
    return res


async def send_reminder(bot: Bot, db: Database, cfg: Config, m, now: datetime) -> tuple[int, int]:
    start = meeting_start(m)
    mins = max(0, math.ceil((start - now).total_seconds() / 60))
    when = f"{mins} daqiqadan keyin" if mins > 0 else "hozir"
    title = f"\n<b>{esc(m['title'])}</b>" if m["title"] else ""
    text = (f"Hurmatli hodimlar!\n\n🔔 <b>Majlis {when} boshlanadi</b> (soat {start:%H:%M}){title}\n\n"
            "⚠️ Iltimos, majlisga kech qolmang!\n\n"
            "Majlis xonasiga kelgach, tugmani bosing va QR kodni skanerlang.")
    res = await broadcast(bot, db, text, checkin_keyboard(cfg.webapp_url))
    await db.set_meeting_flag(m["id"], "reminded")
    return res


async def cancel_meeting(bot: Bot, db: Database, m):
    await db.set_meeting_flag(m["id"], "cancelled")
    if m["announced"] or m["reminded"]:
        await broadcast(bot, db, f"❌ <b>Majlis bekor qilindi</b>\n\n{_when(m)} — {esc(meeting_name(m))}")


async def send_qr_to_admins(bot: Bot, db: Database, cfg: Config, caption: str):
    s = await db.settings()
    png = make_qr_png(qr_payload(s["qr_secret"]), QR_CAPTION)
    for admin_id in await all_admin_ids(db, cfg):
        try:
            await bot.send_document(admin_id, BufferedInputFile(png, filename="majlis_qr.png"), caption=caption)
        except Exception:
            log.warning("Adminga (%s) QR yuborilmadi", admin_id)


def next_qr_rotation(s: dict, tz) -> datetime:
    rotated = datetime.fromisoformat(s["qr_rotated_at"])
    t = parse_hhmm(s.get("qr_rotate_time", "16:00"))
    day = rotated.date() + timedelta(days=int(s.get("qr_rotate_days", "2")))
    return datetime.combine(day, t, tzinfo=tz)


async def rotate_qr(bot: Bot, db: Database, cfg: Config, now: datetime, reason: str):
    await db.set_setting("qr_secret", secrets.token_urlsafe(12))
    await db.set_setting("qr_rotated_at", now.isoformat(timespec="seconds"))
    nxt = next_qr_rotation(await db.settings(), cfg.tz)
    await send_qr_to_admins(
        bot, db, cfg,
        f"♻️ <b>QR kod yangilandi</b> ({reason})\n\nEski QR endi ishlamaydi. Shuni chop etib, "
        f"majlis xonasidagi eskisining o'rniga osing.\nKeyingi yangilanish: {nxt:%d.%m.%Y %H:%M}",
    )


async def ensure_daily_meeting(db: Database, cfg: Config, rules: Rules, now: datetime):
    today = now.date()
    if not rules.daily_enabled or not rules.is_workday(today):
        return
    if await db.daily_meeting(today.isoformat()):
        return
    start = datetime.combine(today, rules.meeting_time, tzinfo=cfg.tz)
    m_id = await db.create_meeting("daily", None, start, None, now)
    await db.set_meeting_flag(m_id, "announced")  # kunlik majlis alohida e'lon qilinmaydi, faqat eslatma
    _, close_dt = rules.window(start)
    if now > close_dt:  # bot majlisdan keyin ishga tushgan bo'lsa — eslatma kerak emas
        await db.set_meeting_flag(m_id, "reminded")


async def tick(bot: Bot, db: Database, cfg: Config, now: datetime | None = None):
    now = now or datetime.now(cfg.tz)
    s = await db.settings()
    rules = Rules.from_settings(s)

    await ensure_daily_meeting(db, cfg, rules, now)

    for m in await db.unfinished_meetings((now.date() - timedelta(days=1)).isoformat()):
        start = meeting_start(m)
        _, close_dt = rules.window(start)
        if not m["announced"]:
            await announce_meeting(bot, db, m)
        if not m["reminded"]:
            if start - timedelta(minutes=rules.remind_before) <= now <= close_dt:
                await send_reminder(bot, db, cfg, m, now)
            elif now > close_dt:
                await db.set_meeting_flag(m["id"], "reminded")
        if not m["report_sent"] and now >= close_dt:
            fresh = await db.meeting(m["id"])
            chunks = await meeting_report_text(db, cfg, fresh, now)
            buttons = ikb([[("✏️ Davomatni tahrirlash", f"ed:{m['id']}:0")]])
            for i, chunk in enumerate(chunks):
                await notify_admins(bot, db, cfg, chunk, buttons if i == len(chunks) - 1 else None)
            await db.set_meeting_flag(m["id"], "report_sent")

    if now >= next_qr_rotation(s, cfg.tz):
        await rotate_qr(bot, db, cfg, now, f"har {s.get('qr_rotate_days', '2')} kunda avtomatik")


async def run_scheduler(bot: Bot, db: Database, cfg: Config, interval: int = 20):
    while True:
        try:
            await tick(bot, db, cfg)
        except Exception:
            log.exception("Rejalashtiruvchida xatolik")
        await asyncio.sleep(interval)
