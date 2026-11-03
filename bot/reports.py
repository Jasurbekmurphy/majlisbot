"""Majlis bo'yicha matnli hisobot va Excel hisobot."""
import io
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .checkin import REASONS_UZ, Rules, meeting_label, meeting_name, meeting_start
from .config import Config
from .db import Database
from .utils import WEEKDAYS_SHORT, esc, fmt_phone, split_message

STATUS_UZ = {
    "on_time": "Vaqtida",
    "late": "Kech qoldi",
    "manual": "Keldi (admin)",
    "removed": "Kelmadi (admin)",
    "absent": "Kelmadi",
    "pending": "Hali belgilanmagan",
}
PRESENT = {"on_time", "late", "manual"}


def employed_for(emp, close_dt: datetime) -> bool:
    """Majlis yopilishidan oldin ro'yxatga olingan hodimlargina hisobga olinadi."""
    return emp["created_at"][:19] <= close_dt.isoformat()[:19]


def meeting_status(row, now: datetime, close_dt: datetime) -> str:
    if row:
        return row["status"]
    return "pending" if now < close_dt else "absent"


async def meeting_report_text(db: Database, cfg: Config, m, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(cfg.tz)
    rules = Rules.from_settings(await db.settings())
    _, close_dt = rules.window(meeting_start(m))
    emps = [e for e in await db.employees() if employed_for(e, close_dt)]
    att = await db.attendance_for_meeting(m["id"])

    groups = {"on_time": [], "late": [], "missing": []}
    for e in emps:
        r = att.get(e["id"])
        st = meeting_status(r, now, close_dt)
        if st in ("on_time", "manual"):
            groups["on_time"].append((e, r))
        elif st == "late":
            groups["late"].append((e, r))
        else:
            groups["missing"].append((e, r))
    for k in ("on_time", "late"):
        groups[k].sort(key=lambda x: x[1]["checked_at"])

    title = f"📋 <b>{esc(meeting_label(m))}</b>"
    if m["cancelled"]:
        title += "\n<i>Bu majlis bekor qilingan.</i>"
    missing_word = "Hali belgilanmaganlar" if now < close_dt else "Kelmaganlar"
    lines = [title, "",
             f"Jami: {len(emps)} | ✅ {len(groups['on_time'])} | ⏰ {len(groups['late'])} | ❌ {len(groups['missing'])}", ""]
    if groups["on_time"]:
        lines.append(f"✅ <b>Vaqtida ({len(groups['on_time'])})</b>")
        for i, (e, r) in enumerate(groups["on_time"], 1):
            t = "admin belgiladi" if r["status"] == "manual" else r["checked_at"][11:19]
            lines.append(f"{i}. {esc(e['full_name'])} — {t}")
        lines.append("")
    if groups["late"]:
        lines.append(f"⏰ <b>Kech qolganlar ({len(groups['late'])})</b>")
        lines += [f"{i}. {esc(e['full_name'])} — {r['checked_at'][11:19]} (+{r['late_min']} daq)"
                  for i, (e, r) in enumerate(groups["late"], 1)]
        lines.append("")
    if groups["missing"]:
        lines.append(f"❌ <b>{missing_word} ({len(groups['missing'])})</b>")
        for i, (e, r) in enumerate(groups["missing"], 1):
            note = " — <i>admin «kelmadi» dedi</i>" if r and r["status"] == "removed" else ""
            lines.append(f"{i}. {esc(e['full_name'])}{note}")
    return split_message("\n".join(lines))


# ------------------------------- Excel -------------------------------
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STATUS_FILL = {
    "on_time": PatternFill("solid", fgColor="E2F0D9"),
    "manual": PatternFill("solid", fgColor="E2F0D9"),
    "late": PatternFill("solid", fgColor="FFF2CC"),
    "absent": PatternFill("solid", fgColor="F8D7DA"),
    "removed": PatternFill("solid", fgColor="F4B6BC"),
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
    rules = Rules.from_settings(await db.settings())
    emps = await db.employees()
    meetings = [m for m in await db.meetings_between(day_from.isoformat(), day_to.isoformat())
                if rules.window(meeting_start(m))[0] <= now]  # boshlanmagan majlislar kirmaydi
    att = await db.attendance_for_meetings([m["id"] for m in meetings])

    wb = Workbook()
    ws = wb.active
    ws.title = "Majlislar bo'yicha"
    rows, statuses = [], []
    summary = {e["id"]: {"total": 0, "on_time": 0, "late": 0, "absent": 0, "late_min": 0} for e in emps}
    for m in meetings:
        start = meeting_start(m)
        _, close_dt = rules.window(start)
        for e in emps:
            if not employed_for(e, close_dt):
                continue
            r = att.get((m["id"], e["id"]))
            st = meeting_status(r, now, close_dt)
            present = st in PRESENT
            rows.append([
                start.date(), WEEKDAYS_SHORT[start.weekday()], start.strftime("%H:%M"), meeting_name(m),
                e["full_name"], fmt_phone(e["phone"]), STATUS_UZ[st],
                r["checked_at"][11:19] if (r and present and st != "manual") else None,
                (r["late_min"] or None) if r and st == "late" else None,
                round(r["distance"]) if r and present and r["distance"] is not None else None,
            ])
            statuses.append(st)
            if st == "pending":
                continue
            sm = summary[e["id"]]
            sm["total"] += 1
            if st == "late":
                sm["late"] += 1
                sm["late_min"] += r["late_min"] or 0
            elif present:
                sm["on_time"] += 1
            else:
                sm["absent"] += 1

    _write_table(ws, ["Sana", "Kun", "Majlis vaqti", "Majlis", "F.I.Sh.", "Telefon", "Holat", "Kelgan vaqti",
                      "Kechikish (daq)", "Ofisdan masofa (m)"],
                 rows, [12, 6, 11, 22, 30, 16, 18, 13, 15, 18])
    for i, st in enumerate(statuses, start=2):
        ws.cell(row=i, column=1).number_format = "DD.MM.YYYY"
        ws.cell(row=i, column=7).fill = STATUS_FILL[st]

    ws2 = wb.create_sheet("Jami")
    rows2 = []
    for e in emps:
        sm = summary[e["id"]]
        came = sm["on_time"] + sm["late"]
        rows2.append([e["full_name"], fmt_phone(e["phone"]), sm["total"], sm["on_time"], sm["late"],
                      sm["absent"], sm["late_min"], came / sm["total"] if sm["total"] else None])
    rows2.sort(key=lambda r: (r[7] is None, -(r[7] or 0), r[0]))
    _write_table(ws2, ["F.I.Sh.", "Telefon", "Majlislar soni", "Vaqtida", "Kech qolgan", "Kelmagan",
                       "Jami kechikish (daq)", "Davomat %"], rows2, [30, 16, 14, 10, 12, 10, 18, 11])
    for i in range(2, ws2.max_row + 1):
        ws2.cell(row=i, column=8).number_format = "0%"

    ws3 = wb.create_sheet("Rad etilganlar")
    rows3 = []
    for r in await db.attempts_range(day_from.isoformat(), day_to.isoformat()):
        rows3.append([r["at"][:10], r["at"][11:19], r["full_name"] or f"tg:{r['telegram_id']}",
                      fmt_phone(r["phone"]) if r["phone"] else "", REASONS_UZ.get(r["reason"], r["reason"]),
                      round(r["distance"]) if r["distance"] is not None else None,
                      round(r["accuracy"]) if r["accuracy"] is not None else None,
                      r["source"] or ""])
    _write_table(ws3, ["Sana", "Vaqt", "F.I.Sh.", "Telefon", "Sabab", "Masofa (m)", "GPS aniqligi (m)",
                       "Joylashuv manbasi"], rows3, [12, 10, 30, 16, 24, 12, 16, 18])

    info = wb.create_sheet("Ma'lumot")
    for row in [
        ["Davr", f"{day_from:%d.%m.%Y} — {day_to:%d.%m.%Y}"],
        ["Majlislar soni", len(meetings)],
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
