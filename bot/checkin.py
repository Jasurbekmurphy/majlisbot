"""Majlisga belgilanish qoidalari: QR, majlis vaqti oynasi, joylashuv tekshiruvi."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Config
from .db import Database
from .utils import WEEKDAYS_SHORT, haversine_m, parse_hhmm, qr_payload

REASONS_UZ = {
    "not_registered": "Ro'yxatdan o'tmagan",
    "bad_qr": "Noto'g'ri yoki eski QR",
    "no_meeting": "Ochiq majlis yo'q",
    "too_early": "Juda erta",
    "removed": "Admin «kelmadi» degan",
    "no_office": "Ofis sozlanmagan",
    "no_location": "Joylashuv yo'q",
    "low_accuracy": "GPS aniqligi past",
    "too_far": "Ofisdan uzoqda",
}


@dataclass(frozen=True)
class Rules:
    meeting_time: time
    daily_enabled: bool
    remind_before: int
    open_before: int
    close_after: int
    late_after: int
    workdays: frozenset[int]
    radius: float
    office: tuple[float, float] | None

    @classmethod
    def from_settings(cls, s: dict[str, str]) -> "Rules":
        office = None
        if s.get("office_lat") and s.get("office_lon"):
            office = (float(s["office_lat"]), float(s["office_lon"]))
        return cls(
            meeting_time=parse_hhmm(s["meeting_time"]) or time(8, 0),
            daily_enabled=s.get("daily_enabled", "1") == "1",
            remind_before=int(s["remind_before"]),
            open_before=int(s["open_before"]),
            close_after=int(s["close_after"]),
            late_after=int(s["late_after"]),
            workdays=frozenset(int(x) for x in s["workdays"].split(",") if x.strip()),
            radius=float(s["radius"]),
            office=office,
        )

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.workdays

    def window(self, start: datetime) -> tuple[datetime, datetime]:
        """Belgilanish oynasi: eslatma yoki open_before (qaysi biri oldinroq) — majlis + close_after."""
        opens = start - timedelta(minutes=max(self.open_before, self.remind_before))
        return opens, start + timedelta(minutes=self.close_after)


def meeting_start(m) -> datetime:
    return datetime.fromisoformat(m["start_at"])


def meeting_name(m) -> str:
    if m["title"]:
        return m["title"]
    return "Kunlik majlis" if m["kind"] == "daily" else "Majlis"


def meeting_label(m, with_date: bool = True) -> str:
    st = meeting_start(m)
    d = f"{st:%d.%m} ({WEEKDAYS_SHORT[st.weekday()]}) " if with_date else ""
    return f"{d}{st:%H:%M} — {meeting_name(m)}"


async def find_open_meeting(db: Database, rules: Rules, now: datetime):
    """(ochiq_majlis, keyingi_majlis) qaytaradi."""
    today = now.date()
    rows = await db.meetings_between((today - timedelta(days=1)).isoformat(), today.isoformat())
    open_now, upcoming = [], []
    for m in rows:
        opens, closes = rules.window(meeting_start(m))
        if opens <= now <= closes:
            open_now.append(m)
        elif now < opens:
            upcoming.append(m)
    current = min(open_now, key=lambda m: abs((meeting_start(m) - now).total_seconds()), default=None)
    nxt = min(upcoming, key=lambda m: meeting_start(m), default=None)
    return current, nxt


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
    rules = Rules.from_settings(s)
    emp = await db.employee_by_tg(telegram_id)
    meeting = None

    async def fail(code: str, message: str, distance: float | None = None) -> CheckinResult:
        await db.add_attempt(telegram_id, emp["id"] if emp else None, now.isoformat(timespec="seconds"),
                             code, lat, lon, accuracy, distance, meeting["id"] if meeting else None)
        return CheckinResult(False, message, emp)

    if not emp or not emp["active"]:
        return await fail("not_registered", "Siz ro'yxatdan o'tmagansiz. Botga /start yozing.")

    if (qr_text or "").strip() != qr_payload(s["qr_secret"]):
        return await fail("bad_qr", "Bu QR kod eskirgan yoki majlis kodi emas. Xonadagi yangi QR kodni skanerlang.")

    meeting, nxt = await find_open_meeting(db, rules, now)
    if not meeting:
        if nxt:
            opens, _ = rules.window(meeting_start(nxt))
            return await fail("too_early",
                              f"Belgilanish {opens:%H:%M} da ochiladi.\nMajlis: {meeting_label(nxt, with_date=False)}")
        return await fail("no_meeting", "Hozir ochiq majlis yo'q.")

    existing = await db.attendance(meeting["id"], emp["id"])
    if existing:
        if existing["status"] == "removed":
            return await fail("removed", "Admin sizni bu majlisga «kelmadi» deb belgilagan.")
        return CheckinResult(True, f"Siz bu majlisga {existing['checked_at'][11:19]} da belgilangansiz.",
                             emp, existing["checked_at"])

    if rules.office is None:
        return await fail("no_office", "Ofis joylashuvi hali sozlanmagan. Adminga murojaat qiling.")

    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return await fail("no_location", "Joylashuvingiz olinmadi. Telefonda joylashuv (GPS) yoqilganini tekshiring.")

    if accuracy is not None and accuracy > cfg.max_gps_accuracy:
        return await fail("low_accuracy",
                          f"GPS aniqligi past (±{accuracy:.0f} m). Deraza yoniga borib qayta urinib ko'ring.")

    distance = haversine_m(lat, lon, *rules.office)
    if distance > rules.radius:
        return await fail(
            "too_far",
            f"Siz ofisdan {distance:.0f} m uzoqdasiz. Belgilanish uchun majlis xonasida bo'lishingiz kerak.",
            distance,
        )

    start = meeting_start(meeting)
    late = now > start + timedelta(minutes=rules.late_after)
    late_min = max(0, int((now - start).total_seconds() // 60)) if late else 0
    status = "late" if late else "on_time"
    checked_at = now.isoformat(timespec="seconds")

    inserted = await db.add_attendance(meeting["id"], emp["id"], checked_at, lat, lon, accuracy,
                                       round(distance, 1), status, late_min)
    if not inserted:
        existing = await db.attendance(meeting["id"], emp["id"])
        return CheckinResult(True, f"Siz bu majlisga {existing['checked_at'][11:19]} da belgilangansiz.",
                             emp, existing["checked_at"])

    msg = f"Majlisga qo'shildingiz.\n{meeting_label(meeting, with_date=False)}\nVaqt: {now:%H:%M:%S}"
    if late:
        msg += f"\nKech qoldingiz: {late_min} daqiqa"
    return CheckinResult(True, msg, emp, checked_at, new=True)
