"""Admin paneli — hammasi tugmalar orqali."""
import re
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, KeyboardButton, MenuButtonDefault, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from ..checkin import Rules, meeting_label, meeting_start
from ..config import Config
from ..db import Database
from ..keyboards import (ADMIN_BUTTONS, BTN_ADD_MEETING, BTN_ATTENDANCE, BTN_EMPLOYEES, BTN_MEETINGS, BTN_QR,
                         BTN_REPORT, BTN_SETTINGS, admin_menu, contact_keyboard, ikb)
from ..roles import IsAdmin, IsSuper, is_super
from ..reports import PRESENT, build_excel, employed_for, meeting_report_text, meeting_status
from ..scheduler import (QR_CAPTION, announce_meeting, broadcast, cancel_meeting, next_qr_rotation, rotate_qr,
                         send_reminder)
from ..utils import WEEKDAYS_SHORT, WEEKDAYS_UZ, esc, fmt_phone, make_qr_png, normalize_phone, parse_hhmm, qr_payload

PAGE = 20
NOT_MENU = ~F.text.in_(ADMIN_BUTTONS)


class AddMeeting(StatesGroup):
    day = State()
    time = State()
    people = State()
    title = State()
    confirm = State()


class EditPeople(StatesGroup):
    choose = State()


class AddEmployees(StatesGroup):
    lines = State()


class SetTime(StatesGroup):
    waiting = State()


class SetOffice(StatesGroup):
    waiting = State()


def short_name(full: str, limit: int = 18) -> str:
    parts = full.split()
    s = f"{parts[0]} {parts[1][0]}." if len(parts) >= 2 else full
    return s if len(s) <= limit else s[:limit - 1] + "…"


async def safe_edit(cb: CallbackQuery, text: str, markup=None):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except Exception:
        await cb.message.answer(text, reply_markup=markup)


