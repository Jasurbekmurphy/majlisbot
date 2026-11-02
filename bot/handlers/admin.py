import calendar
import re
import secrets
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, KeyboardButton, Message, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove)

from ..checkin import Schedule
from ..config import Config
from ..db import Database
from ..reports import attempts_text, build_excel, day_report_text
from ..utils import WEEKDAYS_UZ, esc, fmt_phone, make_qr_png, normalize_phone, parse_hhmm, qr_payload

ADMIN_HELP = """<b>Admin buyruqlari</b>

<b>Hisobotlar</b>
/bugun — bugungi davomat (yoki /bugun 2026-11-01)
/hisobot — shu oy uchun Excel
/hisobot 2026-10 — oy bo'yicha
/hisobot 2026-10-01 2026-10-15 — davr bo'yicha
/urinishlar — bugun rad etilgan (shubhali) urinishlar

<b>Hodimlar</b>
/hodim_qosh — hodim qo'shish (har qatorda: telefon va F.I.Sh.)
/hodimlar — ro'yxat
/hodim_ochir +998901234567 — o'chirish
/hodim_reset +998901234567 — Telegram akkauntni ajratish (telefon almashtirganda)

<b>Sozlamalar</b>
/sozlamalar — joriy sozlamalar
/ofis — ofis joylashuvini belgilash (majlis xonasida turib)
/vaqt 09:00 — majlis vaqti
/radius 150 — ofisdan ruxsat etilgan masofa (metr)
/oyna 30 60 — majlisdan necha daqiqa oldin ochiladi / keyin yopiladi
/kechikish 5 — necha daqiqadan keyin «kech qoldi»
/ish_kunlari 1,2,3,4,5 — majlis kunlari (1=Dushanba … 7=Yakshanba)
/qr — chop etish uchun QR kod
/qr_yangi — yangi QR yaratish (eskisi ishlamay qoladi)"""


class OfficeState(StatesGroup):
    waiting_location = State()


