"""Service to fetch and parse iCalendar (.ics) feeds from external calendars."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, date

import httpx
from icalendar import Calendar
import recurring_ical_events

from models.DBCalendarEvent import DBCalendarEvent

logger = logging.getLogger(__name__)

# How far into the past/future we expand recurring events (months)
SYNC_WINDOW_MONTHS = 3

_LOGIN_PAGE_HINT = (
    "The URL returned an HTML page (likely a login/sign-in page) instead of "
    "calendar data. Make sure you use the secret iCal address, not a regular "
    "calendar link.\n\n"
    "• Google Calendar: Settings ⇒ your calendar ⇒ Integrate calendar ⇒ "
    '"Secret address in iCal format"\n'
    "• Outlook / Live: Calendar Settings ⇒ Shared calendars ⇒ "
    "Publish a calendar ⇒ ICS link"
)


class ICalSyncService:
    """Fetches an iCal URL and returns a list of DBCalendarEvent objects."""

    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    @staticmethod
    def _check_response_is_ical(response: httpx.Response) -> None:
        """Raise a clear error if the response looks like HTML instead of iCal data."""
        content_type = response.headers.get("content-type", "")
        body_start = response.content[:512].strip().lower()

        is_html = "text/html" in content_type or body_start.startswith(b"<!doctype") or body_start.startswith(b"<html")
        if is_html:
            raise ValueError(_LOGIN_PAGE_HINT)

        # Also check it looks vaguely like an iCal file
        if b"BEGIN:VCALENDAR" not in response.content[:2048]:
            raise ValueError(
                "The URL did not return valid iCalendar data. " "Expected a .ics feed starting with BEGIN:VCALENDAR."
            )

    def fetch_and_parse(
        self,
        ical_url: str,
        provider_name: str,
    ) -> list[DBCalendarEvent]:
        """
        Download the .ics feed and return calendar events expanded for the
        configured sync window.
        """
        logger.info("Fetching iCal feed: %s", ical_url)
        response = httpx.get(ical_url, timeout=self._timeout, follow_redirects=True)
        response.raise_for_status()
        self._check_response_is_ical(response)

        cal = Calendar.from_ical(response.content)

        now = datetime.now()
        start_window = (now - timedelta(days=SYNC_WINDOW_MONTHS * 30)).date()
        end_window = (now + timedelta(days=SYNC_WINDOW_MONTHS * 30)).date()

        expanded = recurring_ical_events.of(cal).between(start_window, end_window)

        events: list[DBCalendarEvent] = []
        seen_uids: set[str] = set()

        for component in expanded:
            if component.name != "VEVENT":
                continue

            uid = str(component.get("UID", ""))
            if not uid:
                continue

            summary = str(component.get("SUMMARY", ""))
            description = str(component.get("DESCRIPTION", "")) if component.get("DESCRIPTION") else None
            location = str(component.get("LOCATION", "")) if component.get("LOCATION") else None

            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")

            if not dtstart:
                continue

            dtstart_val = dtstart.dt if dtstart else None
            dtend_val = dtend.dt if dtend else None

            # Determine all-day vs timed
            is_all_day = isinstance(dtstart_val, date) and not isinstance(dtstart_val, datetime)

            start_str = self._to_datetime_str(dtstart_val, is_all_day)
            if dtend_val:
                end_str = self._to_datetime_str(dtend_val, is_all_day)
            else:
                # If no end, default to start + 1 hour (or same day for all-day)
                if is_all_day:
                    end_str = start_str
                else:
                    if isinstance(dtstart_val, datetime):
                        end_str = (dtstart_val + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        end_str = start_str

            # For recurring events, make uid unique per occurrence by appending the start date
            occurrence_uid = uid
            if occurrence_uid in seen_uids:
                occurrence_uid = f"{uid}_{start_str[:10]}"
            seen_uids.add(occurrence_uid)

            # Extract meeting URL from description or URL property
            meeting_url = None
            url_prop = component.get("URL")
            if url_prop:
                meeting_url = str(url_prop)

            # Extract event status (CONFIRMED, TENTATIVE, CANCELLED)
            raw_status = str(component.get("STATUS", "CONFIRMED")).upper()
            status = raw_status.lower() if raw_status in ("CONFIRMED", "TENTATIVE", "CANCELLED") else "confirmed"

            events.append(
                DBCalendarEvent(
                    id=None,
                    provider=provider_name,
                    external_id=occurrence_uid,
                    title=summary or "(No title)",
                    description=description,
                    start_at=start_str,
                    end_at=end_str,
                    is_all_day=is_all_day,
                    location=location,
                    meeting_url=meeting_url,
                    status=status,
                )
            )

        logger.info("Parsed %d events from iCal feed for '%s'", len(events), provider_name)
        return events

    @staticmethod
    def _to_datetime_str(dt_val, is_all_day: bool) -> str:
        """Convert a date or datetime to 'YYYY-MM-DD HH:MM:SS' string."""
        if is_all_day and isinstance(dt_val, date) and not isinstance(dt_val, datetime):
            return f"{dt_val.isoformat()} 00:00:00"
        if isinstance(dt_val, datetime):
            # Strip timezone info for naive storage
            return dt_val.strftime("%Y-%m-%d %H:%M:%S")
        return f"{dt_val} 00:00:00"

    def validate_url(self, ical_url: str) -> dict:
        """Quick validation: fetch the URL and check it's a valid iCal feed."""
        try:
            response = httpx.get(ical_url, timeout=15, follow_redirects=True)
            response.raise_for_status()
            self._check_response_is_ical(response)
            cal = Calendar.from_ical(response.content)
            # Count VEVENT components
            event_count = sum(1 for c in cal.walk() if c.name == "VEVENT")
            cal_name = str(cal.get("X-WR-CALNAME", "")) or None
            return {"ok": True, "event_count": event_count, "calendar_name": cal_name}
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
