"""Admin rollari.

Bosh admin — .env dagi ADMIN_IDS: hamma narsa, shu jumladan sozlamalar, QR'ni yangilash va admin tayinlash.
Yordamchi admin — bosh admin hodimlar ichidan tayinlaydi: sozlamalar va QR'ni yangilashdan boshqa hamma narsa.
"""
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from .config import Config
from .db import Database


def is_super(cfg: Config, user_id: int) -> bool:
    return user_id in cfg.admin_ids


async def is_admin(db: Database, cfg: Config, user_id: int) -> bool:
    return is_super(cfg, user_id) or await db.is_helper_admin(user_id)


async def all_admin_ids(db: Database, cfg: Config) -> list[int]:
    ids = list(cfg.admin_ids)
    for e in await db.helper_admins():
        if e["telegram_id"] not in ids:
            ids.append(e["telegram_id"])
    return ids


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, db: Database, cfg: Config) -> bool:
        return bool(event.from_user) and await is_admin(db, cfg, event.from_user.id)


class IsSuper(Filter):
    async def __call__(self, event: Message | CallbackQuery, cfg: Config) -> bool:
        return bool(event.from_user) and is_super(cfg, event.from_user.id)
