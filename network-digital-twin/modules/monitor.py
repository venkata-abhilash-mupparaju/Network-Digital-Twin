"""
Network Monitoring Module
-------------------------
Performs non-disruptive, periodic ICMP reachability, packet loss, and round-trip time (RTT)
checks against authorized monitored targets on the active local network.
Strictly:
- Scoped to devices on the current network where is_monitored = 1.
- Never pings stale or historical devices from previous network sessions.
- Accurately detects state transitions (ONLINE <-> UNREACHABLE) and logs
  DEVICE_UNREACHABLE / DEVICE_RECOVERED events without duplicate flooding.
- Updates the live NetworkX Digital Twin graph.
- Records full telemetry (sent, received, packet_loss, min, avg, max latency, session_id).
"""

import logging
import threading
import time
from datetime import datetime, timezone

from config import (
    DEFAULT_MONITOR_INTERVAL,
    DEFAULT_PING_TIMEOUT,
    LATENCY_THRESHOLD,
    PACKET_LOSS_THRESHOLD
)
from database import get_db_connection
from .network_info import check_network_change, sync_network_session, get_active_session
from .icmp import ping_host, ping_device_probes, validate_ip
from .events import (
    log_event,
    EVENT_DEVICE_ONLINE,
    EVENT_DEVICE_UNREACHABLE,
    EVENT_DEVICE_RECOVERED,
    EVENT_LATENCY_THRESHOLD,
    EVENT_PACKET_LOSS_THRESHOLD,
    EVENT_NETWORK_CHANGED
)

logger = logging.getLogger(__name__)


