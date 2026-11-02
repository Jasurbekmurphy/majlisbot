import logging
import re
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message,
                           ReplyKeyboardRemove, WebAppInfo)

from ..config import Config
from ..db import Database
from ..keyboards import admin_menu, checkin_keyboard, contact_keyboard
from ..utils import WEEKDAYS_SHORT, esc, fmt_phone, normalize_phone

log = logging.getLogger(__name__)
router = Router(name="user")

# Harflar (lotin/kirill), apostrof turlari, tire va bo'shliq
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ'‘’ʻʼ`\-]+(?:\s+[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ'‘’ʻʼ`\-]+){1,3}$")


class Registration(StatesGroup):
    waiting_name = State()


def admin_employee_keyboard(employee_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 O'chirish (begona odam)", callback_data=f"emp_del:{employee_id}")
    ]])


async def set_menu_button(bot: Bot, chat_id: int, cfg: Config):
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text="Majlisga kirish", web_app=WebAppInfo(url=cfg.webapp_url)),
        )
    except Exception:
        pass


WELCOME = ("Majlisdan oldin shu yerga botdan xabar keladi. Xonaga kelgach, xabardagi "
           "<b>«📷 Majlisga kirish»</b> tugmasini bosing — joylashuv tekshiriladi va kamera ochiladi, "
           "QR kodni skanerlasangiz bo'ldi.\n\nTelefoningizda joylashuv (GPS) yoqilgan bo'lsin.")


async def finish_registration(message: Message, bot: Bot, cfg: Config, full_name: str):
    await set_menu_button(bot, message.chat.id, cfg)
    is_admin = message.from_user.id in cfg.admin_ids
    await message.answer(f"✅ Ro'yxatdan o'tdingiz: <b>{esc(full_name)}</b>",
                         reply_markup=admin_menu() if is_admin else ReplyKeyboardRemove())
    await message.answer(WELCOME, reply_markup=checkin_keyboard(cfg.webapp_url))


@router.message(CommandStart())
async def start(message: Message, bot: Bot, state: FSMContext, db: Database, cfg: Config):
    await state.clear()
    emp = await db.employee_by_tg(message.from_user.id)
    if emp and emp["active"]:
        await set_menu_button(bot, message.chat.id, cfg)
        await message.answer(
            f"Assalomu alaykum, <b>{esc(emp['full_name'])}</b>!\n\n" + WELCOME,
            reply_markup=checkin_keyboard(cfg.webapp_url),
        )
        return
    await message.answer(
        "Assalomu alaykum! Bu bot majlis davomatini qayd etadi.\n\n"
        "Ro'yxatdan o'tish uchun pastdagi tugma orqali telefon raqamingizni yuboring.",
        reply_markup=contact_keyboard(),
    )


@router.message(F.contact)
async def got_contact(message: Message, bot: Bot, state: FSMContext, db: Database, cfg: Config):
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer("Iltimos, boshqa odamning emas, <b>o'zingizning</b> raqamingizni tugma orqali yuboring.",
                             reply_markup=contact_keyboard())
        return

    phone = normalize_phone(contact.phone_number)
    tg_id = message.from_user.id
    emp = await db.employee_by_phone(phone)
    linked = await db.employee_by_tg(tg_id)

    if linked and (not emp or linked["id"] != emp["id"]):
        await message.answer("Bu Telegram akkaunt boshqa hodimga bog'langan. Adminga murojaat qiling.",
                             reply_markup=ReplyKeyboardRemove())
        return

    if emp:
        if not emp["active"]:
            await message.answer("Siz hodimlar ro'yxatidan chiqarilgansiz. Adminga murojaat qiling.",
                                 reply_markup=ReplyKeyboardRemove())
            return
        if emp["telegram_id"] and emp["telegram_id"] != tg_id:
            await message.answer(
                "Bu raqam boshqa Telegram akkauntga bog'langan. Akkauntni almashtirish uchun adminga murojaat qiling.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        # Admin oldindan qo'shgan hodim — ismini so'ramasdan ulaymiz
        await db.link_telegram(emp["id"], tg_id)
        await state.clear()
        await finish_registration(message, bot, cfg, emp["full_name"])
        return

    # Yangi hodim — ism-familiyasini so'raymiz
    await state.set_state(Registration.waiting_name)
    await state.update_data(phone=phone)
    await message.answer(
        "Rahmat! Endi <b>familiya va ismingizni</b> yozing.\n\nMasalan: <i>Aliyev Vali</i>",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Registration.waiting_name, F.text)
async def got_name(message: Message, bot: Bot, state: FSMContext, db: Database, cfg: Config):
    name = " ".join(message.text.split())
    if not NAME_RE.fullmatch(name) or not (5 <= len(name) <= 60):
        await message.answer(
            "Iltimos, familiya va ismingizni faqat harflar bilan, bo'sh joy qoldirib yozing.\n\n"
            "Masalan: <i>Aliyev Vali</i>"
        )
        return
    name = " ".join(w[:1].upper() + w[1:] for w in name.split())

    data = await state.get_data()
    phone = data.get("phone")
    if not phone:
        await state.clear()
        await message.answer("Qaytadan boshlang: /start")
        return

    tg_id = message.from_user.id
    emp = await db.employee_by_phone(phone)
    if emp and emp["telegram_id"] and emp["telegram_id"] != tg_id:
        await state.clear()
        await message.answer("Bu raqam boshqa akkauntga bog'langan. Adminga murojaat qiling.")
        return
    if not emp:
        now_iso = datetime.now(cfg.tz).isoformat(timespec="seconds")
        await db.add_employee(phone, name, now_iso)
        emp = await db.employee_by_phone(phone)
    await db.link_telegram(emp["id"], tg_id)
    await state.clear()
    await finish_registration(message, bot, cfg, emp["full_name"])

    username = f" (@{esc(message.from_user.username)})" if message.from_user.username else ""
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 <b>Yangi hodim ro'yxatdan o'tdi</b>\n\n{esc(emp['full_name'])}\n"
                f"{fmt_phone(phone)}{username}\n\nAgar u hodimingiz bo'lmasa, o'chirib tashlang.",
                reply_markup=admin_employee_keyboard(emp["id"]),
            )
        except Exception:
            log.warning("Adminga (%s) yangi hodim haqida xabar yuborilmadi", admin_id)


@router.message(Registration.waiting_name)
async def name_not_text(message: Message):
    await message.answer("Familiya va ismingizni matn ko'rinishida yozing. Masalan: <i>Aliyev Vali</i>")


@router.message(Command("kirish"))
async def kirish(message: Message, db: Database, cfg: Config):
    emp = await db.employee_by_tg(message.from_user.id)
    if not emp:
        await message.answer("Avval ro'yxatdan o'ting: /start")
        return
    await message.answer("Xonadagi QR kodni skanerlash uchun tugmani bosing:", reply_markup=checkin_keyboard(cfg.webapp_url))


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
        if r["status"] == "removed":
            continue
        d = date.fromisoformat(r["start_at"][:10])
        mark = "⏰" if r["status"] == "late" else "✅"
        extra = f" (+{r['late_min']} daq)" if r["status"] == "late" else ""
        lines.append(f"{mark} {d:%d.%m} {WEEKDAYS_SHORT[d.weekday()]} — {r['checked_at'][11:19]}{extra}")
    await message.answer("\n".join(lines))


@router.message(F.chat.type == "private")
async def fallback(message: Message, db: Database, cfg: Config):
    emp = await db.employee_by_tg(message.from_user.id)
    if emp:
        await message.answer("Majlisdan oldin botdan xabar keladi. Hozir majlis bo'layotgan bo'lsa, tugmani bosing:",
                             reply_markup=checkin_keyboard(cfg.webapp_url))
    else:
        await message.answer("Ro'yxatdan o'tish uchun /start bosing.")
