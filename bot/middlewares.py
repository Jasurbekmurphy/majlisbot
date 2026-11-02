"""Bir odamning so'rovlarini navbat bilan bajarish va takroriy bosishlarni o'tkazib yuborish."""
import asyncio
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

DOUBLE_TAP_SECONDS = 1.0


class UserQueueMiddleware(BaseMiddleware):
    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_tap: dict[tuple[int, str], float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            key = (user.id, event.data or "")
            now = time.monotonic()
            if now - self._last_tap.get(key, 0) < DOUBLE_TAP_SECONDS:
                try:
                    await event.answer()  # aylanib turgan soat belgisini o'chirish
                except Exception:
                    pass
                return None
            self._last_tap[key] = now
            if len(self._last_tap) > 5000:  # xotira o'smasligi uchun
                cutoff = now - 60
                self._last_tap = {k: v for k, v in self._last_tap.items() if v > cutoff}

        lock = self._locks.setdefault(user.id, asyncio.Lock())
        async with lock:
            return await handler(event, data)
