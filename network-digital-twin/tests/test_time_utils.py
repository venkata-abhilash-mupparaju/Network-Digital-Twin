"""
Unit Tests for Centralized Time & Timezone Utilities
-----------------------------------------------------
Validates UTC ISO 8601 parsing, SQLite CURRENT_TIMESTAMP compatibility,
conversion to Asia/Kolkata (IST, UTC+05:30), and dictionary enrichment.
"""

import unittest
from datetime import datetime, timezone, timedelta

from utils.time_utils import (
    DEFAULT_TIMEZONE,
    utc_now,
    utc_iso,
    parse_utc_datetime,
    utc_to_local,
    format_local_time,
    format_local_iso,
    format_display_time,
    enrich_timestamp_dict,
    enrich_session_dict
)


class TestTimeUtils(unittest.TestCase):
    def test_utc_now_is_timezone_aware(self):
        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.tzinfo, timezone.utc)

    def test_utc_iso_format(self):
        dt = datetime(2026, 10, 6, 4, 30, 0, tzinfo=timezone.utc)
        iso = utc_iso(dt)
        self.assertEqual(iso, "2026-10-06T04:30:00Z")

    def test_parse_sqlite_current_timestamp(self):
        """SQLite CURRENT_TIMESTAMP generates 'YYYY-MM-DD HH:MM:SS' in UTC."""
        sqlite_ts = "2026-10-06 04:30:00"
        dt = parse_utc_datetime(sqlite_ts)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 6)
        self.assertEqual(dt.hour, 4)
        self.assertEqual(dt.minute, 30)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parse_iso_string(self):
        iso_str = "2026-10-06T04:30:00Z"
        dt = parse_utc_datetime(iso_str)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 4)
        self.assertEqual(dt.minute, 30)

    def test_utc_to_local_ist_conversion(self):
        """04:30:00 UTC should convert to 10:00:00 IST (+05:30)."""
        sqlite_ts = "2026-10-06 04:30:00"
        loc_dt = utc_to_local(sqlite_ts, tz_name="Asia/Kolkata")
        self.assertIsNotNone(loc_dt)
        self.assertEqual(loc_dt.hour, 10)
        self.assertEqual(loc_dt.minute, 0)
        self.assertEqual(loc_dt.day, 6)

    def test_format_local_time(self):
        sqlite_ts = "2026-10-06 04:30:00"
        formatted = format_local_time(sqlite_ts, tz_name="Asia/Kolkata")
        self.assertIn("06 Oct 2026", formatted)
        self.assertIn("10:00:00 AM IST", formatted)

    def test_format_local_iso(self):
        sqlite_ts = "2026-10-06 04:30:00"
        iso = format_local_iso(sqlite_ts, tz_name="Asia/Kolkata")
        self.assertTrue(iso.startswith("2026-10-06T10:00:00+05:30") or iso.startswith("2026-10-06T10:00:00"))

    def test_enrich_timestamp_dict(self):
        record = {
            "id": 1,
            "device_id": "PC1",
            "timestamp": "2026-10-06 04:30:00"
        }
        enriched = enrich_timestamp_dict(record)
        self.assertEqual(enriched["timestamp_utc"], "2026-10-06T04:30:00Z")
        self.assertIn("10:00:00 AM IST", enriched["timestamp_local"])
        self.assertEqual(enriched["raw_timestamp"], "2026-10-06 04:30:00")
        self.assertIn("10:00:00 AM IST", enriched["timestamp"])

    def test_enrich_session_dict(self):
        session = {
            "session_id": 1,
            "start_time": "2026-10-06 04:00:00",
            "end_time": "2026-10-06 05:00:00"
        }
        enriched = enrich_session_dict(session)
        self.assertIn("09:30:00 AM IST", enriched["start_time_local"])
        self.assertIn("10:30:00 AM IST", enriched["end_time_local"])


if __name__ == "__main__":
    unittest.main()
