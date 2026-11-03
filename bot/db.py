import secrets
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import aiosqlite

DEFAULT_SETTINGS = {
    "meeting_time": "08:00",   # kunlik majlis vaqti
    "daily_enabled": "1",      # kunlik majlis yoqilganmi
    "remind_before": "15",     # majlisdan necha daqiqa oldin eslatma (ilova tugmasi) yuboriladi
    "open_before": "30",       # majlisdan necha daqiqa oldin belgilanish ochiladi
    "close_after": "60",       # majlis boshlangandan necha daqiqa keyin yopiladi
    "late_after": "5",         # necha daqiqadan keyin "kech qoldi"
    "radius": "150",           # ofisdan ruxsat etilgan masofa (metr)
    "workdays": "0,1,2,3,4",   # 0=Dushanba ... 6=Yakshanba
    "office_lat": "",
    "office_lon": "",
    "qr_secret": "",
    "qr_rotated_at": "",       # QR oxirgi marta yangilangan vaqt
    "qr_rotate_days": "2",     # QR necha kunda yangilanadi
    "qr_rotate_time": "16:00", # QR soat nechada yangilanadi
}

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    telegram_id INTEGER UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    is_admin    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meetings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- 'daily' yoki 'extra'
    title       TEXT,
    day         TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  INTEGER,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    announced   INTEGER NOT NULL DEFAULT 0,
    reminded    INTEGER NOT NULL DEFAULT 0,
    report_sent INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_meeting ON meetings(day) WHERE kind = 'daily';
