"""Majlisga belgilanish qoidalari: QR, vaqt oynasi, joylashuv tekshiruvi."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .db import Database
from .utils import haversine_m, parse_hhmm, qr_payload

REASONS_UZ = {
    "not_registered": "Ro'yxatdan o'tmagan",
    "bad_qr": "Noto'g'ri QR",
    "not_workday": "Ish kuni emas",
    "too_early": "Juda erta",
    "too_late": "Vaqt tugagan",
    "no_office": "Ofis sozlanmagan",
    "no_location": "Joylashuv yo'q",
    "low_accuracy": "GPS aniqligi past",
    "too_far": "Ofisdan uzoqda",
}


@dataclass(frozen=True)
class Schedule:
    meeting_time: time
    open_before: int
    close_after: int
    late_after: int
    workdays: frozenset[int]
    radius: float
    office: tuple[float, float] | None

    @classmethod
    def from_settings(cls, s: dict[str, str]) -> "Schedule":
        office = None
        if s.get("office_lat") and s.get("office_lon"):
            office = (float(s["office_lat"]), float(s["office_lon"]))
        return cls(
            meeting_time=parse_hhmm(s["meeting_time"]) or time(9, 0),
            open_before=int(s["open_before"]),
            close_after=int(s["close_after"]),
            late_after=int(s["late_after"]),
            workdays=frozenset(int(x) for x in s["workdays"].split(",") if x.strip()),
            radius=float(s["radius"]),
            office=office,
        )

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.workdays

    def window(self, d: date, tz: ZoneInfo) -> tuple[datetime, datetime, datetime]:
        start = datetime.combine(d, self.meeting_time, tzinfo=tz)
        return (
            start - timedelta(minutes=self.open_before),
            start,
            start + timedelta(minutes=self.close_after),
        )


@dataclass
class CheckinResult:
    ok: bool
    message: str
    employee: object = None
    checked_at: str | None = None
    new: bool = False


async def process_checkin(db: Database, cfg: Config, telegram_id: int, qr_text: str | None,
                          lat: float | None, lon: float | None, accuracy: float | None,
                          now: datetime | None = None) -> CheckinResult:
    now = now or datetime.now(cfg.tz)
    s = await db.settings()
    sched = Schedule.from_settings(s)
    emp = await db.employee_by_tg(telegram_id)

    async def fail(code: str, message: str, distance: float | None = None) -> CheckinResult:
        await db.add_attempt(telegram_id, emp["id"] if emp else None, now.isoformat(timespec="seconds"),
                             code, lat, lon, accuracy, distance)
        return CheckinResult(False, message, emp)

    if not emp or not emp["active"]:
        return await fail("not_registered", "Siz ro'yxatdan o'tmagansiz. Botga /start yozing.")

    if (qr_text or "").strip() != qr_payload(s["qr_secret"]):
        return await fail("bad_qr", "Bu majlis QR kodi emas yoki eskirgan. Xonadagi QR kodni skanerlang.")

    today = now.date()
    if not sched.is_workday(today):
        return await fail("not_workday", "Bugun majlis yo'q.")

    open_dt, start_dt, close_dt = sched.window(today, cfg.tz)
    if now < open_dt:
        return await fail("too_early", f"Belgilanish {open_dt:%H:%M} da ochiladi.")
    if now > close_dt:
        return await fail("too_late", f"Belgilanish vaqti {close_dt:%H:%M} da tugagan.")

    existing = await db.attendance(emp["id"], today.isoformat())
    if existing:
        t = existing["checked_at"][11:19]
        return CheckinResult(True, f"Siz bugun {t} da belgilangansiz.", emp, existing["checked_at"])

    if sched.office is None:
        return await fail("no_office", "Ofis joylashuvi hali sozlanmagan. Adminga murojaat qiling.")

    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return await fail("no_location", "Joylashuvingiz olinmadi. Telefonda GPS yoqilganini tekshiring.")

    if accuracy is not None and accuracy > cfg.max_gps_accuracy:
        return await fail(
            "low_accuracy",
            f"GPS aniqligi past (±{accuracy:.0f} m). Deraza yoniga borib qayta urinib ko'ring.",
        )

    distance = haversine_m(lat, lon, *sched.office)
    if distance > sched.radius:
        return await fail(
            "too_far",
            f"Siz ofisdan {distance:.0f} m uzoqdasiz. Belgilanish uchun majlis xonasida bo'lishingiz kerak.",
            distance,
        )

    late_min = max(0, int((now - start_dt).total_seconds() // 60))
    status = "late" if now > start_dt + timedelta(minutes=sched.late_after) else "on_time"
    if status == "on_time":
        late_min = 0
    checked_at = now.isoformat(timespec="seconds")

    inserted = await db.add_attendance(emp["id"], today.isoformat(), checked_at, lat, lon,
                                       accuracy, round(distance, 1), status, late_min)
    if not inserted:  # bir vaqtda ikki marta bosilgan bo'lsa
        existing = await db.attendance(emp["id"], today.isoformat())
        t = existing["checked_at"][11:19]
        return CheckinResult(True, f"Siz bugun {t} da belgilangansiz.", emp, existing["checked_at"])

    msg = f"Majlisga qo'shildingiz.\nVaqt: {now:%H:%M:%S}"
    if status == "late":
        msg += f"\nKech qoldingiz: {late_min} daqiqa"
    return CheckinResult(True, msg, emp, checked_at, new=True)
