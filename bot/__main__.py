"""Ishga tushirish: python -m bot"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiohttp import web

from .config import Config, load_config
from .db import Database
from .handlers import admin, user
from .scheduler import run_scheduler
from .web import create_web_app

log = logging.getLogger("davomat")


async def setup_commands(bot: Bot, cfg: Config):
    await bot.set_my_commands([BotCommand(command="start", description="Boshlash")], scope=BotCommandScopeDefault())
    for admin_id in cfg.admin_ids:
        try:
            await bot.set_my_commands([BotCommand(command="start", description="Admin paneli")],
                                      scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            log.warning("Admin %s uchun buyruqlar o'rnatilmadi (u botga /start bosmagan bo'lishi mumkin)", admin_id)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    db = Database(cfg.db_path, cfg.tz)
    await db.connect()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), db=db, cfg=cfg)
    dp.include_router(admin.make_router(cfg.admin_ids))  # admin birinchi bo'lishi kerak
    dp.include_router(user.router)

    runner = web.AppRunner(create_web_app(cfg, db, bot))
    await runner.setup()
    await web.TCPSite(runner, cfg.web_host, cfg.web_port).start()
    log.info("Mini App serveri: http://%s:%s/app", cfg.web_host, cfg.web_port)

    try:
        await setup_commands(bot, cfg)
    except Exception:
        log.exception("Bot buyruqlarini o'rnatib bo'lmadi (token to'g'rimi?)")

    scheduler_task = asyncio.create_task(run_scheduler(bot, db, cfg))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
