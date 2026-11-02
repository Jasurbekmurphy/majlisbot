"""Ishga tushirish: python -m bot"""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiohttp import web

from .checkin import Schedule
from .config import Config, load_config
from .db import Database
from .handlers import admin, user
from .reports import day_report_text
from .web import create_web_app

log = logging.getLogger("davomat")

USER_COMMANDS = [
    BotCommand(command="kirish", description="Majlisga kirish (QR skanerlash)"),
    BotCommand(command="tarix", description="Mening belgilanishlarim"),
    BotCommand(command="start", description="Qayta boshlash"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="bugun", description="Bugungi davomat"),
    BotCommand(command="hisobot", description="Excel hisobot"),
    BotCommand(command="urinishlar", description="Rad etilgan urinishlar"),
    BotCommand(command="hodimlar", description="Hodimlar ro'yxati"),
    BotCommand(command="hodim_qosh", description="Hodim qo'shish"),
    BotCommand(command="sozlamalar", description="Sozlamalar"),
    BotCommand(command="qr", description="QR kodni olish"),
    BotCommand(command="admin", description="Barcha admin buyruqlari"),
]


async def setup_commands(bot: Bot, cfg: Config):
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in cfg.admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            log.warning("Admin %s uchun buyruqlar o'rnatilmadi (u botga /start bosmagan bo'lishi mumkin)", admin_id)


async def daily_report_loop(bot: Bot, db: Database, cfg: Config):
    """Har kuni belgilanish oynasi yopilgach adminlarga hisobot yuboradi."""
    while True:
        try:
            now = datetime.now(cfg.tz)
            s = await db.settings()
            sched = Schedule.from_settings(s)
            today = now.date()
            if sched.is_workday(today) and s.get("last_report_day") != today.isoformat():
                _, _, close_dt = sched.window(today, cfg.tz)
                if now >= close_dt:
                    chunks = await day_report_text(db, cfg, today, now)
                    for admin_id in cfg.admin_ids:
                        for chunk in chunks:
                            try:
                                await bot.send_message(admin_id, chunk)
                            except Exception:
                                log.warning("Adminga (%s) hisobot yuborilmadi", admin_id)
                    await db.set_setting("last_report_day", today.isoformat())
        except Exception:
            log.exception("Kunlik hisobotda xatolik")
        await asyncio.sleep(30)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    db = Database(cfg.db_path)
    await db.connect()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), db=db, cfg=cfg)
    dp.include_router(admin.make_router(cfg.admin_ids))  # admin birinchi bo'lishi kerak
    dp.include_router(user.router)

    await setup_commands(bot, cfg)

    runner = web.AppRunner(create_web_app(cfg, db, bot))
    await runner.setup()
    await web.TCPSite(runner, cfg.web_host, cfg.web_port).start()
    log.info("Mini App serveri: http://%s:%s/app", cfg.web_host, cfg.web_port)

    report_task = asyncio.create_task(daily_report_loop(bot, db, cfg))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        report_task.cancel()
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