CREATE INDEX IF NOT EXISTS idx_meetings_day ON meetings(day);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    employee_id INTEGER,
    at          TEXT NOT NULL,
    reason      TEXT NOT NULL,
    lat         REAL,
    lon         REAL,
    accuracy    REAL,
    distance    REAL
);
CREATE INDEX IF NOT EXISTS idx_attempts_at ON attempts(at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ATTENDANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES meetings(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    checked_at  TEXT NOT NULL,
    lat         REAL,
    lon         REAL,
    accuracy    REAL,
    distance    REAL,
    status      TEXT NOT NULL,   -- on_time | late | manual (admin "keldi") | removed (admin "kelmadi")
    late_min    INTEGER NOT NULL DEFAULT 0,
    marked_by   INTEGER,
    UNIQUE (meeting_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_att_meeting ON attendance(meeting_id);
"""

MEETING_FLAGS = {"announced", "reminded", "report_sent", "cancelled"}


class Database:
    def __init__(self, path: str, tz: ZoneInfo):
        self.path = path
        self.tz = tz
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(BASE_SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            await self.conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))

        att_cols = await self._columns("attendance")
        if att_cols and "meeting_id" not in att_cols:
            await self._migrate_v1()
        await self.conn.executescript(ATTENDANCE_SCHEMA)
        if "is_admin" not in await self._columns("employees"):
            await self.conn.execute("ALTER TABLE employees ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "source" not in await self._columns("attempts"):
            await self.conn.execute("ALTER TABLE attempts ADD COLUMN source TEXT")
        if "meeting_id" not in await self._columns("attempts"):
            await self.conn.execute("ALTER TABLE attempts ADD COLUMN meeting_id INTEGER")

        s = await self.settings()
        if s.get("qr_rotate_time") == "20:00" and not s.get("migr_qr_1600"):  # eski standart → 16:00 (bir marta)
            await self.set_setting("qr_rotate_time", "16:00", commit=False)
            await self.set_setting("migr_qr_1600", "1", commit=False)
            s = await self.settings()
        now = datetime.now(self.tz).isoformat(timespec="seconds")
        if not s.get("qr_secret"):
            await self.set_setting("qr_secret", secrets.token_urlsafe(12), commit=False)
        if not s.get("qr_rotated_at"):
            await self.set_setting("qr_rotated_at", now, commit=False)
        await self.conn.commit()

    async def _columns(self, table: str) -> list[str]:
        cur = await self.conn.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in await cur.fetchall()]

    async def _migrate_v1(self):
        """1-versiya (kunlik davomat) bazasini majlislar modeliga ko'chiradi."""
        s = await self.settings()
        mt = time.fromisoformat(s.get("meeting_time") or "09:00")
        now = datetime.now(self.tz).isoformat(timespec="seconds")
        await self.conn.execute("ALTER TABLE attendance RENAME TO attendance_v1")
        await self.conn.executescript(ATTENDANCE_SCHEMA)
        cur = await self.conn.execute("SELECT DISTINCT day FROM attendance_v1")
        for (day,) in await cur.fetchall():
            start = datetime.combine(date.fromisoformat(day), mt, tzinfo=self.tz).isoformat(timespec="seconds")
            c = await self.conn.execute(
                "INSERT INTO meetings(kind, title, day, start_at, created_at, announced, reminded, report_sent) "
                "VALUES ('daily', NULL, ?, ?, ?, 1, 1, 1)", (day, start, now))
            await self.conn.execute(
                "INSERT INTO attendance(meeting_id, employee_id, checked_at, lat, lon, accuracy, distance, status, late_min) "
                "SELECT ?, employee_id, checked_at, lat, lon, accuracy, distance, status, late_min "
                "FROM attendance_v1 WHERE day = ?", (c.lastrowid, day))
        await self.conn.execute("DROP TABLE attendance_v1")
        await self.conn.execute("DELETE FROM settings WHERE key = 'last_report_day'")
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ---------- sozlamalar ----------
    async def settings(self) -> dict[str, str]:
        cur = await self.conn.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    async def set_setting(self, key: str, value, commit: bool = True):
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        if commit:
            await self.conn.commit()

    # ---------- hodimlar ----------
    async def add_employee(self, phone: str, full_name: str, now_iso: str) -> str:
        cur = await self.conn.execute("SELECT id FROM employees WHERE phone = ?", (phone,))
        row = await cur.fetchone()
        if row:
            await self.conn.execute(
                "UPDATE employees SET full_name = ?, active = 1 WHERE id = ?", (full_name, row["id"]))
            result = "updated"
        else:
            await self.conn.execute(
                "INSERT INTO employees(phone, full_name, created_at) VALUES (?, ?, ?)", (phone, full_name, now_iso))
            result = "added"
        await self.conn.commit()
        return result

    async def deactivate_employee_by_id(self, employee_id: int):
        cur = await self.conn.execute("SELECT * FROM employees WHERE id = ? AND active = 1", (employee_id,))
        row = await cur.fetchone()
        if not row:
            return None
        await self.conn.execute("UPDATE employees SET active = 0, telegram_id = NULL, is_admin = 0 WHERE id = ?",
                                (employee_id,))
        await self.conn.commit()
        return row

    async def unlink_telegram_by_id(self, employee_id: int):
        cur = await self.conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,))
        row = await cur.fetchone()
        if not row or not row["telegram_id"]:
            return None
        await self.conn.execute("UPDATE employees SET telegram_id = NULL, is_admin = 0 WHERE id = ?", (employee_id,))
        await self.conn.commit()
        return row

    async def employees(self, active_only: bool = True):
        q = "SELECT * FROM employees"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY full_name COLLATE NOCASE"
        cur = await self.conn.execute(q)
        return await cur.fetchall()

    async def linked_employees(self):
        cur = await self.conn.execute(
            "SELECT * FROM employees WHERE active = 1 AND telegram_id IS NOT NULL ORDER BY full_name COLLATE NOCASE")
        return await cur.fetchall()

    async def employee(self, employee_id: int):
        cur = await self.conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,))
        return await cur.fetchone()

    async def employee_by_tg(self, telegram_id: int):
        cur = await self.conn.execute("SELECT * FROM employees WHERE telegram_id = ?", (telegram_id,))
        return await cur.fetchone()

    async def employee_by_phone(self, phone: str):
        cur = await self.conn.execute("SELECT * FROM employees WHERE phone = ?", (phone,))
        return await cur.fetchone()

    async def set_admin(self, employee_id: int, value: bool):
        await self.conn.execute("UPDATE employees SET is_admin = ? WHERE id = ?", (1 if value else 0, employee_id))
        await self.conn.commit()

    async def helper_admins(self):
        cur = await self.conn.execute(
            "SELECT * FROM employees WHERE is_admin = 1 AND active = 1 AND telegram_id IS NOT NULL "
            "ORDER BY full_name COLLATE NOCASE")
        return await cur.fetchall()

    async def is_helper_admin(self, telegram_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM employees WHERE telegram_id = ? AND is_admin = 1 AND active = 1", (telegram_id,))
        return await cur.fetchone() is not None

    async def link_telegram(self, employee_id: int, telegram_id: int):
        await self.conn.execute("UPDATE employees SET telegram_id = ? WHERE id = ?", (telegram_id, employee_id))
        await self.conn.commit()

    # ---------- majlislar ----------
    async def create_meeting(self, kind: str, title: str | None, start: datetime, created_by: int | None,
                             now: datetime) -> int:
        cur = await self.conn.execute(
            "INSERT INTO meetings(kind, title, day, start_at, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, title, start.date().isoformat(), start.isoformat(timespec="seconds"),
             now.isoformat(timespec="seconds"), created_by),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def meeting(self, meeting_id: int):
        cur = await self.conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        return await cur.fetchone()

    async def daily_meeting(self, day: str):
        cur = await self.conn.execute("SELECT * FROM meetings WHERE kind = 'daily' AND day = ?", (day,))
        return await cur.fetchone()

    async def meetings_between(self, day_from: str, day_to: str, include_cancelled: bool = False):
        q = "SELECT * FROM meetings WHERE day BETWEEN ? AND ?"
        if not include_cancelled:
            q += " AND cancelled = 0"
        cur = await self.conn.execute(q + " ORDER BY start_at", (day_from, day_to))
        return await cur.fetchall()

    async def unfinished_meetings(self, since_day: str):
        cur = await self.conn.execute(
            "SELECT * FROM meetings WHERE cancelled = 0 AND day >= ? "
            "AND (announced = 0 OR reminded = 0 OR report_sent = 0) ORDER BY start_at", (since_day,))
        return await cur.fetchall()

    async def set_meeting_flag(self, meeting_id: int, flag: str, value: int = 1):
        assert flag in MEETING_FLAGS
        await self.conn.execute(f"UPDATE meetings SET {flag} = ? WHERE id = ?", (value, meeting_id))
        await self.conn.commit()

    async def update_meeting_start(self, meeting_id: int, start: datetime):
        await self.conn.execute("UPDATE meetings SET start_at = ?, day = ? WHERE id = ?",
                                (start.isoformat(timespec="seconds"), start.date().isoformat(), meeting_id))
        await self.conn.commit()

    # ---------- davomat ----------
    async def attendance(self, meeting_id: int, employee_id: int):
        cur = await self.conn.execute(
            "SELECT * FROM attendance WHERE meeting_id = ? AND employee_id = ?", (meeting_id, employee_id))
        return await cur.fetchone()

    async def add_attendance(self, meeting_id, employee_id, checked_at, lat, lon, accuracy, distance,
                             status, late_min) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO attendance"
            "(meeting_id, employee_id, checked_at, lat, lon, accuracy, distance, status, late_min) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (meeting_id, employee_id, checked_at, lat, lon, accuracy, distance, status, late_min),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_attendance_status(self, meeting_id: int, employee_id: int, status: str, marked_by: int,
                                    now_iso: str):
        """Admin qo'lda belgilashi: 'manual' (keldi) yoki 'removed' (kelmadi)."""
        row = await self.attendance(meeting_id, employee_id)
        if row:
            await self.conn.execute(
                "UPDATE attendance SET status = ?, marked_by = ?, late_min = CASE WHEN ? = 'removed' THEN 0 ELSE late_min END "
                "WHERE id = ?", (status, marked_by, status, row["id"]))
        else:
            await self.conn.execute(
                "INSERT INTO attendance(meeting_id, employee_id, checked_at, status, late_min, marked_by) "
                "VALUES (?, ?, ?, ?, 0, ?)", (meeting_id, employee_id, now_iso, status, marked_by))
        await self.conn.commit()

    async def attendance_for_meeting(self, meeting_id: int) -> dict[int, aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM attendance WHERE meeting_id = ?", (meeting_id,))
        return {r["employee_id"]: r for r in await cur.fetchall()}

    async def attendance_for_meetings(self, meeting_ids: list[int]) -> dict[tuple[int, int], aiosqlite.Row]:
        if not meeting_ids:
            return {}
        marks = ",".join("?" * len(meeting_ids))
        cur = await self.conn.execute(f"SELECT * FROM attendance WHERE meeting_id IN ({marks})", meeting_ids)
        return {(r["meeting_id"], r["employee_id"]): r for r in await cur.fetchall()}

    async def history(self, employee_id: int, limit: int = 10):
        cur = await self.conn.execute(
            "SELECT a.*, m.start_at, m.title, m.kind FROM attendance a JOIN meetings m ON m.id = a.meeting_id "
            "WHERE a.employee_id = ? ORDER BY m.start_at DESC LIMIT ?", (employee_id, limit))
        return await cur.fetchall()

    # ---------- rad etilgan urinishlar ----------
    async def add_attempt(self, telegram_id, employee_id, at, reason, lat=None, lon=None,
                          accuracy=None, distance=None, meeting_id=None, source=None):
        await self.conn.execute(
            "INSERT INTO attempts(telegram_id, employee_id, at, reason, lat, lon, accuracy, distance, meeting_id, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, employee_id, at, reason, lat, lon, accuracy, distance, meeting_id, source),
        )
        await self.conn.commit()

    async def attempts_range(self, day_from: str, day_to: str):
        cur = await self.conn.execute(
            "SELECT a.*, e.full_name, e.phone FROM attempts a "
            "LEFT JOIN employees e ON e.id = a.employee_id "
            "WHERE substr(a.at, 1, 10) BETWEEN ? AND ? ORDER BY a.at",
            (day_from, day_to),
        )
        return await cur.fetchall()
