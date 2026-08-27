"""
Export the timetable as a Word document in the college's own form; import
events from a CSV or ICS file.

The CSV, ICS and PDF exports are still here and still work -- other things
link to them, and the calendar feed depends on the ICS shape -- but the
timetable page now offers only the Word document, which is the one a
professor actually has to hand in.
"""
import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Event, User
from app.services.nlp_dates import combine, resolve_time

router = APIRouter(prefix="/api/export", tags=["export"])


def _week_events(db: Session, user: User) -> list[Event]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    start = combine(monday, resolve_time("00:00"), user.timezone)
    end = start + timedelta(days=7)
    return (
        db.query(Event)
        .filter(Event.user_id == user.id, Event.is_cancelled.is_(False), Event.start_datetime >= start, Event.start_datetime < end)
        .order_by(Event.start_datetime)
        .all()
    )


@router.get("/csv")
def export_csv(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    events = _week_events(db, user)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Title", "Type", "Subject", "Day", "Start", "End", "Location", "Priority"])
    for e in events:
        writer.writerow(
            [
                e.title,
                e.event_type,
                e.subject or "",
                e.start_datetime.strftime("%A"),
                e.start_datetime.strftime("%Y-%m-%d %H:%M"),
                e.end_datetime.strftime("%Y-%m-%d %H:%M"),
                e.location or "",
                e.priority,
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=timetable.csv"},
    )


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


@router.get("/ics")
def export_ics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    events = _week_events(db, user)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ProfSchedule AI//EN"]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e.id}@profschedule.ai",
            f"DTSTART:{e.start_datetime.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{e.end_datetime.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{_ics_escape(e.title)}",
            f"LOCATION:{_ics_escape(e.location or '')}",
            f"DESCRIPTION:{_ics_escape(e.description or '')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    content = "\r\n".join(lines)
    return StreamingResponse(
        iter([content]),
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=timetable.ics"},
    )


@router.get("/pdf")
def export_pdf(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet

    events = _week_events(db, user)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter))
    styles = getSampleStyleSheet()

    data = [["Day", "Time", "Title", "Type", "Location"]]
    for e in events:
        data.append(
            [
                e.start_datetime.strftime("%A"),
                f"{e.start_datetime.strftime('%I:%M %p')} - {e.end_datetime.strftime('%I:%M %p')}",
                e.title,
                e.event_type,
                e.location or "-",
            ]
        )

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
            ]
        )
    )
    doc.build([Paragraph(f"{user.name} — Weekly Schedule", styles["Title"]), table])
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=timetable.pdf"},
    )


@router.get("/doc")
def export_doc(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The week in the college's own timetable form, as a Word document.

    The layout lives in doc_export because it is a document, not a route: a
    fixed ten-column grid with the two breaks merged down the page, which is
    the form the office issues and expects back.
    """
    from app.services.doc_export import build_timetable_doc

    buf = build_timetable_doc(user, _week_events(db, user))
    safe = "".join(c for c in user.name if c.isalnum() or c in " -_").strip() or "timetable"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe} - Timetable.docx"'},
    )


@router.post("/import/csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    content = (await file.read()).decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    created = 0
    for row in reader:
        try:
            start = datetime.strptime(row["Start"], "%Y-%m-%d %H:%M").replace(tzinfo=None)
            end = datetime.strptime(row["End"], "%Y-%m-%d %H:%M").replace(tzinfo=None)
        except (KeyError, ValueError):
            continue
        event = Event(
            user_id=user.id,
            title=row.get("Title", "Imported Event"),
            event_type=row.get("Type", "other"),
            subject=row.get("Subject") or None,
            start_datetime=start,
            end_datetime=end,
            location=row.get("Location") or None,
            priority=row.get("Priority", "medium"),
        )
        db.add(event)
        created += 1
    db.commit()
    return {"imported": created}
