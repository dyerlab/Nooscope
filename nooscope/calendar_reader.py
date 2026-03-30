"""EventKit bridge for reading calendar events on macOS.

Requires pyobjc-framework-EventKit:
    pip install pyobjc-framework-EventKit

TCC note: the first call will trigger a system permission prompt for calendar
access. Grant it to whichever process is running (Terminal, Python binary, etc.).
On macOS 14+ the API uses requestFullAccessToEventsWithCompletion_; older
versions use requestAccessToEntityType_completion_.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, time, timezone
from typing import Any

log = logging.getLogger(__name__)

# Video call URL patterns to extract from location / notes fields
_VIDEO_URL_RE = re.compile(
    r"https?://\S*(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com"
    r"|webex\.com|gotomeeting\.com|bluejeans\.com|whereby\.com)\S*",
    re.IGNORECASE,
)


def _nsdate_to_datetime(nsdate) -> datetime | None:
    if nsdate is None:
        return None
    ts = nsdate.timeIntervalSince1970()
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()


def _participant_info(p) -> dict[str, str]:
    name = str(p.name() or "")
    email = ""
    url = p.URL()
    if url:
        url_str = str(url.absoluteString())
        if url_str.startswith("mailto:"):
            email = url_str[7:]
    role_map = {0: "unknown", 1: "required", 2: "optional", 3: "chair", 4: "non-participant"}
    status_map = {0: "unknown", 1: "pending", 2: "accepted", 3: "declined", 4: "tentative"}
    return {
        "name": name,
        "email": email,
        "role": role_map.get(int(p.participantRole()), "unknown"),
        "status": status_map.get(int(p.participantStatus()), "unknown"),
        "is_me": bool(p.isCurrentUser()),
    }


def _extract_video_url(text: str | None) -> str:
    if not text:
        return ""
    m = _VIDEO_URL_RE.search(text)
    return m.group(0) if m else ""


def _event_to_dict(event) -> dict[str, Any]:
    start = _nsdate_to_datetime(event.startDate())
    end = _nsdate_to_datetime(event.endDate())
    is_all_day = bool(event.isAllDay())

    location = str(event.location() or "")
    notes = str(event.notes() or "")

    video_url = _extract_video_url(location) or _extract_video_url(notes)

    organizer = None
    org = event.organizer()
    if org:
        organizer = _participant_info(org)

    attendees: list[dict] = []
    raw_attendees = event.attendees()
    if raw_attendees:
        for p in raw_attendees:
            attendees.append(_participant_info(p))

    cal = event.calendar()
    calendar_name = str(cal.title()) if cal else ""

    return {
        "title": str(event.title() or ""),
        "start": start,
        "end": end,
        "is_all_day": is_all_day,
        "location": location,
        "notes": notes,
        "video_url": video_url,
        "organizer": organizer,
        "attendees": attendees,
        "calendar_name": calendar_name,
        "external_id": str(event.calendarItemExternalIdentifier() or ""),
    }


def _request_access(store) -> bool:
    """Request calendar access and block until granted or denied. Returns True if granted."""
    granted_holder: list[bool] = [False]
    done = threading.Event()

    def handler(granted, error):
        granted_holder[0] = bool(granted)
        done.set()

    # macOS 14+ prefers requestFullAccessToEventsWithCompletion_
    try:
        store.requestFullAccessToEventsWithCompletion_(handler)
    except AttributeError:
        import EventKit as _EK
        store.requestAccessToEntityType_completion_(_EK.EKEntityTypeEvent, handler)

    done.wait(timeout=30)
    return granted_holder[0]


def get_events_for_date(
    target_date: date,
    calendars: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return all calendar events for target_date, sorted all-day first then by start time.

    Args:
        target_date: The date to fetch events for.
        calendars: Optional list of calendar names to filter by. Empty/None = all.

    Returns:
        List of event dicts. Returns [] if EventKit is unavailable or access denied.
    """
    try:
        import EventKit
        from Foundation import NSDate, NSCalendar, NSCalendarUnitYear, NSCalendarUnitMonth, NSCalendarUnitDay
    except ImportError:
        log.warning("pyobjc-framework-EventKit not installed; skipping calendar injection")
        return []

    store = EventKit.EKEventStore.alloc().init()

    if not _request_access(store):
        log.warning("Calendar access denied; skipping calendar injection")
        return []

    # Build start-of-day and end-of-day NSDate for target_date
    ns_cal = NSCalendar.currentCalendar()
    components = ns_cal.components_fromDate_(
        NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay,
        NSDate.date(),
    )
    components.setYear_(target_date.year)
    components.setMonth_(target_date.month)
    components.setDay_(target_date.day)
    components.setHour_(0)
    components.setMinute_(0)
    components.setSecond_(0)
    start_ns = ns_cal.dateFromComponents_(components)

    components.setHour_(23)
    components.setMinute_(59)
    components.setSecond_(59)
    end_ns = ns_cal.dateFromComponents_(components)

    # Optionally filter by calendar name
    all_cals = store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)
    if calendars:
        cal_names = set(calendars)
        all_cals = [c for c in all_cals if str(c.title()) in cal_names]

    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        start_ns, end_ns, all_cals
    )
    raw_events = store.eventsMatchingPredicate_(predicate)

    events = [_event_to_dict(e) for e in (raw_events or [])]

    # Sort: all-day events first, then by start time
    def sort_key(e):
        if e["is_all_day"]:
            return (0, datetime.min.replace(tzinfo=timezone.utc))
        return (1, e["start"] or datetime.min.replace(tzinfo=timezone.utc))

    events.sort(key=sort_key)
    return events
