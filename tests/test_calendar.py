"""Calendar availability from HA and privacy filtering."""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from roompanel.calendar import CalendarService


class FakeCalendarHA:
    connected = True
    time_zone = "Europe/Stockholm"

    def get_entity(self, ref):
        if ref == "room-calendar":
            return {"entity_id": "calendar.room", "domain": "calendar", "state": "on"}
        return None

    async def service(self, domain, action, entity_id, data, *, return_response):
        assert (domain, action, entity_id, return_response) == ("calendar", "get_events", "calendar.room", True)
        now = datetime.now(timezone.utc)
        events = [{"start": (now - timedelta(hours=3)).isoformat(),
                   "end": (now + timedelta(hours=1)).isoformat(), "summary": "Private event"},
                  {"start": (now + timedelta(minutes=30)).isoformat(),
                   "end": (now + timedelta(hours=2)).isoformat(), "summary": "Other event"}]
        return {"response": {"calendar.room": {"events": events}}}


class CalendarTests(unittest.IsolatedAsyncioTestCase):
    def test_naive_graph_and_ha_all_day_times(self):
        zone = ZoneInfo("Europe/Stockholm")
        self.assertEqual(CalendarService._parse_time("2026-11-12", zone),
                         datetime(2026, 11, 11, 23, tzinfo=timezone.utc))
        self.assertEqual(CalendarService._parse_time("2026-11-12T09:30:00", timezone.utc),
                         datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc))

    async def test_calendar_binding_uses_stable_ref_and_hides_private_details(self):
        calendar = CalendarService(None, FakeCalendarHA(), lambda: {})
        state = await calendar.status({"id": "room", "calendar": {"kind": "ha", "entity_id": "room-calendar"}})
        self.assertEqual(state["status"], "busy")
        self.assertEqual(state["timezone"], "Europe/Stockholm")
        self.assertEqual(len(state["intervals"]), 1)
        self.assertEqual(set(state["intervals"][0]), {"start", "end"})
        self.assertEqual(state["until"], state["intervals"][0]["end"])
        unknown = await calendar.status({"id": "room", "calendar": {"kind": "ha", "entity_id": "invalid"}})
        self.assertEqual(unknown["status"], "unknown")
