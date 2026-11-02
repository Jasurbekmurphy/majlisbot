from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
                           WebAppInfo)

# Admin asosiy menyusi (pastki doimiy tugmalar)
BTN_ATTENDANCE = "📋 Davomat"
BTN_ADD_MEETING = "➕ Majlis qo'shish"
BTN_MEETINGS = "📅 Majlislar"
BTN_REPORT = "📊 Hisobot"
BTN_EMPLOYEES = "👥 Hodimlar"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_QR = "🔳 QR kod"
ADMIN_BUTTONS = {BTN_ATTENDANCE, BTN_ADD_MEETING, BTN_MEETINGS, BTN_REPORT, BTN_EMPLOYEES, BTN_SETTINGS, BTN_QR}


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ATTENDANCE), KeyboardButton(text=BTN_ADD_MEETING)],
            [KeyboardButton(text=BTN_MEETINGS), KeyboardButton(text=BTN_REPORT)],
            [KeyboardButton(text=BTN_EMPLOYEES), KeyboardButton(text=BTN_SETTINGS)],
            [KeyboardButton(text=BTN_QR)],
        ],
        # is_persistent=False: menyuni yozish maydonidagi ⌨️ belgisi bilan yopib-ochish mumkin
        resize_keyboard=True, is_persistent=False,
        input_field_placeholder="Menyu: ⌨️ belgisi bilan yopiladi/ochiladi",
    )


def checkin_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📷 Majlisga kirish", web_app=WebAppInfo(url=webapp_url))
    ]])


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def ikb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """[[(matn, callback_data), ...], ...] → InlineKeyboardMarkup"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows if row
    ])
