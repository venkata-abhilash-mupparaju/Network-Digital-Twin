"""
Centralized Time & Timezone Utilities
-------------------------------------
Handles uniform UTC storage and Asia/Kolkata (IST, UTC+05:30) presentation
for telemetry, network events, and connection sessions.

Fixes the UTC discrepancy where system telemetry appeared in UTC (e.g. 04:xx)
rather than Indian Standard Time (10:xx).
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Union, Dict, Any
import logging

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Kolkata"
IST_FALLBACK_OFFSET = timezone(timedelta(hours=5, minutes=30), name="IST")


def get_local_tz(tz_name: str = DEFAULT_TIMEZONE):
    """
    Returns the ZoneInfo for the specified timezone name, falling back to
    UTC+05:30 if the zone database is unavailable.
    """
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception as e:
        logger.debug("ZoneInfo lookup failed for %s (%s); using IST offset fallback.", tz_name, e)
        return IST_FALLBACK_OFFSET


def utc_now() -> datetime:
    """Returns timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    """
    Returns an ISO 8601 string in UTC format (e.g. '2026-10-06T04:36:40Z').
    """
    if dt is None:
        dt = utc_now()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_datetime(val: Union[datetime, str, None]) -> Optional[datetime]:
    """
    Parses various datetime representations into a timezone-aware UTC datetime.
    Supports:
    - datetime objects (naive assumed UTC, aware converted to UTC)
    - ISO 8601 strings (e.g. '2026-10-06T04:36:40Z', '2026-10-06T04:36:40+00:00')
    - SQLite CURRENT_TIMESTAMP ('2026-10-06 04:36:40')
    - SQLite with microseconds ('2026-10-06 04:36:40.123456')
    """
    if val is None:
        return None

    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    if not isinstance(val, str):
        return None

    s = val.strip()
    if not s:
        return None

    # Replace 'Z' with '+00:00' for fromisoformat compatibility
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    # Try ISO parsing
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass

    # Try standard SQLite format 'YYYY-MM-DD HH:MM:SS'
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%Y/%m/%d %H:%M:%S"
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    logger.warning("Could not parse datetime string: %r", val)
    return None


def utc_to_local(val: Union[datetime, str, None], tz_name: str = DEFAULT_TIMEZONE) -> Optional[datetime]:
    """
    Converts a UTC datetime (or string) to local datetime in target timezone.
    """
    utc_dt = parse_utc_datetime(val)
    if utc_dt is None:
        return None
    return utc_dt.astimezone(get_local_tz(tz_name))


def format_local_time(
    val: Union[datetime, str, None],
    tz_name: str = DEFAULT_TIMEZONE,
    fmt: str = "%d %b %Y, %I:%M:%S %p IST"
) -> str:
    """
    Formats a UTC datetime/string to a human-readable local time string.
    Example output: '06 Oct 2026, 10:30:27 AM IST'
    """
    loc_dt = utc_to_local(val, tz_name=tz_name)
    if loc_dt is None:
        return "N/A"
    return loc_dt.strftime(fmt)


def format_local_iso(val: Union[datetime, str, None], tz_name: str = DEFAULT_TIMEZONE) -> str:
    """
    Formats a UTC datetime/string to an ISO string in the local timezone.
    Example: '2026-10-06T10:30:27+05:30'
    """
    loc_dt = utc_to_local(val, tz_name=tz_name)
    if loc_dt is None:
        return ""
    return loc_dt.isoformat()


def format_display_time(val: Union[datetime, str, None], tz_name: str = DEFAULT_TIMEZONE) -> str:
    """
    Convenience alias for format_local_time.
    """
    return format_local_time(val, tz_name=tz_name)


def enrich_timestamp_dict(
    record: Dict[str, Any],
    timestamp_key: str = "timestamp",
    tz_name: str = DEFAULT_TIMEZONE
) -> Dict[str, Any]:
    """
    Augments a dictionary (e.g. SQLite row) containing a timestamp with both
    UTC and local representations:
    - timestamp_utc: ISO UTC string
    - timestamp_local: Human-formatted local IST string
    - timestamp_local_iso: Local ISO 8601 string
    - timestamp_display: Display string
    - timestamp: updated to human-friendly local string if originally SQLite raw string
    """
    raw_val = record.get(timestamp_key)
    parsed = parse_utc_datetime(raw_val)

    if parsed is not None:
        utc_str = utc_iso(parsed)
        local_str = format_local_time(parsed, tz_name=tz_name)
        local_iso = format_local_iso(parsed, tz_name=tz_name)

        record["timestamp_utc"] = utc_str
        record["timestamp_local"] = local_str
        record["timestamp_local_iso"] = local_iso
        record["timestamp_display"] = local_str
        # For templates rendering {{ ev.timestamp }}, provide human local time while keeping raw_timestamp available
        record["raw_timestamp"] = raw_val
        record["timestamp"] = local_str
    else:
        record["timestamp_utc"] = None
        record["timestamp_local"] = "N/A"
        record["timestamp_local_iso"] = ""
        record["timestamp_display"] = "N/A"

    return record


def enrich_session_dict(
    session: Dict[str, Any],
    tz_name: str = DEFAULT_TIMEZONE
) -> Dict[str, Any]:
    """
    Enriches start_time and end_time fields in a network_sessions record.
    """
    start_raw = session.get("start_time")
    start_parsed = parse_utc_datetime(start_raw)
    if start_parsed:
        session["start_time_utc"] = utc_iso(start_parsed)
        session["start_time_local"] = format_local_time(start_parsed, tz_name=tz_name)
        session["start_time_display"] = session["start_time_local"]
    else:
        session["start_time_utc"] = None
        session["start_time_local"] = "N/A"
        session["start_time_display"] = "N/A"

    end_raw = session.get("end_time")
    if end_raw:
        end_parsed = parse_utc_datetime(end_raw)
        if end_parsed:
            session["end_time_utc"] = utc_iso(end_parsed)
            session["end_time_local"] = format_local_time(end_parsed, tz_name=tz_name)
            session["end_time_display"] = session["end_time_local"]
        else:
            session["end_time_utc"] = None
            session["end_time_local"] = "N/A"
            session["end_time_display"] = "N/A"
    else:
        session["end_time_utc"] = None
        session["end_time_local"] = None
        session["end_time_display"] = "Active"

    return session