def make_router() -> Router:
    router = Router(name="admin")
    router.message.filter(IsAdmin())
    router.callback_query.filter(IsAdmin())

    # ======================= Kirish / menyu =======================
    @router.message(CommandStart())
    @router.message(Command("admin", "menu"))
    async def start(message: Message, state: FSMContext, db: Database, cfg: Config):
        await state.clear()
        sup = is_super(cfg, message.from_user.id)
        role = "Bosh admin" if sup else "Admin"
        await message.answer(f"👋 <b>Admin paneli</b> ({role})\n\nPastdagi tugmalardan foydalaning.",
                             reply_markup=admin_menu(sup))
        if not await db.employee_by_tg(message.from_user.id):
            await message.answer(
                "Agar o'zingiz ham majlisga QR orqali belgilanmoqchi bo'lsangiz, hodim sifatida ro'yxatdan o'ting:",
                reply_markup=ikb([[("📱 Hodim sifatida ro'yxatdan o'tish", "self:reg")]]),
            )

    @router.callback_query(F.data == "self:reg")
    async def self_register(cb: CallbackQuery):
        await cb.answer()
        await cb.message.answer("Telefon raqamingizni tugma orqali yuboring:", reply_markup=contact_keyboard())

    @router.callback_query(F.data == "noop")
    async def noop(cb: CallbackQuery):
        await cb.answer()

    # ======================= 📋 Davomat =======================
    async def send_report(target: Message, db: Database, cfg: Config, m, edit: bool = False):
        chunks = await meeting_report_text(db, cfg, m)
        buttons = ikb([[("✏️ Tahrirlash", f"ed:{m['id']}:0"), ("🔄 Yangilash", f"rp:{m['id']}")]])
        if edit and len(chunks) == 1:
            try:
                await target.edit_text(chunks[0], reply_markup=buttons)
                return
            except Exception:
                pass
        for i, chunk in enumerate(chunks):
            await target.answer(chunk, reply_markup=buttons if i == len(chunks) - 1 else None)

    @router.message(F.text == BTN_ATTENDANCE)
    async def attendance_menu(message: Message, state: FSMContext, db: Database, cfg: Config):
        await state.clear()
        now = datetime.now(cfg.tz)
        today = now.date().isoformat()
        meetings = await db.meetings_between(today, today)
        if not meetings:
            recent = await db.meetings_between((now.date() - timedelta(days=7)).isoformat(), today)
            if recent:
                await message.answer(
                    "Bugun majlis yo'q. Oxirgi majlislar:",
                    reply_markup=ikb([[(meeting_label(m), f"rp:{m['id']}")] for m in recent[-5:]]))
            else:
                await message.answer("Bugun majlis yo'q.")
            return
        if len(meetings) == 1:
            await send_report(message, db, cfg, meetings[0])
            return
        await message.answer("Bugungi majlislar — qaysi birini ko'rasiz?",
                             reply_markup=ikb([[(meeting_label(m, with_date=False), f"rp:{m['id']}")] for m in meetings]))

    @router.callback_query(F.data.startswith("rp:"))
    async def report_cb(cb: CallbackQuery, db: Database, cfg: Config):
        m = await db.meeting(int(cb.data.split(":")[1]))
        await cb.answer("Yangilandi" if m else "Topilmadi")
        if m:
            await send_report(cb.message, db, cfg, m, edit=True)

    # --- davomatni tahrirlash: ismni bosib keldi/kelmadi qilish ---
    async def edit_keyboard(db: Database, cfg: Config, m, page: int):
        now = datetime.now(cfg.tz)
        rules = Rules.from_settings(await db.settings())
        _, close_dt = rules.window(meeting_start(m))
        emps = [e for e in await db.meeting_employees(m["id"]) if employed_for(e, close_dt)]
        att = await db.attendance_for_meeting(m["id"])
        pages = max(1, (len(emps) + PAGE - 1) // PAGE)
        page = min(max(page, 0), pages - 1)
        rows, row = [], []
        for e in emps[page * PAGE:(page + 1) * PAGE]:
            r = att.get(e["id"])
            st = meeting_status(r, now, close_dt)
            icon = {"on_time": "✅", "manual": "✅", "late": "⏰", "pending": "⬜"}.get(st, "❌")
            t = f" {r['checked_at'][11:16]}" if r and st in ("on_time", "late") else ""
            row.append((f"{icon} {short_name(e['full_name'])}{t}", f"tg:{m['id']}:{e['id']}:{page}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        rows.append(row)
        if pages > 1:
            rows.append([("◀️", f"ed:{m['id']}:{page - 1}") if page > 0 else ("·", "noop"),
                         (f"{page + 1}/{pages}", "noop"),
                         ("▶️", f"ed:{m['id']}:{page + 1}") if page < pages - 1 else ("·", "noop")])
        rows.append([("⬅️ Hisobotga qaytish", f"rp:{m['id']}")])
        return ikb(rows)

    def edit_text(m) -> str:
        return (f"✏️ <b>{esc(meeting_label(m))}</b>\n\n"
                "Ismni bosing:\n✅/⏰ keldi → ❌ <b>kelmadi</b> bo'ladi (QR'ni olib ketib skanerlaganlar uchun)\n"
                "❌/⬜ → ✅ <b>keldi</b> bo'ladi (masalan, telefoni o'chib qolgan bo'lsa)\n\n"
                "Hodimga bu haqda xabar boradi.")

    @router.callback_query(F.data.startswith("ed:"))
    async def edit_attendance(cb: CallbackQuery, db: Database, cfg: Config):
        _, mid, page = cb.data.split(":")
        m = await db.meeting(int(mid))
        await cb.answer()
        if m:
            await safe_edit(cb, edit_text(m), await edit_keyboard(db, cfg, m, int(page)))

    @router.callback_query(F.data.startswith("tg:"))
    async def toggle_attendance(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        _, mid, eid, page = cb.data.split(":")
        m, e = await db.meeting(int(mid)), await db.employee(int(eid))
        if not m or not e:
            await cb.answer("Topilmadi")
            return
        now = datetime.now(cfg.tz)
        r = await db.attendance(m["id"], e["id"])
        if r and r["status"] in PRESENT:
            new_status, note, user_text = "removed", "❌ kelmadi", "❌ Admin sizni «{}» majlisiga <b>kelmadi</b> deb belgiladi."
        else:
            new_status, note, user_text = "manual", "✅ keldi", "✅ Admin sizni «{}» majlisiga <b>keldi</b> deb belgiladi."
        await db.set_attendance_status(m["id"], e["id"], new_status, cb.from_user.id, now.isoformat(timespec="seconds"))
        await cb.answer(f"{e['full_name']}: {note}")
        if e["telegram_id"]:
            try:
                await bot.send_message(e["telegram_id"], user_text.format(esc(meeting_label(m))))
            except Exception:
                pass
        try:
            await cb.message.edit_reply_markup(reply_markup=await edit_keyboard(db, cfg, m, int(page)))
        except Exception:
            pass

    # ======================= ➕ Majlis qo'shish =======================
    @router.message(F.text == BTN_ADD_MEETING)
    async def add_meeting(message: Message, state: FSMContext, cfg: Config):
        await state.clear()
        await state.set_state(AddMeeting.day)
        today = datetime.now(cfg.tz).date()
        days = [today + timedelta(days=i) for i in range(3)]
        names = ["Bugun", "Ertaga", "Indinga"]
        await message.answer(
            "➕ <b>Yangi majlis</b>\n\nMajlis qaysi kuni? Tugmani bosing yoki sanani yozing (masalan <code>05.11</code>).",
            reply_markup=ikb([
                [(f"{n} ({d:%d.%m})", f"ma:d:{d.isoformat()}") for n, d in zip(names[:2], days[:2])],
                [(f"{names[2]} ({days[2]:%d.%m}, {WEEKDAYS_SHORT[days[2].weekday()]})", f"ma:d:{days[2].isoformat()}")],
                [("❌ Bekor qilish", "ma:x")],
            ]),
        )

    async def ask_time(target: Message, state: FSMContext, day: date, edit_cb: CallbackQuery | None = None):
        await state.update_data(day=day.isoformat())
        await state.set_state(AddMeeting.time)
        text = (f"🗓 {day:%d.%m.%Y} ({WEEKDAYS_UZ[day.weekday()]})\n\n"
                "Majlis soat nechada? Yozing, masalan <code>15:00</code>")
        if edit_cb:
            await safe_edit(edit_cb, text)
        else:
            await target.answer(text)

    @router.callback_query(AddMeeting.day, F.data.startswith("ma:d:"))
    async def add_meeting_day_cb(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await ask_time(cb.message, state, date.fromisoformat(cb.data[5:]), cb)

    @router.message(AddMeeting.day, F.text, NOT_MENU)
    async def add_meeting_day_text(message: Message, state: FSMContext, cfg: Config):
        today = datetime.now(cfg.tz).date()
        m = re.fullmatch(r"\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\s*", message.text)
        try:
            assert m
            y = int(m.group(3)) if m.group(3) else today.year
            y = y + 2000 if y < 100 else y
            d = date(y, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and d < today:
                d = date(y + 1, d.month, d.day)
            assert d >= today
        except (AssertionError, ValueError):
            await message.answer("Sanani tushunmadim yoki u o'tib ketgan. Masalan: <code>05.11</code>")
            return
        await ask_time(message, state, d)

    @router.message(AddMeeting.time, F.text, NOT_MENU)
    async def add_meeting_time(message: Message, state: FSMContext, db: Database, cfg: Config):
        t = parse_hhmm(message.text)
        if not t:
            await message.answer("Vaqtni shunday yozing: <code>15:00</code>")
            return
        day = date.fromisoformat((await state.get_data())["day"])
        start = datetime.combine(day, t, tzinfo=cfg.tz)
        if start <= datetime.now(cfg.tz):
            await message.answer("Bu vaqt o'tib ketgan. Boshqa vaqt yozing.")
            return
        await state.update_data(start=start.isoformat(), sel=[])
        await state.set_state(AddMeeting.people)
        await message.answer(PEOPLE_TEXT, reply_markup=await people_keyboard(db, set(), 0))

    # ---------- 👥 qatnashuvchilarni tanlash (yangi majlis va mavjud majlis uchun umumiy) ----------
    PEOPLE_TEXT = ("👥 <b>Majlisga kimlar qatnashadi?</b>\n\n"
                   "Ismlarni bosib belgilang, keyin «➡️ Davom etish»ni bosing.\n"
                   "Hamma hodim qatnashsa — «👥 Hammasi».")

    async def people_keyboard(db: Database, selected: set[int], page: int):
        emps = await db.employees()
        pages = max(1, (len(emps) + PAGE - 1) // PAGE)
        page = min(max(page, 0), pages - 1)
        rows, row = [], []
        for e in emps[page * PAGE:(page + 1) * PAGE]:
            mark = "✅" if e["id"] in selected else "▫️"
            row.append((f"{mark} {short_name(e['full_name'])}", f"mp:t:{e['id']}:{page}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        rows.append(row)
        if pages > 1:
            rows.append([("◀️", f"mp:p:{page - 1}") if page > 0 else ("·", "noop"), (f"{page + 1}/{pages}", "noop"),
                         ("▶️", f"mp:p:{page + 1}") if page < pages - 1 else ("·", "noop")])
        rows.append([("☑️ Barchasini belgilash", f"mp:sel:{page}"), ("🧹 Tozalash", f"mp:none:{page}")])
        rows.append([("👥 Hammasi", "mp:all"), (f"➡️ Davom etish ({len(selected)} ta)", "mp:ok")])
        rows.append([("❌ Bekor qilish", "ma:x")])
        return ikb(rows)

    PICKING = StateFilter(AddMeeting.people, EditPeople.choose)

    async def redraw_people(cb: CallbackQuery, state: FSMContext, db: Database, page: int):
        sel = set((await state.get_data()).get("sel", []))
        try:
            await cb.message.edit_reply_markup(reply_markup=await people_keyboard(db, sel, page))
        except Exception:
            pass

    @router.message(PICKING, F.text, NOT_MENU)
    async def people_text(message: Message):
        await message.answer("Qatnashuvchilarni yuqoridagi tugmalar orqali belgilang, keyin «➡️ Davom etish» "
                             "yoki «👥 Hammasi»ni bosing.")

    @router.callback_query(PICKING, F.data.startswith("mp:t:"))
    async def people_toggle(cb: CallbackQuery, state: FSMContext, db: Database):
        _, _, eid, page = cb.data.split(":")
        sel = set((await state.get_data()).get("sel", []))
        sel.symmetric_difference_update({int(eid)})
        await state.update_data(sel=sorted(sel))
        await cb.answer()
        await redraw_people(cb, state, db, int(page))

    @router.callback_query(PICKING, F.data.startswith("mp:p:"))
    async def people_page(cb: CallbackQuery, state: FSMContext, db: Database):
        await cb.answer()
        await redraw_people(cb, state, db, int(cb.data.split(":")[2]))

    @router.callback_query(PICKING, F.data.startswith("mp:sel:") | F.data.startswith("mp:none:"))
    async def people_bulk(cb: CallbackQuery, state: FSMContext, db: Database):
        _, kind, page = cb.data.split(":")
        sel = [e["id"] for e in await db.employees()] if kind == "sel" else []
        await state.update_data(sel=sel)
        await cb.answer("Hammasi belgilandi" if kind == "sel" else "Tozalandi")
        await redraw_people(cb, state, db, int(page))

    @router.callback_query(PICKING, F.data.in_({"mp:all", "mp:ok"}))
    async def people_done(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Database, cfg: Config):
        sel = (await state.get_data()).get("sel", [])
        if cb.data == "mp:ok" and not sel:
            await cb.answer("Kamida bitta hodimni belgilang yoki «👥 Hammasi»ni bosing.", show_alert=True)
            return
        people = [] if cb.data == "mp:all" else sel
        await cb.answer()
        if await state.get_state() == EditPeople.choose.state:
            await apply_people_edit(cb, state, bot, db, cfg, people)
            return
        await state.update_data(people=people)
        await state.set_state(AddMeeting.title)
        who = "hamma hodimlar" if not people else f"{len(people)} kishi tanlandi"
        await safe_edit(cb, f"👥 Qatnashuvchilar: <b>{who}</b>")
        await cb.message.answer(
            "📝 Majlis mavzusi yoki e'longa qo'shimcha matn yozing (ixtiyoriy).\n\n"
            "Masalan: <i>Oylik hisobotlar muhokamasi. Hisobotlaringizni olib keling.</i>",
            reply_markup=ikb([[("⏭ O'tkazib yuborish", "ma:skip")]]))

    def people_summary(names: list[str], everyone: bool, limit: int = 15) -> str:
        if everyone:
            return f"hamma hodimlar ({len(names)} kishi)"
        shown = ", ".join(esc(n) for n in names[:limit])
        more = f" va yana {len(names) - limit} kishi" if len(names) > limit else ""
        return f"{len(names)} kishi — {shown}{more}"

    async def ask_confirm(target: Message, state: FSMContext, db: Database, title: str | None):
        await state.update_data(title=title)
        await state.set_state(AddMeeting.confirm)
        data = await state.get_data()
        start = datetime.fromisoformat(data["start"])
        rules = Rules.from_settings(await db.settings())
        people = set(data.get("people") or [])
        emps = [e for e in await db.employees() if not people or e["id"] in people]
        linked = sum(1 for e in emps if e["telegram_id"])
        not_linked = len(emps) - linked
        warn = f" ({not_linked} tasi botga kirmagan — ularga xabar bormaydi.)" if not_linked else ""
        text = (f"<b>Majlisni tasdiqlang</b>\n\n🗓 {start:%d.%m.%Y} ({WEEKDAYS_UZ[start.weekday()]})\n"
                f"🕒 {start:%H:%M}\n📝 {esc(title) if title else '—'}\n"
                f"👥 {people_summary([e['full_name'] for e in emps], not people)}\n\n"
                f"Tasdiqlasangiz, hozir {linked} ta hodimga e'lon yuboriladi.{warn} Majlisdan {rules.remind_before} "
                f"daqiqa oldin esa ularga ilovaga kirish tugmasi boradi.")
        await target.answer(text, reply_markup=ikb([[("✅ E'lon qilish", "ma:ok")], [("❌ Bekor qilish", "ma:x")]]))

    @router.message(AddMeeting.title, F.text, NOT_MENU)
    async def add_meeting_title(message: Message, state: FSMContext, db: Database):
        await ask_confirm(message, state, db, message.text.strip()[:500])

    @router.callback_query(AddMeeting.title, F.data == "ma:skip")
    async def add_meeting_skip(cb: CallbackQuery, state: FSMContext, db: Database):
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=None)
        await ask_confirm(cb.message, state, db, None)

    @router.callback_query(AddMeeting.confirm, F.data == "ma:ok")
    async def add_meeting_ok(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Database, cfg: Config):
        data = await state.get_data()
        await state.clear()
        await cb.answer("E'lon yuborilmoqda…")
        now = datetime.now(cfg.tz)
        start = datetime.fromisoformat(data["start"])
        if start <= now:
            await safe_edit(cb, "Bu vaqt o'tib ketdi. Majlisni qaytadan qo'shing.")
            return
        m_id = await db.create_meeting("extra", data.get("title"), start, cb.from_user.id, now)
        if data.get("people"):
            await db.set_participants(m_id, data["people"])
        m = await db.meeting(m_id)
        await safe_edit(cb, f"⏳ {esc(meeting_label(m))}\nE'lon yuborilmoqda…")
        sent, failed = await announce_meeting(bot, db, m)
        rules = Rules.from_settings(await db.settings())
        extra = ""
        if start - timedelta(minutes=rules.remind_before) <= now:
            await send_reminder(bot, db, cfg, await db.meeting(m_id), now)
            extra = "\nMajlis yaqin bo'lgani uchun kirish tugmasi ham darhol yuborildi."
        else:
            extra = f"\nKirish tugmasi {start - timedelta(minutes=rules.remind_before):%d.%m %H:%M} da boradi."
        fail_txt = f" ({failed} tasiga yetmadi — botni bloklagan bo'lishi mumkin)" if failed else ""
        await safe_edit(cb, f"✅ <b>Majlis qo'shildi</b>\n{esc(meeting_label(m))}\n\n"
                            f"E'lon {sent} ta hodimga yuborildi{fail_txt}.{extra}")

    @router.callback_query(F.data == "ma:x")
    async def add_meeting_cancel(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.answer()
        await safe_edit(cb, "Bekor qilindi.")

    # ======================= 📅 Majlislar =======================
    async def meetings_view(db: Database, cfg: Config):
        now = datetime.now(cfg.tz)
        s = await db.settings()
        rules = Rules.from_settings(s)
        meetings = await db.meetings_between(now.date().isoformat(), (now.date() + timedelta(days=30)).isoformat())
        days = ", ".join(WEEKDAYS_SHORT[d] for d in sorted(rules.workdays))
        daily = (f"✅ har ish kuni ({days}) soat {s['meeting_time']}" if rules.daily_enabled else "❌ o'chirilgan")
        lines = ["📅 <b>Majlislar</b>", "", f"Kunlik majlis: {daily}", ""]
        if meetings:
            lines.append("<b>Bugun va kelgusi:</b>")
        else:
            lines.append("Bugun va kelgusida qo'shimcha majlis yo'q.")
        rows = []
        for m in meetings[:25]:
            _, close_dt = rules.window(meeting_start(m))
            mark = "✔️" if now > close_dt else ("🟢" if meeting_start(m) <= now else "🕒")
            lines.append(f"{mark} {esc(meeting_label(m))}")
            rows.append([(f"{mark} {meeting_label(m)}", f"mt:{m['id']}")])
        rows.append([("➕ Majlis qo'shish", "ma:new")])
        return "\n".join(lines), ikb(rows)

    @router.message(F.text == BTN_MEETINGS)
    async def meetings_list(message: Message, state: FSMContext, db: Database, cfg: Config):
        await state.clear()
        text, kb = await meetings_view(db, cfg)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data == "ml")
    async def meetings_list_cb(cb: CallbackQuery, db: Database, cfg: Config):
        await cb.answer()
        text, kb = await meetings_view(db, cfg)
        await safe_edit(cb, text, kb)

    @router.callback_query(F.data == "ma:new")
    async def meetings_add_cb(cb: CallbackQuery, state: FSMContext, cfg: Config):
        await cb.answer()
        await add_meeting(cb.message, state, cfg)

    @router.callback_query(F.data.startswith("mt:"))
    async def meeting_card(cb: CallbackQuery, db: Database, cfg: Config):
        await cb.answer()
        await show_meeting(cb, db, cfg, int(cb.data.split(":")[1]))

    async def show_meeting(cb: CallbackQuery, db: Database, cfg: Config, mid: int):
        m = await db.meeting(mid)
        if not m:
            return
        now = datetime.now(cfg.tz)
        rules = Rules.from_settings(await db.settings())
        start = meeting_start(m)
        opens, close_dt = rules.window(start)
        if m["cancelled"]:
            status = "❌ bekor qilingan"
        elif now > close_dt:
            status = "✔️ bo'lib o'tdi"
        elif now >= opens:
            status = "🟢 belgilanish ochiq"
        else:
            status = f"🕒 kutilmoqda (belgilanish {opens:%H:%M} da ochiladi)"
        att = await db.attendance_for_meeting(m["id"])
        came = sum(1 for r in att.values() if r["status"] in PRESENT)
        kind = "kunlik" if m["kind"] == "daily" else "qo'shimcha"
        emps = await db.meeting_employees(m["id"])
        everyone = not await db.participant_ids(m["id"])
        note = f"\n📝 {esc(m['title'])}" if m["title"] else ""
        text = (f"<b>{esc(meeting_label(m))}</b>{note}\n\nTuri: {kind}\n"
                f"Holati: {status}\n👥 Qatnashuvchilar: {people_summary([e['full_name'] for e in emps], everyone)}\n"
                f"Belgilanganlar: {came}")
        rows = []
        if now >= opens:
            rows.append([("📋 Davomat", f"rp:{m['id']}"), ("✏️ Tahrirlash", f"ed:{m['id']}:0")])
        if m["kind"] == "extra" and not m["cancelled"] and now <= close_dt:
            rows.append([("👥 Qatnashuvchilarni o'zgartirish", f"mpe:{m['id']}")])
        if not m["cancelled"] and now <= close_dt:
            rows.append([("🚫 Majlisni bekor qilish", f"mc:{m['id']}")])
        rows.append([("⬅️ Majlislar", "ml")])
        await safe_edit(cb, text, ikb(rows))

    @router.callback_query(F.data.startswith("mpe:"))
    async def people_edit_start(cb: CallbackQuery, state: FSMContext, db: Database):
        m = await db.meeting(int(cb.data.split(":")[1]))
        await cb.answer()
        if not m or m["cancelled"]:
            return
        current = await db.participant_ids(m["id"])
        sel = sorted(current) if current else [e["id"] for e in await db.employees()]
        await state.clear()
        await state.set_state(EditPeople.choose)
        await state.update_data(mid=m["id"], sel=sel)
        await safe_edit(cb, f"<b>{esc(meeting_label(m))}</b>\n\n" + PEOPLE_TEXT, await people_keyboard(db, set(sel), 0))

    async def apply_people_edit(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Database, cfg: Config,
                                people: list[int]):
        mid = (await state.get_data())["mid"]
        await state.clear()
        m = await db.meeting(mid)
        if not m:
            return
        before = {e["id"] for e in await db.meeting_employees(mid)}
        await db.set_participants(mid, people)
        after_emps = await db.meeting_employees(mid)
        after = {e["id"] for e in after_emps}
        added = [e for e in after_emps if e["id"] not in before and e["telegram_id"]]
        removed = [e for e in await db.employees() if e["id"] in before - after and e["telegram_id"]]
        now = datetime.now(cfg.tz)
        _, close_dt = Rules.from_settings(await db.settings()).window(meeting_start(m))
        if m["announced"] and added:
            await announce_meeting(bot, db, m, recipients=added)
        if m["reminded"] and added and now <= close_dt:
            await send_reminder(bot, db, cfg, m, now, recipients=added)
        if (m["announced"] or m["reminded"]) and removed:
            await broadcast(bot, db, f"ℹ️ Siz «{esc(meeting_label(m))}» majlisi qatnashuvchilari ro'yxatidan "
                                     f"chiqarildingiz. Bu majlisga kelishingiz shart emas.", recipients=removed)
        notes = [f"qo'shildi: {len(after - before)}", f"chiqarildi: {len(before - after)}"]
        await safe_edit(cb, f"✅ Qatnashuvchilar yangilandi ({', '.join(notes)}).\n"
                            "Yangi qo'shilganlarga e'lon yuborildi." if added and m["announced"] else
                            f"✅ Qatnashuvchilar yangilandi ({', '.join(notes)}).")
        await cb.message.answer("Majlis:", reply_markup=ikb([[(f"📅 {meeting_label(m)}", f"mt:{mid}")]]))

    @router.callback_query(F.data.startswith("mc:"))
    async def meeting_cancel_ask(cb: CallbackQuery, db: Database):
        m = await db.meeting(int(cb.data.split(":")[1]))
        await cb.answer()
        if m:
            await safe_edit(cb, f"«{esc(meeting_label(m))}» bekor qilinsinmi?\nHodimlarga xabar boradi.",
                            ikb([[("Ha, bekor qilish", f"mcy:{m['id']}"), ("Yo'q", f"mt:{m['id']}")]]))

    @router.callback_query(F.data.startswith("mcy:"))
    async def meeting_cancel_yes(cb: CallbackQuery, bot: Bot, db: Database):
        m = await db.meeting(int(cb.data.split(":")[1]))
        await cb.answer()
        if m and not m["cancelled"]:
            await cancel_meeting(bot, db, m)
            await safe_edit(cb, f"❌ Bekor qilindi: {esc(meeting_label(m))}", ikb([[("⬅️ Majlislar", "ml")]]))

    # ======================= 📊 Hisobot =======================
    @router.message(F.text == BTN_REPORT)
    async def report_menu(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("📊 Qaysi davr uchun Excel hisobot kerak?", reply_markup=ikb([
            [("Bugun", "rx:today"), ("Shu hafta", "rx:week")],
            [("Shu oy", "rx:month"), ("O'tgan oy", "rx:prev")],
        ]))

    @router.callback_query(F.data.startswith("rx:"))
    async def report_excel(cb: CallbackQuery, db: Database, cfg: Config):
        await cb.answer("Tayyorlanmoqda…")
        today = datetime.now(cfg.tz).date()
        kind = cb.data[3:]
        if kind == "today":
            d1 = d2 = today
        elif kind == "week":
            d1, d2 = today - timedelta(days=today.weekday()), today
        elif kind == "month":
            d1, d2 = today.replace(day=1), today
        else:
            d2 = today.replace(day=1) - timedelta(days=1)
            d1 = d2.replace(day=1)
        data = await build_excel(db, cfg, d1, d2)
        await cb.message.answer_document(
            BufferedInputFile(data, filename=f"davomat_{d1:%Y%m%d}_{d2:%Y%m%d}.xlsx"),
            caption=f"📊 Davomat: {d1:%d.%m.%Y} — {d2:%d.%m.%Y}")

    # ======================= 👥 Hodimlar =======================
    def is_emp_admin(e, cfg: Config) -> bool:
        return bool(e["is_admin"]) or (e["telegram_id"] is not None and e["telegram_id"] in cfg.admin_ids)

    async def employees_view(db: Database, cfg: Config, page: int, viewer: int):
        emps = await db.employees()
        linked = sum(1 for e in emps if e["telegram_id"])
        admins = sum(1 for e in emps if is_emp_admin(e, cfg))
        pages = max(1, (len(emps) + PAGE - 1) // PAGE)
        page = min(max(page, 0), pages - 1)
        text = (f"👥 <b>Hodimlar: {len(emps)}</b>\n🔗 botga ulangan: {linked}\n⭕ ulanmagan: {len(emps) - linked}\n"
                f"👑 adminlar: {admins}\n\nHodimni boshqarish uchun ismini bosing.")
        rows, row = [], []
        for e in emps[page * PAGE:(page + 1) * PAGE]:
            mark = "👑" if is_emp_admin(e, cfg) else ("🔗" if e["telegram_id"] else "⭕")
            row.append((f"{mark} {short_name(e['full_name'], 22)}", f"em:{e['id']}:{page}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        rows.append(row)
        if pages > 1:
            rows.append([("◀️", f"el:{page - 1}") if page > 0 else ("·", "noop"), (f"{page + 1}/{pages}", "noop"),
                         ("▶️", f"el:{page + 1}") if page < pages - 1 else ("·", "noop")])
        bottom = [("➕ Hodim qo'shish", "ea")]
        if is_super(cfg, viewer):
            bottom.append(("👑 Adminlar", "adl"))
        rows.append(bottom)
        return text, ikb(rows)

    @router.message(F.text == BTN_EMPLOYEES)
    async def employees_list(message: Message, state: FSMContext, db: Database, cfg: Config):
        await state.clear()
        text, kb = await employees_view(db, cfg, 0, message.from_user.id)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data.startswith("el:"))
    async def employees_page(cb: CallbackQuery, db: Database, cfg: Config):
        await cb.answer()
        text, kb = await employees_view(db, cfg, int(cb.data[3:]), cb.from_user.id)
        await safe_edit(cb, text, kb)

    @router.callback_query(F.data.startswith("em:"))
    async def employee_card(cb: CallbackQuery, db: Database, cfg: Config):
        _, eid, page = cb.data.split(":")
        await cb.answer()
        await show_employee(cb, db, cfg, int(eid), page)

    async def show_employee(cb: CallbackQuery, db: Database, cfg: Config, eid: int, page):
        e = await db.employee(eid)
        if not e or not e["active"]:
            await safe_edit(cb, "Hodim topilmadi.", ikb([[("⬅️ Ro'yxat", f"el:{page}")]]))
            return
        month_start = datetime.now(cfg.tz).date().replace(day=1).isoformat()
        hist = [h for h in await db.history(e["id"], 200) if h["start_at"][:10] >= month_start]
        came = sum(1 for h in hist if h["status"] in PRESENT)
        late = sum(1 for h in hist if h["status"] == "late")
        target_super = e["telegram_id"] is not None and e["telegram_id"] in cfg.admin_ids
        role = "👑 Bosh admin" if target_super else ("👑 Admin" if e["is_admin"] else "")
        text = (f"👤 <b>{esc(e['full_name'])}</b>\n📞 {fmt_phone(e['phone'])}\n"
                f"{'🔗 Botga ulangan' if e['telegram_id'] else '⭕ Botga hali kirmagan'}\n"
                + (f"{role}\n" if role else "") +
                f"Qo'shilgan: {e['created_at'][:10]}\n\nShu oy: {came} marta keldi, shundan {late} marta kech qoldi.")
        viewer_super = is_super(cfg, cb.from_user.id)
        rows = []
        if viewer_super and not target_super:
            if e["is_admin"]:
                rows.append([("➖ Adminlikdan olish", f"adr:{e['id']}:{page}")])
            else:
                rows.append([("👑 Admin qilish", f"adm:{e['id']}:{page}")])
        if viewer_super or not is_emp_admin(e, cfg):
            if not target_super:
                rows.append([("🗑 O'chirish", f"edl:{e['id']}:{page}")])
            if e["telegram_id"] and not target_super:
                rows[-1].append(("🔄 Akkauntni ajratish", f"eun:{e['id']}:{page}"))
        rows.append([("⬅️ Ro'yxat", f"el:{page}")])
        await safe_edit(cb, text, ikb(rows))

    @router.callback_query(F.data.startswith("edl:"))
    async def employee_delete_ask(cb: CallbackQuery, db: Database):
        _, eid, page = cb.data.split(":")
        e = await db.employee(int(eid))
        await cb.answer()
        if e:
            await safe_edit(cb, f"<b>{esc(e['full_name'])}</b> ro'yxatdan o'chirilsinmi?\nDavomat tarixi saqlanadi.",
                            ikb([[("Ha, o'chirish", f"edy:{e['id']}:{page}"), ("Yo'q", f"em:{e['id']}:{page}")]]))

    async def remove_employee(bot: Bot, db: Database, emp_id: int):
        row = await db.deactivate_employee_by_id(emp_id)
        if row and row["telegram_id"]:
            try:
                await bot.send_message(row["telegram_id"], "Siz hodimlar ro'yxatidan chiqarildingiz.")
                await bot.set_chat_menu_button(chat_id=row["telegram_id"], menu_button=MenuButtonDefault())
            except Exception:
                pass
        return row

    @router.callback_query(F.data.startswith("edy:"))
    async def employee_delete_yes(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        _, eid, page = cb.data.split(":")
        target = await db.employee(int(eid))
        if target and is_emp_admin(target, cfg) and not is_super(cfg, cb.from_user.id):
            await cb.answer("Adminni faqat bosh admin o'chira oladi.", show_alert=True)
            return
        row = await remove_employee(bot, db, int(eid))
        await cb.answer("O'chirildi" if row else "Allaqachon o'chirilgan")
        text, kb = await employees_view(db, cfg, int(page), cb.from_user.id)
        await safe_edit(cb, text, kb)

    @router.callback_query(F.data.startswith("emp_del:"))  # yangi ro'yxatdan o'tgan haqidagi xabardagi tugma
    async def emp_delete_from_notice(cb: CallbackQuery, bot: Bot, db: Database):
        row = await remove_employee(bot, db, int(cb.data.split(":", 1)[1]))
        await cb.answer("O'chirildi" if row else "Allaqachon o'chirilgan", show_alert=not row)
        try:
            await cb.message.edit_text(cb.message.html_text + f"\n\n❌ <b>O'chirildi</b> ({esc(cb.from_user.full_name)})")
        except Exception:
            pass

    @router.callback_query(F.data.startswith("eun:"))
    async def employee_unlink(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        _, eid, page = cb.data.split(":")
        target = await db.employee(int(eid))
        if target and is_emp_admin(target, cfg) and not is_super(cfg, cb.from_user.id):
            await cb.answer("Adminni faqat bosh admin o'zgartira oladi.", show_alert=True)
            return
        row = await db.unlink_telegram_by_id(int(eid))
        await cb.answer("Akkaunt ajratildi" if row else "Akkaunt ulanmagan")
        if row:
            try:
                await bot.send_message(row["telegram_id"], "Akkauntingiz hodimdan ajratildi. Qayta ulanish uchun /start bosing.")
            except Exception:
                pass
        text, kb = await employees_view(db, cfg, int(page), cb.from_user.id)
        await safe_edit(cb, text, kb)

    # ---------- 👑 adminlar (faqat bosh admin) ----------
    async def admins_view(db: Database, cfg: Config, bot: Bot):
        helpers = await db.helper_admins()
        lines = ["👑 <b>Adminlar</b>", "", "<b>Bosh admin</b> (sozlamalar, QR'ni yangilash, admin tayinlash):"]
        for tg in cfg.admin_ids:
            e = await db.employee_by_tg(tg)
            lines.append(f"• {esc(e['full_name']) if e else f'Telegram ID {tg}'}")
        lines += ["", "<b>Adminlar</b> (sozlamalar va QR'ni yangilashdan boshqa hamma narsa):"]
        lines += [f"• {esc(e['full_name'])} — {fmt_phone(e['phone'])}" for e in helpers] or ["— hali yo'q"]
        lines += ["", "Yangi admin qo'shish: <b>👥 Hodimlar</b> → hodimni tanlang → <b>👑 Admin qilish</b>.\n"
                  "Hodim avval botga kirib ro'yxatdan o'tgan bo'lishi kerak."]
        rows = [[(f"➖ {short_name(e['full_name'], 24)} — adminlikdan olish", f"adr:{e['id']}:0")] for e in helpers]
        rows.append([("⬅️ Hodimlar", "el:0")])
        return "\n".join(lines), ikb(rows)

    @router.callback_query(F.data == "adl", IsSuper())
    async def admins_list(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        await cb.answer()
        text, kb = await admins_view(db, cfg, bot)
        await safe_edit(cb, text, kb)

    @router.callback_query(F.data.startswith("adm:"), IsSuper())
    async def admin_grant(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        _, eid, page = cb.data.split(":")
        e = await db.employee(int(eid))
        if not e or not e["active"]:
            await cb.answer("Hodim topilmadi.", show_alert=True)
            return
        if not e["telegram_id"]:
            await cb.answer("Bu hodim hali botga kirmagan. Avval u botga kirib ro'yxatdan o'tsin.", show_alert=True)
            return
        await db.set_admin(e["id"], True)
        await cb.answer(f"{e['full_name']} admin qilindi")
        try:
            await bot.send_message(
                e["telegram_id"],
                "👑 <b>Siz admin qilindingiz</b>\n\nPastdagi menyu orqali davomat, majlislar, hisobot va hodimlarni "
                "boshqarasiz. Majlisga o'zingiz ham avvalgidek QR orqali belgilanasiz.",
                reply_markup=admin_menu(False))
        except Exception:
            pass
        await show_employee(cb, db, cfg, e["id"], page)

    @router.callback_query(F.data.startswith("adr:"), IsSuper())
    async def admin_revoke(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        _, eid, page = cb.data.split(":")
        e = await db.employee(int(eid))
        if not e or not e["is_admin"]:
            await cb.answer("Bu hodim admin emas.")
            return
        await db.set_admin(e["id"], False)
        await cb.answer(f"{e['full_name']} adminlikdan olindi")
        if e["telegram_id"]:
            try:
                await bot.send_message(e["telegram_id"], "Adminlik huquqingiz olib tashlandi. Majlisga avvalgidek "
                                                         "botdagi tugma orqali belgilanasiz.",
                                       reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
        if cb.message and cb.message.text and cb.message.text.startswith("👑 Adminlar"):
            text, kb = await admins_view(db, cfg, bot)
            await safe_edit(cb, text, kb)
        else:
            await show_employee(cb, db, cfg, e["id"], page)

    @router.callback_query(F.data == "ea")
    async def employee_add(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.set_state(AddEmployees.lines)
        await cb.message.answer(
            "Botga kirmagan hodimlarni yozing — har qatorda telefon va familiya-ism:\n\n"
            "<code>+998901234567 Aliyev Vali\n+998911112233 Karimova Nilufar</code>\n\n"
            "Ular keyin botga kirsa, ism so'ralmaydi — raqami bo'yicha darhol ulanadi.\n"
            "Ism xato bo'lsa, shu yo'l bilan to'g'rilash ham mumkin.",
            reply_markup=ikb([[("❌ Bekor qilish", "ma:x")]]))

    @router.message(AddEmployees.lines, F.text, NOT_MENU)
    async def employee_add_lines(message: Message, state: FSMContext, db: Database, cfg: Config):
        now_iso = datetime.now(cfg.tz).isoformat(timespec="seconds")
        added, updated, bad = 0, 0, []
        for line in message.text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^([+\d][\d\s\-()]{7,}\d)\s+(\D.+)$", line)
            phone = normalize_phone(m.group(1)) if m else ""
            if not m or not (10 <= len(phone) <= 15):
                bad.append(line)
                continue
            if await db.add_employee(phone, " ".join(m.group(2).split()), now_iso) == "added":
                added += 1
            else:
                updated += 1
        await state.clear()
        parts = [f"✅ Qo'shildi: {added}"] + ([f"♻️ Yangilandi: {updated}"] if updated else [])
        if bad:
            parts.append("⚠️ Tushunilmadi:\n" + "\n".join(esc(b) for b in bad))
        await message.answer("\n".join(parts))
        text, kb = await employees_view(db, cfg, 0, message.from_user.id)
        await message.answer(text, reply_markup=kb)

    # ======================= ⚙️ Sozlamalar =======================
    CHOICES = {
        "remind_before": ("🔔 Majlisdan necha daqiqa oldin kirish tugmasi yuborilsin?", [10, 15, 20, 30], "daq"),
        "late_after": ("⏰ Majlis boshlanganidan necha daqiqa keyin «kech qoldi» hisoblansin?", [0, 5, 10, 15], "daq"),
        "close_after": ("⌛ Majlis boshlanganidan necha daqiqa keyin belgilanish yopilsin?", [30, 60, 90, 120], "daq"),
        "radius": ("📏 Ofisdan qancha masofagacha ruxsat berilsin?", [50, 100, 150, 200, 300, 500], "m"),
    }

    async def settings_view(db: Database):
        s = await db.settings()
        rules = Rules.from_settings(s)
        days = ", ".join(WEEKDAYS_SHORT[d] for d in sorted(rules.workdays)) or "—"
        daily_state = "✅ yoqilgan" if rules.daily_enabled else "❌ o'chirilgan"
        office = f"{float(s['office_lat']):.5f}, {float(s['office_lon']):.5f}" if rules.office else "❗ belgilanmagan"
        text = ("⚙️ <b>Sozlamalar</b>\n\n"
                f"🕗 Kunlik majlis: <b>{s['meeting_time']}</b> ({daily_state})\n"
                f"📅 Majlis kunlari: {days}\n"
                f"🔔 Kirish tugmasi: majlisdan {s['remind_before']} daq oldin\n"
                f"⏰ Kech qoldi: {s['late_after']} daqiqadan keyin\n"
                f"⌛ Belgilanish yopiladi: majlisdan {s['close_after']} daq keyin\n"
                f"📍 Ofis: {office}\n"
                f"📏 Ruxsat etilgan masofa: {s['radius']} m\n"
                f"🔳 QR: har {s['qr_rotate_days']} kunda soat {s['qr_rotate_time']} da yangilanadi")
        kb = ikb([
            [("🕗 Majlis vaqti", "st:time"), (f"Kunlik: {'✅' if rules.daily_enabled else '❌'}", "st:daily")],
            [("📅 Majlis kunlari", "st:days"), ("🔔 Eslatma vaqti", "st:remind_before")],
            [("⏰ Kechikish", "st:late_after"), ("⌛ Yopilish", "st:close_after")],
            [("📍 Ofis joylashuvi", "st:office"), ("📏 Masofa", "st:radius")],
        ])
        return text, kb

    @router.message(F.text == BTN_SETTINGS, IsSuper())
    async def settings_menu(message: Message, state: FSMContext, db: Database):
        await state.clear()
        text, kb = await settings_view(db)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data == "st:back", IsSuper())
    async def settings_back(cb: CallbackQuery, db: Database):
        await cb.answer()
        text, kb = await settings_view(db)
        await safe_edit(cb, text, kb)

    @router.callback_query(F.data.in_({f"st:{k}" for k in CHOICES}), IsSuper())
    async def settings_choice(cb: CallbackQuery, db: Database):
        key = cb.data[3:]
        title, values, unit = CHOICES[key]
        cur = (await db.settings())[key]
        await cb.answer()
        btns = [(f"{'✅ ' if str(v) == cur else ''}{v} {unit}", f"sv:{key}:{v}") for v in values]
        await safe_edit(cb, title, ikb([btns[i:i + 3] for i in range(0, len(btns), 3)] + [[("⬅️ Orqaga", "st:back")]]))

    @router.callback_query(F.data.startswith("sv:"), IsSuper())
    async def settings_set(cb: CallbackQuery, db: Database):
        _, key, value = cb.data.split(":")
        if key not in CHOICES or int(value) not in CHOICES[key][1]:
            await cb.answer("Noto'g'ri qiymat")
            return
        await db.set_setting(key, value)
        await cb.answer("Saqlandi")
        text, kb = await settings_view(db)
        await safe_edit(cb, text, kb)

    @router.callback_query(F.data == "st:daily", IsSuper())
    async def settings_daily(cb: CallbackQuery, db: Database, cfg: Config):
        s = await db.settings()
        enable = s.get("daily_enabled", "1") != "1"
        await db.set_setting("daily_enabled", "1" if enable else "0")
        note = "Kunlik majlis yoqildi"
        if not enable:
            note = "Kunlik majlis o'chirildi"
            today = await db.daily_meeting(datetime.now(cfg.tz).date().isoformat())
            if today and not today["reminded"] and not today["cancelled"]:
                await db.set_meeting_flag(today["id"], "cancelled")
        await cb.answer(note)
        text, kb = await settings_view(db)
        await safe_edit(cb, text, kb)

    async def days_keyboard(db: Database):
        rules = Rules.from_settings(await db.settings())
        btns = [(f"{'✅' if i in rules.workdays else '▫️'} {WEEKDAYS_SHORT[i]}", f"sd:{i}") for i in range(7)]
        return ikb([btns[:4], btns[4:], [("⬅️ Orqaga", "st:back")]])

    @router.callback_query(F.data == "st:days", IsSuper())
    async def settings_days(cb: CallbackQuery, db: Database):
        await cb.answer()
        await safe_edit(cb, "📅 Kunlik majlis qaysi kunlari bo'ladi? Bosib yoqing/o'chiring.", await days_keyboard(db))

    @router.callback_query(F.data.startswith("sd:"), IsSuper())
    async def settings_day_toggle(cb: CallbackQuery, db: Database):
        d = int(cb.data[3:])
        days = set(Rules.from_settings(await db.settings()).workdays)
        days.symmetric_difference_update({d})
        if not days:
            await cb.answer("Kamida bitta kun bo'lishi kerak", show_alert=True)
            return
        await db.set_setting("workdays", ",".join(str(x) for x in sorted(days)))
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=await days_keyboard(db))

    @router.callback_query(F.data == "st:time", IsSuper())
    async def settings_time(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.set_state(SetTime.waiting)
        await cb.message.answer("🕗 Kunlik majlis vaqtini yozing, masalan <code>08:00</code>",
                                reply_markup=ikb([[("❌ Bekor qilish", "ma:x")]]))

    @router.message(SetTime.waiting, F.text, NOT_MENU, IsSuper())
    async def settings_time_set(message: Message, state: FSMContext, db: Database, cfg: Config):
        t = parse_hhmm(message.text)
        if not t:
            await message.answer("Shunday yozing: <code>08:00</code>")
            return
        await db.set_setting("meeting_time", f"{t:%H:%M}")
        await state.clear()
        now = datetime.now(cfg.tz)
        note = ""
        today = await db.daily_meeting(now.date().isoformat())
        if today and not today["cancelled"]:
            new_start = datetime.combine(now.date(), t, tzinfo=cfg.tz)
            if not today["reminded"] and new_start > now:
                await db.update_meeting_start(today["id"], new_start)
                note = "\nBugungi majlis ham shu vaqtga ko'chirildi."
            else:
                note = "\nBugungi majlisga ta'sir qilmaydi (u allaqachon boshlangan yoki eslatma yuborilgan)."
        rules = Rules.from_settings(await db.settings())
        remind = datetime.combine(now.date(), t) - timedelta(minutes=rules.remind_before)
        await message.answer(f"✅ Kunlik majlis: {t:%H:%M}. Kirish tugmasi har kuni {remind:%H:%M} da boradi.{note}")
        text, kb = await settings_view(db)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data == "st:office", IsSuper())
    async def settings_office(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.set_state(SetOffice.waiting)
        await cb.message.answer(
            "📍 Majlis xonasida turib, pastdagi tugmani bosing.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📍 Shu yerni ofis deb belgilash", request_location=True)],
                          [KeyboardButton(text="Bekor qilish")]],
                resize_keyboard=True, one_time_keyboard=True))

    @router.message(SetOffice.waiting, F.location, IsSuper())
    async def settings_office_set(message: Message, state: FSMContext, db: Database):
        loc = message.location
        await db.set_setting("office_lat", f"{loc.latitude:.6f}")
        await db.set_setting("office_lon", f"{loc.longitude:.6f}")
        await state.clear()
        acc = f" (aniqlik ±{loc.horizontal_accuracy:.0f} m)" if loc.horizontal_accuracy else ""
        await message.answer(f"✅ Ofis joylashuvi saqlandi{acc}", reply_markup=admin_menu(True))
        await message.answer_location(loc.latitude, loc.longitude)

    @router.message(SetOffice.waiting, IsSuper())
    async def settings_office_cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu(True))

    # ======================= 🔳 QR =======================
    @router.message(F.text == BTN_QR)
    async def qr_menu(message: Message, state: FSMContext, db: Database, cfg: Config):
        await state.clear()
        s = await db.settings()
        png = make_qr_png(qr_payload(s["qr_secret"]), QR_CAPTION)
        nxt = next_qr_rotation(s, cfg.tz)
        await message.answer_document(
            BufferedInputFile(png, filename="majlis_qr.png"),
            caption=(f"🔳 Joriy QR kod. Chop etib, majlis xonasiga osing.\n\n"
                     f"Amal qiladi: {nxt:%d.%m.%Y %H:%M} gacha — keyin avtomatik yangilanadi va yangisi sizga keladi."),
            reply_markup=ikb([[("♻️ Hozir yangilash", "qr:new")]]) if is_super(cfg, message.from_user.id) else None)

    @router.callback_query(F.data == "qr:new", IsSuper())
    async def qr_new_ask(cb: CallbackQuery):
        await cb.answer()
        await cb.message.answer("QR hozir yangilansinmi? Eski QR darhol ishlamay qoladi.",
                                reply_markup=ikb([[("Ha, yangilash", "qr:yes"), ("Yo'q", "ma:x")]]))

    @router.callback_query(F.data == "qr:yes", IsSuper())
    async def qr_new(cb: CallbackQuery, bot: Bot, db: Database, cfg: Config):
        await cb.answer("Yangilanmoqda…")
        try:
            await cb.message.delete()
        except Exception:
            pass
        await rotate_qr(bot, db, cfg, datetime.now(cfg.tz), "admin tomonidan")

    # Menyudan tashqari matn — faqat holat (state) bo'lmaganda
    @router.message(StateFilter(None), F.text, ~F.text.in_(ADMIN_BUTTONS), ~F.text.startswith("/"))
    async def admin_fallback(message: Message, cfg: Config):
        await message.answer("Pastdagi menyudan tanlang 👇", reply_markup=admin_menu(is_super(cfg, message.from_user.id)))

    @router.message(F.text == BTN_SETTINGS)
    async def settings_denied(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("⚙️ Sozlamalarni faqat bosh admin o'zgartira oladi.", reply_markup=admin_menu(False))

    @router.callback_query(F.data.regexp(r"^(st|sv|sd|qr|adm|adr|adl)(:|$)"))
    async def super_only(cb: CallbackQuery):
        await cb.answer("Bu amal faqat bosh admin uchun.", show_alert=True)

    return router
