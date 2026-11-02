"""Kunlik matnli hisobot va Excel hisobot."""
import io
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .checkin import REASONS_UZ, Schedule
from .config import Config
from .db import Database
from .utils import WEEKDAYS_SHORT, WEEKDAYS_UZ, esc, fmt_phone, split_message

STATUS_UZ = {"on_time": "Vaqtida", "late": "Kech qoldi", "absent": "Kelmadi", "pending": "Hali belgilanmagan"}


def _employed_on(emp, day: date) -> bool:
    return emp["created_at"][:10] <= day.isoformat()


def _absent_status(day: date, now: datetime, sched: Schedule, cfg: Config) -> str:
    if day == now.date():
        _, _, close_dt = sched.window(day, cfg.tz)
        if now < close_dt:
            return "pending"
    return "absent"


async def day_report_text(db: Database, cfg: Config, day: date, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(cfg.tz)
    s = await db.settings()
    sched = Schedule.from_settings(s)
    emps = [e for e in await db.employees() if _employed_on(e, day)]
    att = await db.attendance_for_day(day.isoformat())

    head = f"📋 <b>Majlis davomati — {day:%d.%m.%Y} ({WEEKDAYS_UZ[day.weekday()]})</b>\n"
    head += f"Majlis vaqti: {s['meeting_time']}\n"
    if not sched.is_workday(day):
        head += "\n<i>Bu kun ish kuni sifatida belgilanmagan.</i>\n"

    on_time, late, missing = [], [], []
    for e in emps:
        r = att.get(e["id"])
        if not r:
            missing.append(e)
        elif r["status"] == "late":
            late.append((e, r))
        else:
            on_time.append((e, r))
    on_time.sort(key=lambda x: x[1]["checked_at"])
    late.sort(key=lambda x: x[1]["checked_at"])

    absent_word = "Hali belgilanmaganlar" if _absent_status(day, now, sched, cfg) == "pending" else "Kelmaganlar"
    lines = [head, f"Jami: {len(emps)} | ✅ {len(on_time)} | ⏰ {len(late)} | ❌ {len(missing)}", ""]
    if on_time:
        lines.append(f"✅ <b>Vaqtida ({len(on_time)})</b>")
        lines += [f"{i}. {esc(e['full_name'])} — {r['checked_at'][11:19]}" for i, (e, r) in enumerate(on_time, 1)]
        lines.append("")
    if late:
        lines.append(f"⏰ <b>Kech qolganlar ({len(late)})</b>")
        lines += [f"{i}. {esc(e['full_name'])} — {r['checked_at'][11:19]} (+{r['late_min']} daq)"
                  for i, (e, r) in enumerate(late, 1)]
        lines.append("")
    if missing:
        lines.append(f"❌ <b>{absent_word} ({len(missing)})</b>")
        lines += [f"{i}. {esc(e['full_name'])} — {fmt_phone(e['phone'])}" for i, e in enumerate(missing, 1)]
    return split_message("\n".join(lines))


async def attempts_text(db: Database, day: date) -> list[str]:
    rows = await db.attempts_range(day.isoformat(), day.isoformat())
    if not rows:
        return [f"{day:%d.%m.%Y} kuni rad etilgan urinishlar yo'q."]
    lines = [f"🚫 <b>Rad etilgan urinishlar — {day:%d.%m.%Y}</b>", ""]
    for r in rows:
        who = esc(r["full_name"]) if r["full_name"] else f"tg:{r['telegram_id']}"
        dist = f", {r['distance']:.0f} m" if r["distance"] is not None else ""
        lines.append(f"{r['at'][11:19]} — {who}: {REASONS_UZ.get(r['reason'], r['reason'])}{dist}")
    return split_message("\n".join(lines))


# ------------------------------- Excel -------------------------------
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STATUS_FILL = {
    "on_time": PatternFill("solid", fgColor="E2F0D9"),
    "late": PatternFill("solid", fgColor="FFF2CC"),
    "absent": PatternFill("solid", fgColor="F8D7DA"),
    "pending": PatternFill("solid", fgColor="EDEDED"),
}
THIN = Border(*(Side(style="thin", color="BFBFBF"),) * 4)


def _write_table(ws, headers, rows, widths):
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, THIN
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        ws.append(row)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.border = THIN
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


async def build_excel(db: Database, cfg: Config, day_from: date, day_to: date,
                      now: datetime | None = None) -> bytes:
    now = now or datetime.now(cfg.tz)
    day_to = min(day_to, now.date())
    s = await db.settings()
    sched = Schedule.from_settings(s)
    emps = await db.employees()
    att = await db.attendance_range(day_from.isoformat(), day_to.isoformat())

    days = []
    d = day_from
    while d <= day_to:
        if sched.is_workday(d):
            days.append(d)
        d += timedelta(days=1)

    wb = Workbook()

    # 1-varaq: kunlik
    ws = wb.active
    ws.title = "Kunlik"
    daily_rows, statuses = [], []
    summary = {e["id"]: {"days": 0, "on_time": 0, "late": 0, "absent": 0, "late_min": 0} for e in emps}
    for day in days:
        for e in emps:
            if not _employed_on(e, day):
                continue
            r = att.get((e["id"], day.isoformat()))
            if r:
                status = r["status"]
                row = [day, WEEKDAYS_SHORT[day.weekday()], e["full_name"], fmt_phone(e["phone"]),
                       STATUS_UZ[status], r["checked_at"][11:19], r["late_min"] or None,
                       round(r["distance"]) if r["distance"] is not None else None]
            else:
                status = _absent_status(day, now, sched, cfg)
                row = [day, WEEKDAYS_SHORT[day.weekday()], e["full_name"], fmt_phone(e["phone"]),
                       STATUS_UZ[status], None, None, None]
            daily_rows.append(row)
            statuses.append(status)
            if status != "pending":
                sm = summary[e["id"]]
                sm["days"] += 1
                sm[status] += 1
                if status == "late":
                    sm["late_min"] += r["late_min"]

    _write_table(ws, ["Sana", "Kun", "F.I.Sh.", "Telefon", "Holat", "Kelgan vaqti", "Kechikish (daq)",
                      "Ofisdan masofa (m)"], daily_rows, [12, 6, 30, 16, 18, 13, 15, 18])
    for i, status in enumerate(statuses, start=2):
        ws.cell(row=i, column=1).number_format = "DD.MM.YYYY"
        ws.cell(row=i, column=5).fill = STATUS_FILL[status]

    # 2-varaq: jami
    ws2 = wb.create_sheet("Jami")
    rows2 = []
    for e in emps:
        sm = summary[e["id"]]
        came = sm["on_time"] + sm["late"]
        pct = came / sm["days"] if sm["days"] else None
        rows2.append([e["full_name"], fmt_phone(e["phone"]), sm["days"], sm["on_time"], sm["late"],
                      sm["absent"], sm["late_min"], pct])
    rows2.sort(key=lambda r: (r[7] is None, -(r[7] or 0), r[0]))
    _write_table(ws2, ["F.I.Sh.", "Telefon", "Majlislar soni", "Vaqtida", "Kech qolgan", "Kelmagan",
                       "Jami kechikish (daq)", "Davomat %"], rows2, [30, 16, 14, 10, 12, 10, 18, 11])
    for i in range(2, ws2.max_row + 1):
        ws2.cell(row=i, column=8).number_format = "0%"

    # 3-varaq: rad etilgan urinishlar (shubhali holatlar)
    ws3 = wb.create_sheet("Rad etilganlar")
    rows3 = []
    for r in await db.attempts_range(day_from.isoformat(), day_to.isoformat()):
        rows3.append([r["at"][:10], r["at"][11:19], r["full_name"] or f"tg:{r['telegram_id']}",
                      fmt_phone(r["phone"]) if r["phone"] else "", REASONS_UZ.get(r["reason"], r["reason"]),
                      round(r["distance"]) if r["distance"] is not None else None,
                      round(r["accuracy"]) if r["accuracy"] is not None else None])
    _write_table(ws3, ["Sana", "Vaqt", "F.I.Sh.", "Telefon", "Sabab", "Masofa (m)", "GPS aniqligi (m)"],
                 rows3, [12, 10, 30, 16, 22, 12, 16])

    info = wb.create_sheet("Ma'lumot")
    for row in [
        ["Davr", f"{day_from:%d.%m.%Y} — {day_to:%d.%m.%Y}"],
        ["Majlis vaqti", s["meeting_time"]],
        ["Ish kunlari soni", len(days)],
        ["Hodimlar soni", len(emps)],
        ["Hisobot tuzilgan", now.strftime("%d.%m.%Y %H:%M")],
    ]:
        info.append(row)
    info.column_dimensions["A"].width = 20
    info.column_dimensions["B"].width = 28
    for c in info["A"]:
        c.font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
