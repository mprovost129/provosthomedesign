"""Google event operations use stable IDs; errors never imply an empty calendar."""
from datetime import datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
from html import escape

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone


EASTERN = ZoneInfo("America/New_York")


class CalendarUnavailable(Exception):
    pass


class CalendarMismatch(CalendarUnavailable):
    pass


def moment(value, calendar_timezone=EASTERN):
    if "dateTime" in value:
        result = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=ZoneInfo(value.get("timeZone", "America/New_York")))
        return result
    return datetime.combine(datetime.fromisoformat(value["date"]).date(), time(), calendar_timezone)


def expected_events(appointment):
    common = {"visibility": "private", "transparency": "opaque",
              "extendedProperties": {"private": {"phdAppointment": str(appointment.pk)}}}
    ranges = [(appointment.starts_at, appointment.ends_at), (appointment.ends_at, appointment.reserved_until)]
    events = []
    for index, (start, end) in enumerate(ranges):
        events.append({**common, "id": appointment.event_ids[index],
            "summary": f"Client meeting: {appointment.full_name}" if index == 0 else "Client meeting buffer",
            "description": (escape(f"{appointment.reference}\n{appointment.full_name}\n{appointment.email}\n"
                f"{appointment.phone}\n\n{appointment.purpose}") if index == 0 else "Reserved 30-minute buffer after client meeting."),
            "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
            "end": {"dateTime": end.isoformat(), "timeZone": "America/New_York"}})
    return events


def validate_event(actual, expected):
    if (actual.get("id") != expected["id"] or actual.get("status") == "cancelled"
            or actual.get("extendedProperties", {}).get("private") != expected["extendedProperties"]["private"]
            or actual.get("transparency", "opaque") != "opaque"
            or moment(actual.get("start", {})) != moment(expected["start"])
            or moment(actual.get("end", {})) != moment(expected["end"])):
        raise CalendarMismatch("Calendar event changed; owner review is required.")


