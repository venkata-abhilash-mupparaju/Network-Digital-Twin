"""
Network Events Module
---------------------
Manages system and device event generation, deduplication, and persistence in SQLite.
Ensures that recurring events (e.g. repeated ping failure every 10 seconds) do not flood
the events table or user notifications.
"""

import logging
from datetime import datetime, timezone

from database import get_db_connection
from utils.time_utils import enrich_timestamp_dict

logger = logging.getLogger(__name__)

# Standard Event Types
EVENT_NETWORK_CHANGED = "NETWORK_CHANGED"
EVENT_DEVICE_DISCOVERED = "DEVICE_DISCOVERED"
EVENT_DEVICE_ADDED_TO_MONITORING = "DEVICE_ADDED_TO_MONITORING"
EVENT_DEVICE_ONLINE = "DEVICE_ONLINE"
EVENT_DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"
EVENT_DEVICE_RECOVERED = "DEVICE_RECOVERED"
EVENT_LATENCY_THRESHOLD = "LATENCY_THRESHOLD"
EVENT_PACKET_LOSS_THRESHOLD = "PACKET_LOSS_THRESHOLD"
EVENT_SIMULATION_STARTED = "SIMULATION_STARTED"
EVENT_SIMULATION_RESET = "SIMULATION_RESET"


def log_event(device_id, event_type, message, conn=None):
    """
    Persists an event into SQLite network_events table.
    
    Args:
        device_id (str): Identifier of device or 'SYSTEM' / 'SIMULATION'.
        event_type (str): Categorized event type.
        message (str): Human-readable event description.
        conn (sqlite3.Connection, optional): Existing DB connection if within a transaction.
    """
    dev_str = str(device_id) if device_id else "SYSTEM"
    try:
        if conn is not None:
            conn.execute(
                "INSERT INTO network_events (device_id, event_type, message) VALUES (?, ?, ?)",
                (dev_str, event_type, message)
            )
        else:
            with get_db_connection() as c:
                c.execute(
                    "INSERT INTO network_events (device_id, event_type, message) VALUES (?, ?, ?)",
                    (dev_str, event_type, message)
                )
                c.commit()
        logger.info("Event [%s] (%s): %s", event_type, dev_str, message)
    except Exception as e:
        logger.error("Failed to log network event: %s", e)


def get_recent_events(limit=50, event_type=None, device_id=None):
    """
    Retrieves recent network events from SQLite.
    
    Returns:
        list of dicts
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM network_events WHERE 1=1"
            params = []
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            if device_id:
                query += " AND device_id = ?"
                params.append(device_id)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = [dict(r) for r in cursor.fetchall()]
            return [enrich_timestamp_dict(r) for r in rows]
    except Exception as e:
        logger.error("Error retrieving network events: %s", e)
        return []
