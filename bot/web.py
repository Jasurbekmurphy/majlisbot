"""Mini App sahifasi va /api/checkin endpointi."""
import logging
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from .checkin import process_checkin
from .config import Config
from .db import Database

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).parent / "webapp"
_last_request: dict[int, float] = {}  # oddiy "flood" himoyasi


def _num(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


async def index(request: web.Request):
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-store"})


async def health(request: web.Request):
    return web.json_response({"ok": True})


async def checkin(request: web.Request):
    cfg: Config = request.app["cfg"]
    db: Database = request.app["db"]
    bot: Bot | None = request.app.get("bot")

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "message": "Noto'g'ri so'rov."}, status=400)

    # 1. So'rov haqiqatan Telegram'dan, shu foydalanuvchidan kelganini tekshiramiz
    try:
        data = safe_parse_webapp_init_data(cfg.bot_token, body.get("initData") or "")
    except ValueError:
        return web.json_response(
            {"ok": False, "message": "Telegram ma'lumotlari tasdiqlanmadi. Botdan qayta oching."}, status=401
        )
    auth_date = data.auth_date
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - auth_date).total_seconds() > cfg.init_data_ttl:
        return web.json_response(
            {"ok": False, "message": "Sahifa eskirgan. Botdagi tugma orqali qayta oching."}, status=401
        )
    if not data.user:
        return web.json_response({"ok": False, "message": "Foydalanuvchi aniqlanmadi."}, status=401)

    tg_id = data.user.id
    now_mono = _time.monotonic()
    if now_mono - _last_request.get(tg_id, 0) < 3:
        return web.json_response({"ok": False, "message": "Biroz kuting va qayta urinib ko'ring."}, status=429)
    _last_request[tg_id] = now_mono

    result = await process_checkin(
        db, cfg, tg_id,
        qr_text=body.get("qr"),
        lat=_num(body.get("lat")),
        lon=_num(body.get("lon")),
        accuracy=_num(body.get("accuracy")),
        source={"gps": "GPS", "tg": "Telegram"}.get(str(body.get("source") or "")),
    )

    if result.new and bot:
        try:
            await bot.send_message(tg_id, "✅ " + result.message)
        except Exception:
            log.exception("Tasdiq xabarini yuborib bo'lmadi")

    return web.json_response({"ok": result.ok, "message": result.message, "checked_at": result.checked_at})


def create_web_app(cfg: Config, db: Database, bot: Bot | None) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app["cfg"], app["db"], app["bot"] = cfg, db, bot
    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/api/checkin", checkin)
    return app