def make_router(admin_ids: frozenset[int]) -> Router:
    router = Router(name="admin")
    router.message.filter(F.from_user.id.in_(admin_ids))

    @router.message(Command("admin", "yordam"))
    async def admin_help(message: Message):
        await message.answer(ADMIN_HELP)

    # ---------------- hisobotlar ----------------
    @router.message(Command("bugun"))
    async def bugun(message: Message, command: CommandObject, db: Database, cfg: Config):
        day = datetime.now(cfg.tz).date()
        if command.args:
            try:
                day = date.fromisoformat(command.args.strip())
            except ValueError:
                await message.answer("Sana formati: /bugun 2026-11-01")
                return
        for chunk in await day_report_text(db, cfg, day):
            await message.answer(chunk)

    @router.message(Command("urinishlar"))
    async def urinishlar(message: Message, command: CommandObject, db: Database, cfg: Config):
        day = datetime.now(cfg.tz).date()
        if command.args:
            try:
                day = date.fromisoformat(command.args.strip())
            except ValueError:
                await message.answer("Sana formati: /urinishlar 2026-11-01")
                return
        for chunk in await attempts_text(db, day):
            await message.answer(chunk)

    @router.message(Command("hisobot"))
    async def hisobot(message: Message, command: CommandObject, db: Database, cfg: Config):
        today = datetime.now(cfg.tz).date()
        args = (command.args or "").split()
        try:
            if not args:
                d1, d2 = today.replace(day=1), today
            elif len(args) == 1 and re.fullmatch(r"\d{4}-\d{2}", args[0]):
                y, m = map(int, args[0].split("-"))
                d1 = date(y, m, 1)
                d2 = date(y, m, calendar.monthrange(y, m)[1])
            elif len(args) == 2:
                d1, d2 = date.fromisoformat(args[0]), date.fromisoformat(args[1])
            else:
                raise ValueError
        except ValueError:
            await message.answer("Format: /hisobot, /hisobot 2026-10 yoki /hisobot 2026-10-01 2026-10-15")
            return
        if d1 > d2:
            d1, d2 = d2, d1
        if d1 > today:
            await message.answer("Bu davr hali kelmagan.")
            return
        data = await build_excel(db, cfg, d1, d2)
        name = f"davomat_{d1:%Y%m%d}_{min(d2, today):%Y%m%d}.xlsx"
        await message.answer_document(
            BufferedInputFile(data, filename=name),
            caption=f"📊 Davomat hisoboti: {d1:%d.%m.%Y} — {min(d2, today):%d.%m.%Y}",
        )

    # ---------------- hodimlar ----------------
    @router.message(Command("hodim_qosh"))
    async def hodim_qosh(message: Message, command: CommandObject, db: Database, cfg: Config):
        text = command.args or ""
        if not text.strip():
            await message.answer(
                "Hodimlarni shunday yuboring (har qatorda bittadan):\n\n"
                "<code>/hodim_qosh\n+998901234567 Aliyev Vali\n+998911112233 Karimova Nilufar</code>"
            )
            return
        now_iso = datetime.now(cfg.tz).isoformat(timespec="seconds")
        added, updated, bad = [], [], []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^([+\d][\d\s\-()]{7,}\d)\s+(.+)$", line)
            phone = normalize_phone(m.group(1)) if m else ""
            if not m or not (10 <= len(phone) <= 15):
                bad.append(line)
                continue
            res = await db.add_employee(phone, m.group(2).strip(), now_iso)
            (added if res == "added" else updated).append(m.group(2).strip())
        parts = []
        if added:
            parts.append(f"✅ Qo'shildi: {len(added)}")
        if updated:
            parts.append(f"♻️ Yangilandi: {len(updated)}")
        if bad:
            parts.append("⚠️ Tushunilmadi:\n" + "\n".join(esc(b) for b in bad))
        await message.answer("\n".join(parts) or "Hech narsa qo'shilmadi.")

    @router.message(Command("hodimlar"))
    async def hodimlar(message: Message, db: Database):
        emps = await db.employees()
        if not emps:
            await message.answer("Hodimlar yo'q. /hodim_qosh orqali qo'shing.")
            return
        lines = [f"<b>Hodimlar ({len(emps)})</b>", "🔗 — botga ulangan, ⭕ — hali ulanmagan", ""]
        for i, e in enumerate(emps, 1):
            mark = "🔗" if e["telegram_id"] else "⭕"
            lines.append(f"{i}. {mark} {esc(e['full_name'])} — {fmt_phone(e['phone'])}")
        text = "\n".join(lines)
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i + 4000])

    @router.message(Command("hodim_ochir"))
    async def hodim_ochir(message: Message, command: CommandObject, db: Database):
        phone = normalize_phone(command.args)
        if not phone:
            await message.answer("Format: /hodim_ochir +998901234567")
            return
        ok = await db.deactivate_employee(phone)
        await message.answer("✅ O'chirildi (tarixi saqlanadi)." if ok else "Bunday faol hodim topilmadi.")

    @router.message(Command("hodim_reset"))
    async def hodim_reset(message: Message, command: CommandObject, db: Database):
        phone = normalize_phone(command.args)
        if not phone:
            await message.answer("Format: /hodim_reset +998901234567")
            return
        ok = await db.unlink_telegram(phone)
        await message.answer(
            "✅ Telegram akkaunt ajratildi. Hodim yangi akkauntdan /start orqali qayta ro'yxatdan o'tadi."
            if ok else "Bu raqamga bog'langan akkaunt topilmadi."
        )

    # ---------------- sozlamalar ----------------
    @router.message(Command("sozlamalar"))
    async def sozlamalar(message: Message, db: Database):
        s = await db.settings()
        sched = Schedule.from_settings(s)
        days = ", ".join(WEEKDAYS_UZ[d] for d in sorted(sched.workdays)) or "—"
        office = (f"{s['office_lat']}, {s['office_lon']}" if sched.office else "❗ belgilanmagan (/ofis)")
        await message.answer(
            "<b>Sozlamalar</b>\n\n"
            f"Majlis vaqti: <b>{s['meeting_time']}</b>\n"
            f"Belgilanish oynasi: {s['open_before']} daq oldin — {s['close_after']} daq keyin\n"
            f"Kech qoldi: {s['late_after']} daqiqadan keyin\n"
            f"Majlis kunlari: {days}\n"
            f"Ofis joylashuvi: {office}\n"
            f"Ruxsat etilgan masofa: {s['radius']} m"
        )

    @router.message(Command("vaqt"))
    async def vaqt(message: Message, command: CommandObject, db: Database):
        t = parse_hhmm(command.args or "")
        if not t:
            await message.answer("Format: /vaqt 09:00")
            return
        await db.set_setting("meeting_time", f"{t:%H:%M}")
        await message.answer(f"✅ Majlis vaqti: {t:%H:%M}")

    @router.message(Command("radius"))
    async def radius(message: Message, command: CommandObject, db: Database):
        try:
            r = int((command.args or "").strip())
            assert 20 <= r <= 5000
        except (ValueError, AssertionError):
            await message.answer("Format: /radius 150 (20 dan 5000 metrgacha)")
            return
        await db.set_setting("radius", str(r))
        await message.answer(f"✅ Ruxsat etilgan masofa: {r} m")

    @router.message(Command("oyna"))
    async def oyna(message: Message, command: CommandObject, db: Database):
        try:
            before, after = map(int, (command.args or "").split())
            assert 0 <= before <= 600 and 1 <= after <= 720
        except (ValueError, AssertionError):
            await message.answer("Format: /oyna 30 60\n(majlisdan 30 daq oldin ochiladi, boshlangandan 60 daq keyin yopiladi)")
            return
        await db.set_setting("open_before", str(before))
        await db.set_setting("close_after", str(after))
        await message.answer(f"✅ Belgilanish: majlisdan {before} daq oldin ochiladi, {after} daq keyin yopiladi.")

    @router.message(Command("kechikish"))
    async def kechikish(message: Message, command: CommandObject, db: Database):
        try:
            n = int((command.args or "").strip())
            assert 0 <= n <= 240
        except (ValueError, AssertionError):
            await message.answer("Format: /kechikish 5")
            return
        await db.set_setting("late_after", str(n))
        await message.answer(f"✅ Majlis boshlanganidan {n} daqiqa o'tib kelganlar «kech qoldi» hisoblanadi.")

    @router.message(Command("ish_kunlari"))
    async def ish_kunlari(message: Message, command: CommandObject, db: Database):
        try:
            nums = sorted({int(x) for x in re.split(r"[,\s]+", (command.args or "").strip()) if x})
            assert nums and all(1 <= n <= 7 for n in nums)
        except (ValueError, AssertionError):
            await message.answer("Format: /ish_kunlari 1,2,3,4,5\n(1=Dushanba … 6=Shanba, 7=Yakshanba)")
            return
        await db.set_setting("workdays", ",".join(str(n - 1) for n in nums))
        await message.answer("✅ Majlis kunlari: " + ", ".join(WEEKDAYS_UZ[n - 1] for n in nums))

    @router.message(Command("ofis"))
    async def ofis(message: Message, command: CommandObject, state: FSMContext, db: Database):
        if command.args:
            m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)[,\s]+(-?\d+(?:\.\d+)?)\s*", command.args)
            if not m:
                await message.answer("Format: /ofis 41.311081 69.240562 yoki shunchaki /ofis")
                return
            await db.set_setting("office_lat", m.group(1))
            await db.set_setting("office_lon", m.group(2))
            await message.answer(f"✅ Ofis joylashuvi saqlandi: {m.group(1)}, {m.group(2)}")
            return
        await state.set_state(OfficeState.waiting_location)
        await message.answer(
            "Majlis xonasida turib, pastdagi tugma orqali joylashuvingizni yuboring.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📍 Shu yerni ofis deb belgilash", request_location=True)],
                          [KeyboardButton(text="Bekor qilish")]],
                resize_keyboard=True, one_time_keyboard=True,
            ),
        )

    @router.message(OfficeState.waiting_location, F.location)
    async def ofis_location(message: Message, state: FSMContext, db: Database):
        loc = message.location
        await db.set_setting("office_lat", f"{loc.latitude:.6f}")
        await db.set_setting("office_lon", f"{loc.longitude:.6f}")
        await state.clear()
        acc = f" (aniqlik ±{loc.horizontal_accuracy:.0f} m)" if loc.horizontal_accuracy else ""
        await message.answer(
            f"✅ Ofis joylashuvi saqlandi: {loc.latitude:.6f}, {loc.longitude:.6f}{acc}",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer_location(loc.latitude, loc.longitude)

    @router.message(OfficeState.waiting_location)
    async def ofis_cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=ReplyKeyboardRemove())

    # ---------------- QR ----------------
    async def send_qr(message: Message, db: Database, note: str):
        s = await db.settings()
        png = make_qr_png(qr_payload(s["qr_secret"]),
                          "Telegram botdagi «Majlisga kirish» tugmasini bosing\nva shu kodni skanerlang")
        await message.answer_document(BufferedInputFile(png, filename="majlis_qr.png"), caption=note)

    @router.message(Command("qr"))
    async def qr(message: Message, db: Database):
        await send_qr(message, db, "🖨 Shu QR kodni chop etib, majlis xonasiga osib qo'ying.")

    @router.message(Command("qr_yangi"))
    async def qr_yangi(message: Message, db: Database):
        await db.set_setting("qr_secret", secrets.token_urlsafe(12))
        await send_qr(message, db, "♻️ Yangi QR kod yaratildi. Eski QR endi ishlamaydi — buni chop etib almashtiring.")

    return router
