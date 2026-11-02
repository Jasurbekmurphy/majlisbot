import secrets

import aiosqlite

DEFAULT_SETTINGS = {
    "meeting_time": "09:00",   # majlis boshlanish vaqti
    "open_before": "30",       # majlisdan necha daqiqa oldin belgilanish ochiladi
    "close_after": "60",       # majlis boshlangandan necha daqiqa keyin yopiladi
    "late_after": "5",         # necha daqiqadan keyin "kech qoldi" hisoblanadi
    "radius": "150",           # ofisdan ruxsat etilgan masofa (metr)
    "workdays": "0,1,2,3,4",   # 0=Dushanba ... 6=Yakshanba
    "office_lat": "",
    "office_lon": "",
    "qr_secret": "",
    "last_report_day": "",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    telegram_id INTEGER UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    day         TEXT NOT NULL,
    checked_at  TEXT NOT NULL,
    lat         REAL,
    lon         REAL,
    accuracy    REAL,
    distance    REAL,
    status      TEXT NOT NULL,
    late_min    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (employee_id, day)
);

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

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_att_day ON attendance(day);
CREATE INDEX IF NOT EXISTS idx_attempts_at ON attempts(at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            if key == "qr_secret":
                value = secrets.token_urlsafe(12)
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ---------- sozlamalar ----------
    async def settings(self) -> dict[str, str]:
        cur = await self.conn.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    async def set_setting(self, key: str, value: str):
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        await self.conn.commit()

    # ---------- hodimlar ----------
    async def add_employee(self, phone: str, full_name: str, now_iso: str) -> str:
        cur = await self.conn.execute("SELECT id FROM employees WHERE phone = ?", (phone,))
        row = await cur.fetchone()
        if row:
            await self.conn.execute(
                "UPDATE employees SET full_name = ?, active = 1 WHERE id = ?", (full_name, row["id"])
            )
            result = "updated"
        else:
            await self.conn.execute(
                "INSERT INTO employees(phone, full_name, created_at) VALUES (?, ?, ?)",
                (phone, full_name, now_iso),
            )
            result = "added"
        await self.conn.commit()
        return result

    async def deactivate_employee(self, phone: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE employees SET active = 0, telegram_id = NULL WHERE phone = ? AND active = 1", (phone,)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def unlink_telegram(self, phone: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE employees SET telegram_id = NULL WHERE phone = ? AND telegram_id IS NOT NULL", (phone,)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def employees(self, active_only: bool = True):
        q = "SELECT * FROM employees"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY full_name COLLATE NOCASE"
        cur = await self.conn.execute(q)
        return await cur.fetchall()

    async def employee_by_tg(self, telegram_id: int):
        cur = await self.conn.execute("SELECT * FROM employees WHERE telegram_id = ?", (telegram_id,))
        return await cur.fetchone()

    async def employee_by_phone(self, phone: str):
        cur = await self.conn.execute("SELECT * FROM employees WHERE phone = ?", (phone,))
        return await cur.fetchone()

    async def link_telegram(self, employee_id: int, telegram_id: int):
        await self.conn.execute(
            "UPDATE employees SET telegram_id = ? WHERE id = ?", (telegram_id, employee_id)
        )
        await self.conn.commit()

    # ---------- davomat ----------
    async def attendance(self, employee_id: int, day: str):
        cur = await self.conn.execute(
            "SELECT * FROM attendance WHERE employee_id = ? AND day = ?", (employee_id, day)
        )
        return await cur.fetchone()

    async def add_attendance(self, employee_id, day, checked_at, lat, lon, accuracy, distance,
                             status, late_min) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO attendance"
            "(employee_id, day, checked_at, lat, lon, accuracy, distance, status, late_min) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (employee_id, day, checked_at, lat, lon, accuracy, distance, status, late_min),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def attendance_for_day(self, day: str) -> dict[int, aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM attendance WHERE day = ?", (day,))
        return {r["employee_id"]: r for r in await cur.fetchall()}

    async def attendance_range(self, day_from: str, day_to: str) -> dict[tuple[int, str], aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM attendance WHERE day BETWEEN ? AND ?", (day_from, day_to)
        )
        return {(r["employee_id"], r["day"]): r for r in await cur.fetchall()}

    async def history(self, employee_id: int, limit: int = 10):
        cur = await self.conn.execute(
            "SELECT * FROM attendance WHERE employee_id = ? ORDER BY day DESC LIMIT ?",
            (employee_id, limit),
        )
        return await cur.fetchall()

    # ---------- rad etilgan urinishlar ----------
    async def add_attempt(self, telegram_id, employee_id, at, reason, lat=None, lon=None,
                          accuracy=None, distance=None):
        await self.conn.execute(
            "INSERT INTO attempts(telegram_id, employee_id, at, reason, lat, lon, accuracy, distance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, employee_id, at, reason, lat, lon, accuracy, distance),
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
