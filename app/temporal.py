from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo


def aware_iso(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone, e.g. 2026-09-18T14:00:00+05:30")
    return parsed.astimezone(timezone.utc).isoformat()


def window(question: str, tz_name: str, start: date | None = None,
           end: date | None = None, now: datetime | None = None):
    tz = ZoneInfo(tz_name)
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    basis = "explicit" if start or end else None
    if basis is None:
        q = question.lower()
        if re.search(r"\blast month\b", q):
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
            basis = "last_month"
        elif re.search(r"\byesterday\b", q):
            start, end = today - timedelta(days=1), today
            basis = "yesterday"
        elif re.search(r"\btoday\b", q):
            start, end = today, today + timedelta(days=1)
            basis = "today"
    if start and end and start >= end:
        raise ValueError("start_date must be before end_date (exclusive)")

    def utc(d):
        return datetime.combine(d, time.min, tzinfo=tz).astimezone(timezone.utc).isoformat() if d else None

    return {"start": utc(start), "end_exclusive": utc(end), "timezone": tz_name, "basis": basis}