class NetworkMonitor:
    def __init__(self, digital_twin, interval=DEFAULT_MONITOR_INTERVAL):
        """
        Manages the background telemetry loop that observes the real network
        and synchronizes the live Digital Twin.
        """
        self.digital_twin = digital_twin
        self.interval = max(3, interval)
        self.is_running = False
        self._thread = None
        self._previous_states = {}  # device_id -> status
        self._previous_loss = {}    # device_id -> packet_loss
        self._previous_alerted = {} # device_id -> bool
        self.current_network_info = {}

    def start(self):
        """Starts the background monitoring loop in a daemon thread."""
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="NetworkMonitorThread")
        self._thread.start()
        logger.info("Network Monitor started with interval of %d seconds.", self.interval)

    def stop(self):
        """Stops the monitoring loop gracefully."""
        self.is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("Network Monitor stopped.")

    def run_single_cycle(self):
        """
        Executes one complete monitoring pass:
        1. Checks for network transitions (Hotspot A -> Hotspot B).
        2. Syncs network session and marks non-current network devices as STALE.
        3. Queries ONLY active monitored devices for the current network.
        4. Updates Digital Twin in-memory graph to strictly represent current network.
        5. Pings current targets, records telemetry with min/max latency and session_id.
        6. Fires deduplicated events on state changes (UNREACHABLE, RECOVERED, THRESHOLDS).
        """
        results = []
        try:
            # 1. Detect active network state
            net_info, has_changed = check_network_change()
            self.current_network_info = net_info
            current_net_id = net_info.get("network_id")
            current_gateway = net_info.get("gateway")

            # 2. Synchronize active network session in SQLite
            session_id = sync_network_session(net_info)

            with get_db_connection() as conn:
                cursor = conn.cursor()

                # If network changed, update gateway in Digital Twin and log event
                if has_changed:
                    if current_gateway and hasattr(self.digital_twin, "update_gateway_ip"):
                        self.digital_twin.update_gateway_ip(current_gateway)
                    log_event("SYSTEM", EVENT_NETWORK_CHANGED,
                              f"Network transition detected: switched to {current_net_id}", conn=conn)

                # 3. Query ONLY devices belonging to the CURRENT network that are marked is_monitored = 1
                cursor.execute(
                    """
                    SELECT device_id, device_name, ip_address, network_id, device_type, description
                    FROM devices
                    WHERE network_id = ? AND is_monitored = 1
                    """,
                    (current_net_id,)
                )
                current_devices = [dict(r) for r in cursor.fetchall()]

                # 4. Synchronize Digital Twin graph so it ONLY contains current network nodes
                self.digital_twin.sync_with_current_network(
                    gateway_ip=current_gateway,
                    monitored_devices=current_devices
                )

                # 5. Monitor each device on the CURRENT network
                for dev in current_devices:
                    dev_id = dev["device_id"]
                    dev_name = dev["device_name"]
                    ip = dev["ip_address"]

                    is_online, latency, err = ping_host(ip, timeout_seconds=DEFAULT_PING_TIMEOUT)
                    current_status = "ONLINE" if is_online else "UNREACHABLE"
                    packet_loss = 0.0 if is_online else 100.0
                    prev_status = self._previous_states.get(dev_id)

                    # Update Digital Twin graph
                    self.digital_twin.update_device_status(dev_id, current_status, latency)

                    # Update device in SQLite devices table
                    cursor.execute(
                        """
                        UPDATE devices
                        SET status = ?,
                            last_seen = CASE WHEN ? = 'ONLINE' THEN CURRENT_TIMESTAMP ELSE last_seen END
                        WHERE device_id = ?
                        """,
                        (current_status, current_status, dev_id)
                    )

                    # Persist telemetry record in device_status table
                    cursor.execute(
                        """
                        INSERT INTO device_status (
                            device_id, status, latency, min_latency, max_latency, last_latency,
                            packets_sent, packets_received, packet_loss, network_id, session_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (dev_id, current_status, latency, latency, latency, latency,
                         1, 1 if is_online else 0, packet_loss, current_net_id, session_id)
                    )

                    # Deduplicated State Transitions
                    if prev_status is not None and prev_status != current_status:
                        if current_status == "UNREACHABLE":
                            msg = f"Device {dev_name} ({dev_id} @ {ip}) is UNREACHABLE (100% packet loss)"
                            log_event(dev_id, EVENT_DEVICE_UNREACHABLE, msg, conn=conn)
                        elif current_status == "ONLINE" and prev_status == "UNREACHABLE":
                            msg = f"Device {dev_name} ({dev_id} @ {ip}) RECOVERED: status is ONLINE ({latency} ms)"
                            log_event(dev_id, EVENT_DEVICE_RECOVERED, msg, conn=conn)
                        else:
                            msg = f"Device {dev_name} ({dev_id}) state changed from {prev_status} to {current_status}"
                            log_event(dev_id, "STATUS_CHANGE", msg, conn=conn)

                    # Alert on High Latency (only when exceeding threshold and not previously alerted)
                    if is_online and latency and latency > LATENCY_THRESHOLD:
                        if not self._previous_alerted.get(f"{dev_id}_lat"):
                            msg = f"{dev_name} latency ({latency} ms) exceeded threshold ({LATENCY_THRESHOLD} ms)"
                            log_event(dev_id, EVENT_LATENCY_THRESHOLD, msg, conn=conn)
                            self._previous_alerted[f"{dev_id}_lat"] = True
                    else:
                        self._previous_alerted[f"{dev_id}_lat"] = False

                    # Alert on High Packet Loss
                    if packet_loss >= PACKET_LOSS_THRESHOLD:
                        if not self._previous_alerted.get(f"{dev_id}_loss"):
                            msg = f"{dev_name} packet loss ({packet_loss}%) exceeded threshold ({PACKET_LOSS_THRESHOLD}%)"
                            log_event(dev_id, EVENT_PACKET_LOSS_THRESHOLD, msg, conn=conn)
                            self._previous_alerted[f"{dev_id}_loss"] = True
                    else:
                        self._previous_alerted[f"{dev_id}_loss"] = False

                    self._previous_states[dev_id] = current_status
                    self._previous_loss[dev_id] = packet_loss

                    results.append({
                        "device_id": dev_id,
                        "device_name": dev_name,
                        "ip": ip,
                        "status": current_status,
                        "latency": latency,
                        "packet_loss": packet_loss,
                        "error": err
                    })

                conn.commit()

        except Exception as e:
            logger.error("Error during monitoring cycle: %s", e)

        return results

    def _monitor_loop(self):
        """Worker loop running in background thread."""
        logger.info("Background monitoring thread active.")
        while self.is_running:
            try:
                self.run_single_cycle()
            except Exception as e:
                logger.error("Unexpected error in monitoring loop: %s", e)

            for _ in range(self.interval * 2):
                if not self.is_running:
                    break
                time.sleep(0.5)
