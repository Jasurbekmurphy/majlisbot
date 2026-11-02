from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonWebApp,
                           Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo)

from ..config import Config
from ..db import Database
from ..utils import WEEKDAYS_SHORT, esc, normalize_phone

router = Router(name="user")


def checkin_keyboard(cfg: Config) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📷 Majlisga kirish (QR skanerlash)", web_app=WebAppInfo(url=cfg.webapp_url))
    ]])


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


async def set_menu_button(bot: Bot, chat_id: int, cfg: Config):
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text="Majlisga kirish", web_app=WebAppInfo(url=cfg.webapp_url)),
        )
    except Exception:
        pass


@router.message(CommandStart())
async def start(message: Message, bot: Bot, db: Database, cfg: Config):
    emp = await db.employee_by_tg(message.from_user.id)
    if emp and emp["active"]:
        await set_menu_button(bot, message.chat.id, cfg)
        await message.answer(
            f"Assalomu alaykum, <b>{esc(emp['full_name'])}</b>!\n\n"
            "Majlisga kelganingizda pastdagi tugmani bosing va xonadagi QR kodni skanerlang.",
            reply_markup=checkin_keyboard(cfg),
        )
        return
    await message.answer(
        "Assalomu alaykum! Bu bot majlis davomatini qayd etadi.\n\n"
        "Ro'yxatdan o'tish uchun pastdagi tugma orqali telefon raqamingizni yuboring.",
        reply_markup=contact_keyboard(),
    )


@router.message(F.contact)
async def got_contact(message: Message, bot: Bot, db: Database, cfg: Config):
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer("Iltimos, boshqa odamning emas, <b>o'zingizning</b> raqamingizni tugma orqali yuboring.",
                             reply_markup=contact_keyboard())
        return

    phone = normalize_phone(contact.phone_number)
    emp = await db.employee_by_phone(phone)
    if not emp or not emp["active"]:
        await message.answer(
            f"+{phone} raqami hodimlar ro'yxatida topilmadi.\nAdminga murojaat qiling.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if emp["telegram_id"] and emp["telegram_id"] != message.from_user.id:
        await message.answer(
            "Bu raqam boshqa Telegram akkauntga bog'langan. Akkauntni almashtirish uchun adminga murojaat qiling.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    old = await db.employee_by_tg(message.from_user.id)
    if old and old["id"] != emp["id"]:
        await message.answer("Bu Telegram akkaunt boshqa hodimga bog'langan. Adminga murojaat qiling.",
                             reply_markup=ReplyKeyboardRemove())
        return

    await db.link_telegram(emp["id"], message.from_user.id)
    await set_menu_button(bot, message.chat.id, cfg)
    await message.answer(f"✅ Ro'yxatdan o'tdingiz: <b>{esc(emp['full_name'])}</b>",
                         reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "Majlisga kelganingizda shu tugmani (yoki pastdagi «Majlisga kirish» menyusini) bosing "
        "va xonadagi QR kodni skanerlang. Telefonda GPS yoqilgan bo'lishi kerak.",
        reply_markup=checkin_keyboard(cfg),
    )


@router.message(Command("kirish"))
async def kirish(message: Message, db: Database, cfg: Config):
    emp = await db.employee_by_tg(message.from_user.id)
    if not emp:
        await message.answer("Avval ro'yxatdan o'ting: /start")
        return
    await message.answer("Xonadagi QR kodni skanerlash uchun tugmani bosing:", reply_markup=checkin_keyboard(cfg))


@router.message(Command("tarix"))
async def tarix(message: Message, db: Database):
    emp = await db.employee_by_tg(message.from_user.id)
    if not emp:
        await message.answer("Avval ro'yxatdan o'ting: /start")
        return
    rows = await db.history(emp["id"], 15)
    if not rows:
        await message.answer("Hozircha belgilanishlar yo'q.")
        return
    lines = ["<b>Oxirgi belgilanishlaringiz:</b>"]
    for r in rows:
        d = date.fromisoformat(r["day"])
        mark = "⏰" if r["status"] == "late" else "✅"
        extra = f" (+{r['late_min']} daq)" if r["status"] == "late" else ""
        lines.append(f"{mark} {d:%d.%m} {WEEKDAYS_SHORT[d.weekday()]} — {r['checked_at'][11:19]}{extra}")
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private")
async def fallback(message: Message, db: Database, cfg: Config):
    emp = await db.employee_by_tg(message.from_user.id)
    if emp:
        await message.answer("Majlisga belgilanish uchun tugmani bosing:", reply_markup=checkin_keyboard(cfg))
    else:
        await message.answer("Ro'yxatdan o'tish uchun /start bosing.")