class GoogleCalendar:
    api = "https://www.googleapis.com/calendar/v3"

    def token(self):
        required = [getattr(settings, name, "") for name in
                    ["GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET", "GOOGLE_CALENDAR_REFRESH_TOKEN"]]
        if not all(required):
            raise CalendarUnavailable("Google Calendar is not connected.")
        # Keep access tokens in process memory, never the shared database cache.
        if getattr(self, "_token_until", 0) > timezone.now().timestamp():
            return self._token
        try:
            response = requests.post("https://oauth2.googleapis.com/token", data={
                "client_id": required[0], "client_secret": required[1], "refresh_token": required[2],
                "grant_type": "refresh_token"}, timeout=8)
            response.raise_for_status()
            payload = response.json()
            self._token = payload["access_token"]
            self._token_until = timezone.now().timestamp() + max(0, int(payload["expires_in"]) - 60)
            return self._token
        except (requests.RequestException, ValueError, KeyError):
            raise CalendarUnavailable("Google authorization needs attention.") from None

    def request(self, method, path, *, absent=False, conflict=False, **kwargs):
        try:
            response = requests.request(method, self.api + path,
                headers={"Authorization": "Bearer " + self.token(), **kwargs.pop("headers", {})},
                timeout=8, **kwargs)
            if absent and response.status_code in (404, 410):
                return None
            if conflict and response.status_code == 409:
                return None
            response.raise_for_status()
            return response.json() if response.content else {}
        except (requests.RequestException, ValueError):
            raise CalendarUnavailable("Calendar request could not be verified.") from None

    def path(self, calendar_id, event_id=""):
        return "/calendars/" + quote(calendar_id, safe="") + "/events" + ("/" + quote(event_id, safe="") if event_id else "")

    def identity(self, calendar_id):
        if not hasattr(self, "_identities"):
            self._identities = {}
        if calendar_id not in self._identities:
            metadata = self.request("GET", "/calendars/" + quote(calendar_id, safe=""))
            if not metadata.get("id") or not metadata.get("timeZone"):
                raise CalendarUnavailable("Calendar identity could not be verified.")
            self._identities[calendar_id] = metadata
        return self._identities[calendar_id]

    def busy(self, start, end, *, exclude=(), calendar_id=None):
        calendar_id = calendar_id or settings.GOOGLE_CALENDAR_ID
        metadata = self.identity(calendar_id)
        calendar_zone = ZoneInfo(metadata["timeZone"])
        calendar_id = metadata["id"]
        intervals, page = [], None
        for _ in range(100):
            params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                      "singleEvents": "true", "showDeleted": "false", "maxResults": 2500}
            if page:
                params["pageToken"] = page
            payload = self.request("GET", self.path(calendar_id), params=params)
            if "items" not in payload and payload.get("kind") == "calendar#events":
                payload["items"] = []
            if not isinstance(payload.get("items"), list):
                raise CalendarUnavailable("Calendar availability is incomplete.")
            try:
                for event in payload["items"]:
                    if event.get("id") in exclude or event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                        continue
                    intervals.append((moment(event["start"], calendar_zone), moment(event["end"], calendar_zone)))
            except (KeyError, ValueError, TypeError):
                raise CalendarUnavailable("Calendar availability is incomplete.") from None
            page = payload.get("nextPageToken")
            if not page:
                return intervals
        raise CalendarUnavailable("Calendar availability could not be fully checked.")

    def get(self, calendar_id, event_id):
        return self.request("GET", self.path(calendar_id, event_id), absent=True)

    def ensure(self, appointment, expected):
        actual = self.get(appointment.calendar_id, expected["id"])
        if actual is None:
            self.request("POST", self.path(appointment.calendar_id), json=expected,
                         params={"sendUpdates": "none"}, conflict=True)
            actual = self.get(appointment.calendar_id, expected["id"])
        if actual is None:
            raise CalendarUnavailable("Calendar save could not be verified.")
        validate_event(actual, expected)

    def remove(self, appointment):
        for expected in expected_events(appointment):
            actual = self.get(appointment.calendar_id, expected["id"])
            if actual is None or actual.get("status") == "cancelled":
                continue
            if actual.get("extendedProperties", {}).get("private", {}).get("phdAppointment") != str(appointment.pk):
                raise CalendarMismatch("Calendar event ownership does not match.")
            if not actual.get("etag"):
                raise CalendarUnavailable("Calendar deletion could not be verified.")
            self.request("DELETE", self.path(appointment.calendar_id, expected["id"]), absent=True,
                         headers={"If-Match": actual["etag"]}, params={"sendUpdates": "none"})
        for event_id in appointment.event_ids:
            actual = self.get(appointment.calendar_id, event_id)
            if actual and actual.get("status") != "cancelled":
                raise CalendarUnavailable("Calendar deletion could not be verified.")


class PreviewCalendar:
    """Fictitious calendar only: guarded by the dedicated local development setting."""
    def events(self):
        from .models import Appointment
        events = {event["id"]: {**event, "etag": "preview-only"} for item in Appointment.objects.filter(state="confirmed")
                  for event in expected_events(item)}
        events.update(cache.get("phd-preview-calendar-events", {}))
        return events

    def identity(self, calendar_id):
        return {"id": calendar_id, "timeZone": "America/New_York"}

    def busy(self, start, end, *, exclude=(), calendar_id=None):
        return [(moment(event["start"]), moment(event["end"])) for key, event in self.events().items()
                if key not in exclude and moment(event["start"]) < end and moment(event["end"]) > start]

    def get(self, calendar_id, event_id):
        return self.events().get(event_id)

    def ensure(self, appointment, expected):
        events = self.events()
        if expected["id"] in events:
            validate_event(events[expected["id"]], expected)
        else:
            events[expected["id"]] = {**expected, "etag": "preview-only"}
            cache.set("phd-preview-calendar-events", events, None)

    def remove(self, appointment):
        events = self.events()
        for key in appointment.event_ids:
            events.pop(key, None)
        cache.set("phd-preview-calendar-events", events, None)


def calendar_provider():
    backend = getattr(settings, "BOOKING_CALENDAR_BACKEND", "google")
    if backend == "preview" and getattr(settings, "INTAKE_LOCAL_DEVELOPMENT", False):
        return PreviewCalendar()
    if backend != "google":
        raise ImproperlyConfigured("Production booking requires Google Calendar.")
    return GoogleCalendar()
