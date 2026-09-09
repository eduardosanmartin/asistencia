"""Hours & lateness computation (task 5.1).

compute_hours(user_id, start_date, end_date) walks the worker's accepted
AttendanceEvents in chronological order and greedily pairs each entry with
the following exit. Invariant: only accepted marks (OK / OK_extra) are
paired; INVALIDO_* attempts never produce work time.

Report shape:
    pairs    [{entry, exit, minutes, delay_minutes, is_extra, is_open,
               late}]
    flags    {orphan_exits, double_exits, consecutive_entries,
              late_entries}
    totals   {minutes, extra_minutes, days_worked}
    subtotals {daily, weekly, monthly}  (org-timezone calendar buckets)

An unmatched entry at the range end stays open with 0 minutes until a
closing exit arrives; an unmatched exit is an orphan, and two orphan
exits in a row flip the double-exit flag.
"""

from datetime import datetime, timedelta, timezone

from app.models import AttendanceEvent, User

from .attendance import _grace_minutes, _org_timezone

ACCEPTED_OUTCOMES = ("OK", "OK_extra")


def _start_of_day(day, tz):
    """Aware datetime at local midnight for the given date."""
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _local_day(ts, tz):
    return ts.astimezone(tz).date()


def compute_hours(user_id, start_date, end_date):
    """Work-hours report for one worker over an inclusive date range."""
    tz = _org_timezone()
    grace = _grace_minutes()
    start_dt = _start_of_day(start_date, tz).astimezone(timezone.utc)
    end_dt = (_start_of_day(end_date, tz) + timedelta(days=1)).astimezone(
        timezone.utc
    )

    events = list(
        AttendanceEvent.select()
        .where(
            AttendanceEvent.user_id == user_id,
            AttendanceEvent.outcome.in_(ACCEPTED_OUTCOMES),
            AttendanceEvent.timestamp >= start_dt,
            AttendanceEvent.timestamp < end_dt,
        )
        .order_by(AttendanceEvent.timestamp.asc())
    )

    pairs = []
    flags = {
        "orphan_exits": [],
        "double_exits": [],
        "consecutive_entries": [],
        "late_entries": [],
    }
    open_pair = None
    prev_type = None

    for event in events:
        if event.event_type == "entry":
            if open_pair is not None:
                # Consecutive entry: close the current pair as still-open
                # (0 minutes, same day preserved) and anchor a fresh pair
                # with the new entry.
                flags["consecutive_entries"].append(open_pair["entry"])
                pairs.append(open_pair)
            open_pair = {
                "entry": event,
                "exit": None,
                "minutes": 0,
                "delay_minutes": event.delay_minutes or 0,
                "is_extra": event.outcome == "OK_extra",
                "is_open": True,
                "late": (event.delay_minutes or 0) > grace,
            }
            prev_type = "entry"
        else:  # exit
            if open_pair is not None:
                open_pair["exit"] = event
                open_pair["minutes"] = int(
                    (event.timestamp - open_pair["entry"].timestamp)
                    .total_seconds()
                    // 60
                )
                open_pair["is_open"] = False
                if open_pair["late"]:
                    flags["late_entries"].append(open_pair)
                pairs.append(open_pair)
                open_pair = None
            else:
                flags["orphan_exits"].append(event)
                if prev_type == "exit":
                    flags["double_exits"].append(event)
            prev_type = "exit"

    if open_pair is not None:
        pairs.append(open_pair)

    totals = {
        "minutes": sum(p["minutes"] for p in pairs),
        "extra_minutes": sum(p["minutes"] for p in pairs if p["is_extra"]),
        "days_worked": len({_local_day(e.timestamp, tz) for e in events}),
    }

    daily, weekly, monthly = {}, {}, {}
    for pair in pairs:
        day = _local_day(pair["entry"].timestamp, tz)
        iso = day.isocalendar()
        day_key = day.isoformat()
        week_key = f"{iso[0]:04d}-W{iso[1]:02d}"
        month_key = f"{day.year:04d}-{day.month:02d}"
        minutes = pair["minutes"]
        daily[day_key] = daily.get(day_key, 0) + minutes
        weekly[week_key] = weekly.get(week_key, 0) + minutes
        monthly[month_key] = monthly.get(month_key, 0) + minutes

    return {
        "user": User.get_or_none(User.id == user_id),
        "start_date": start_date,
        "end_date": end_date,
        "pairs": pairs,
        "flags": flags,
        "totals": totals,
        "subtotals": {"daily": daily, "weekly": weekly, "monthly": monthly},
    }