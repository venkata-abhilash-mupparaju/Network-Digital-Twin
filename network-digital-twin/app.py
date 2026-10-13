"""
Network Digital Twin - Central Flask Application
------------------------------------------------
Provides full REST APIs, background telemetry monitoring,
and web interfaces for Live Monitoring, Device Management,
What-If Simulation, and Telemetry History.
"""

import logging
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, abort

from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    SECRET_KEY, DEBUG, HOST, PORT,
    HOTSPOT_GATEWAY_ID, HOTSPOT_GATEWAY_NAME, HOTSPOT_GATEWAY_TYPE,
    DEFAULT_MONITOR_INTERVAL, LATENCY_ALERT_THRESHOLD,
    PING_COUNT, PING_TIMEOUT
)
from database import init_db, get_db_connection
from utils.time_utils import (
    utc_now,
    utc_iso,
    format_local_time,
    format_local_iso,
    format_display_time,
    enrich_timestamp_dict,
    enrich_session_dict
)
from modules import (
    DigitalTwinNetwork,
    NetworkMonitor,
    NetworkSimulator,
    ping_host,
    ping_device_probes,
    validate_ip,
    discover_local_devices,
    discover_devices_active,
    get_discovery_progress,
    get_current_network_info,
    check_network_change,
    sync_network_session,
    get_active_session,
    get_current_authorized_devices,
    ensure_authorized_test_devices,
    log_event,
    get_recent_events,
    EVENT_NETWORK_CHANGED,
    EVENT_DEVICE_DISCOVERED,
    EVENT_DEVICE_ADDED_TO_MONITORING,
    EVENT_DEVICE_ONLINE,
    EVENT_DEVICE_UNREACHABLE,
    EVENT_DEVICE_RECOVERED,
    EVENT_LATENCY_THRESHOLD,
    EVENT_PACKET_LOSS_THRESHOLD,
    EVENT_SIMULATION_STARTED,
    EVENT_SIMULATION_RESET
)

# In-memory cache for latest discovered devices
_last_discovered_cache = []

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
)
logger = logging.getLogger("net_twin.app")

# Initialize Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY

# 1. Initialize In-Memory Digital Twin Graph
digital_twin = DigitalTwinNetwork()

# 2. Initialize What-If Simulation Engine (operates on copy)
simulator = NetworkSimulator(digital_twin)

# 3. Initialize Background Network Monitoring Engine
monitor = NetworkMonitor(digital_twin, interval=DEFAULT_MONITOR_INTERVAL)


def sync_initial_network_twin(net_info, session_id):
    """
    Synchronizes initial network twin with authorized devices on current network.
    Ensures baseline authorized devices exist and populates the Digital Twin graph.
    """
    if net_info.get("gateway"):
        digital_twin.update_gateway_ip(net_info["gateway"])

    # Ensure baseline authorized devices exist for this network session
    authorized = get_current_authorized_devices(net_info)
    if not authorized:
        authorized = ensure_authorized_test_devices(net_info)

    monitored = [d for d in authorized if d.get("is_monitored") == 1]
    digital_twin.sync_with_current_network(
        gateway_ip=net_info.get("gateway"),
        authorized_devices=authorized,
        monitored_devices=monitored,
        session_id=session_id,
        network_cidr=net_info.get("network")
    )
    logger.info("Initialized Digital Twin with %d authorized device(s) on %s", len(authorized), net_info.get("network_id"))


# Initialize DB
init_db()

# Synchronize current active network & session
initial_net = get_current_network_info()
initial_net_id = initial_net.get("network_id")
initial_session_id = sync_network_session(initial_net)

sync_initial_network_twin(initial_net, initial_session_id)

simulator.clone_from_digital_twin(digital_twin, initial_net)
monitor.start()


# ============================================================================
# WEB PAGE ROUTES
# ============================================================================

@app.route("/")
def index():
    """Renders the real-time Digital Twin Dashboard."""
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    stale_count = 0
    recent_events = []

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM devices WHERE (network_id != ? OR network_id IS NULL)",
                (current_net_id,)
            )
            st_row = cursor.fetchone()
            if st_row:
                stale_count = st_row["cnt"]

            cursor.execute("SELECT * FROM network_events ORDER BY id DESC LIMIT 8")
            recent_events = [enrich_timestamp_dict(dict(r)) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("Error fetching dashboard data: %s", e)

    summary = digital_twin.get_summary(stale_count=stale_count)

    return render_template(
        "index.html",
        active_page="dashboard",
        summary=summary,
        gateway_id=HOTSPOT_GATEWAY_ID,
        gateway_name=HOTSPOT_GATEWAY_NAME,
        recent_events=recent_events,
        current_network=net_info
    )


@app.route("/devices")
def devices_page():
    """Renders the Device Management page with Current vs Historical separation."""
    current_devices = []
    historical_devices = []
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    gateway_ip = net_info.get("gateway") or "192.168.43.1"

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices ORDER BY id ASC")
            all_rows = cursor.fetchall()
            for r in all_rows:
                if r["network_id"] == current_net_id:
                    current_devices.append(r)
                else:
                    historical_devices.append(r)
    except Exception as e:
        logger.error("Error fetching devices: %s", e)

    return render_template(
        "devices.html",
        active_page="devices",
        current_devices=current_devices,
        historical_devices=historical_devices,
        devices=current_devices,  # backward compatibility
        current_network=net_info,
        gateway={"id": HOTSPOT_GATEWAY_ID, "name": HOTSPOT_GATEWAY_NAME, "ip": gateway_ip}
    )


@app.route("/simulation")
def simulation_page():
    """Renders the What-If Simulation Environment."""
    return render_template(
        "simulation.html",
        active_page="simulation",
        gateway_id=HOTSPOT_GATEWAY_ID
    )


@app.route("/history")
def history_page():
    """Renders Historical Telemetry, Sessions, and Event Audit Logs."""
    events = []
    statuses = []
    sessions = []
    total_pings = 0
    uptime_pct = 100.0
    total_events = 0
    monitored_devices_count = 0
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Fetch recent events enriched with IST and UTC timestamps
            cursor.execute("SELECT * FROM network_events ORDER BY id DESC LIMIT 100")
            events = [enrich_timestamp_dict(dict(r)) for r in cursor.fetchall()]
            
            # Fetch recent status checks enriched with device name and local timestamps
            cursor.execute("""
                SELECT s.*, d.device_name
                FROM device_status s
                LEFT JOIN devices d ON s.device_id = d.device_id
                ORDER BY s.id DESC LIMIT 100
            """)
            statuses = [enrich_timestamp_dict(dict(r)) for r in cursor.fetchall()]

            # Fetch network sessions enriched with local start/end times
            cursor.execute("SELECT * FROM network_sessions ORDER BY session_id DESC LIMIT 50")
            sessions = [enrich_session_dict(dict(r)) for r in cursor.fetchall()]

            # Compute historical uptime % and ping total
            cursor.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN status='ONLINE' THEN 1 ELSE 0 END) AS online_cnt FROM device_status")
            stats_row = cursor.fetchone()
            if stats_row and stats_row["total"] > 0:
                total_pings = stats_row["total"]
                online_pings = stats_row["online_cnt"] or 0
                uptime_pct = round((online_pings / total_pings) * 100, 1)

            # Total events logged
            cursor.execute("SELECT COUNT(*) AS cnt FROM network_events")
            ev_cnt_row = cursor.fetchone()
            if ev_cnt_row:
                total_events = ev_cnt_row["cnt"]

            # Current monitored devices count
            cursor.execute("SELECT COUNT(*) AS cnt FROM devices WHERE network_id = ? AND is_monitored = 1", (current_net_id,))
            dev_cnt_row = cursor.fetchone()
            if dev_cnt_row:
                monitored_devices_count = dev_cnt_row["cnt"]
    except Exception as e:
        logger.error("Error fetching history: %s", e)

    return render_template(
        "history.html",
        active_page="history",
        events=events,
        statuses=statuses,
        sessions=sessions,
        total_pings=total_pings,
        uptime_pct=uptime_pct,
        total_events=total_events,
        monitored_devices_count=monitored_devices_count,
        current_network=net_info
    )


# ============================================================================
# LIVE NETWORK & MONITORING APIS
# ============================================================================

@app.route("/api/status", methods=["GET"])
def api_status():
    """Returns operational status and Digital Twin graph summary."""
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    stale_count = 0
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM devices WHERE (network_id != ? OR network_id IS NULL)", (current_net_id,))
            row = cursor.fetchone()
            if row:
                stale_count = row["cnt"]
    except Exception:
        pass

    now_utc = utc_now()
    return jsonify({
        "status": "operational",
        "mode": "LIVE_MONITORING",
        "timestamp": utc_iso(now_utc),
        "timestamp_utc": utc_iso(now_utc),
        "timestamp_local": format_local_time(now_utc),
        "timestamp_local_iso": format_local_iso(now_utc),
        "current_network": net_info,
        "gateway": {
            "id": HOTSPOT_GATEWAY_ID,
            "name": HOTSPOT_GATEWAY_NAME,
            "ip": net_info.get("gateway")
        },
        "summary": digital_twin.get_summary(stale_count=stale_count)
    })


@app.route("/api/network/info", methods=["GET"])
@app.route("/api/network/current", methods=["GET"])
def api_network_info():
    """
    Returns the active local network configuration (interface, local IP, gateway, subnet, and network_id)
    of the machine running the Flask server.
    """
    net_info, has_changed = check_network_change()
    return jsonify({
        "success": True,
        "network": net_info
    })


@app.route("/api/network/refresh", methods=["POST"])
def api_network_refresh():
    """
    Forces a fresh check of the active local network configuration, synchronizes
    network sessions in SQLite, and updates the central gateway in the Digital Twin.
    """
    net_info, has_changed = check_network_change()
    if net_info.get("gateway"):
        digital_twin.update_gateway_ip(net_info["gateway"])
    session_id = sync_network_session(net_info)
    return jsonify({
        "success": True,
        "has_changed": has_changed,
        "session_id": session_id,
        "network": net_info
    })


@app.route("/api/digital-twin", methods=["GET"])
def api_digital_twin():
    """Returns high-level metadata, architectural node states, and summary of the live Digital Twin."""
    net_info = get_current_network_info()
    return jsonify(digital_twin.to_api_dict(net_info))


@app.route("/api/digital-twin/topology", methods=["GET"])
@app.route("/api/network", methods=["GET"])
def api_network():
    """Returns Cytoscape-formatted graph elements for live topology."""
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    stale_count = 0
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM devices WHERE (network_id != ? OR network_id IS NULL)", (current_net_id,))
            row = cursor.fetchone()
            if row:
                stale_count = row["cnt"]
    except Exception:
        pass

    return jsonify({
        "mode": "LIVE_MONITORING",
        "elements": digital_twin.to_cytoscape_elements(),
        "summary": digital_twin.get_summary(stale_count=stale_count)
    })


@app.route("/api/telemetry", methods=["GET"])
def api_telemetry():
    """Returns historical telemetry entries filterable by device_id and network_id."""
    device_id = request.args.get("device_id")
    network_id = request.args.get("network_id")
    limit = request.args.get("limit", 50, type=int)

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT s.*, d.device_name
                FROM device_status s
                LEFT JOIN devices d ON s.device_id = d.device_id
                WHERE 1=1
            """
            params = []
            if device_id:
                query += " AND s.device_id = ?"
                params.append(device_id)
            if network_id:
                query += " AND s.network_id = ?"
                params.append(network_id)
            query += " ORDER BY s.id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            return jsonify([enrich_timestamp_dict(dict(r)) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/events", methods=["GET"])
def api_events():
    """Returns latest network events."""
    limit = request.args.get("limit", 20, type=int)
    event_type = request.args.get("type")
    device_id = request.args.get("device_id")
    events = get_recent_events(limit=limit, event_type=event_type, device_id=device_id)
    return jsonify(events)


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    """Returns historical network connection sessions."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM network_sessions ORDER BY session_id DESC LIMIT 50")
            rows = cursor.fetchall()
            return jsonify([enrich_session_dict(dict(r)) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/monitor/ping-device", methods=["GET"])
def api_ping_device():
    """Performs an immediate, on-demand ICMP ping against a specific IP."""
    ip = request.args.get("ip", "").strip()
    if not validate_ip(ip):
        return jsonify({"is_online": False, "latency": None, "error": "Invalid IPv4 address"}), 400

    is_online, latency, err = ping_host(ip, timeout_seconds=1)
    return jsonify({
        "ip": ip,
        "is_online": is_online,
        "latency": latency,
        "error": err
    })


@app.route("/api/monitoring/status", methods=["GET"])
def api_monitoring_status():
    """Returns live monitoring status and graph summary."""
    return api_status()


@app.route("/api/monitoring/ping-all", methods=["POST"])
@app.route("/api/monitor/ping-all", methods=["POST"])
def api_ping_all_visible_devices():
    """
    MANUAL OPERATION: Pings all currently visible devices with exactly 5 ICMP echo requests each.
    Calculates packets_sent, packets_received, packet_loss, min_latency, average_latency, max_latency.
    If 0 packets received: latency values are N/A (never 0 ms).
    Saves telemetry in SQLite device_status for each tested device.
    """
    global _last_discovered_cache
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    gateway_ip = net_info.get("gateway")

    req_data = request.get_json(silent=True) or {}
    targets = req_data.get("targets")

    if not targets:
        if _last_discovered_cache:
            targets = [d["ip_address"] for d in _last_discovered_cache]
        else:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ip_address FROM devices WHERE network_id = ?", (current_net_id,))
                rows = cursor.fetchall()
                targets = [r["ip_address"] for r in rows]
            if gateway_ip and gateway_ip not in targets:
                targets.insert(0, gateway_ip)

    unique_targets = []
    seen = set()
    for t in targets:
        if t and t not in seen and validate_ip(t):
            seen.add(t)
            unique_targets.append(t)

    active_session = get_active_session()
    session_id = active_session.get("session_id") if active_session else None

    def _probe_single(ip):
        probes = ping_device_probes(ip, count=PING_COUNT, timeout_seconds=PING_TIMEOUT)
        return ip, probes

    probe_results = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(_probe_single, ip): ip for ip in unique_targets}
        for f in as_completed(futures):
            ip, probes = f.result()
            probe_results[ip] = probes

    table_rows = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for ip in unique_targets:
            probes = probe_results.get(ip, {})
            is_online = probes.get("status") == "ONLINE"
            sent = probes.get("packets_sent", PING_COUNT)
            received = probes.get("packets_received", 0)
            loss = probes.get("packet_loss", 100.0)
            avg_lat = probes.get("average_latency")
            min_lat = probes.get("min_latency")
            max_lat = probes.get("max_latency")

            cursor.execute("SELECT device_id, device_name FROM devices WHERE ip_address = ?", (ip,))
            dev_row = cursor.fetchone()
            if dev_row:
                dev_id = dev_row["device_id"]
                dev_name = dev_row["device_name"]
                cursor.execute(
                    """
                    UPDATE devices
                    SET status = ?,
                        last_seen = CASE WHEN ? = 'ONLINE' THEN CURRENT_TIMESTAMP ELSE last_seen END
                    WHERE device_id = ?
                    """,
                    (probes.get("status"), probes.get("status"), dev_id)
                )
            else:
                if ip == gateway_ip:
                    dev_id = HOTSPOT_GATEWAY_ID
                    dev_name = HOTSPOT_GATEWAY_NAME
                    dev_type = "Gateway"
                else:
                    dev_id = f"DEV_{ip.replace('.', '_')}"
                    dev_name = f"Host-{ip.split('.')[-1]}"
                    dev_type = "Computer"

                cursor.execute(
                    """
                    INSERT INTO devices (
                        device_id, device_name, ip_address, device_type, network_id,
                        status, is_monitored, source, authorization_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, 'DISCOVERY', 'AUTHORIZED')
                    ON CONFLICT(device_id) DO UPDATE SET
                        status = excluded.status,
                        last_seen = CURRENT_TIMESTAMP
                    """,
                    (dev_id, dev_name, ip, dev_type, current_net_id, probes.get("status", "UNREACHABLE"))
                )

            digital_twin.update_device_status(dev_id, probes.get("status", "UNREACHABLE"), avg_lat)

            cursor.execute(
                """
                INSERT INTO device_status (
                    device_id, status, latency, min_latency, max_latency, last_latency,
                    packets_sent, packets_received, packet_loss, network_id, session_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (dev_id, probes.get("status", "UNREACHABLE"), avg_lat, min_lat, max_lat,
                 probes.get("last_latency"), sent, received, loss, current_net_id, session_id)
            )

            table_rows.append({
                "device": ip,
                "device_id": dev_id,
                "ip_address": ip,
                "hostname": dev_name,
                "status": probes.get("status", "UNREACHABLE"),
                "sent": sent,
                "received": received,
                "loss": f"{loss}%",
                "packet_loss": loss,
                "avg": f"{avg_lat} ms" if avg_lat is not None else "N/A",
                "min": f"{min_lat} ms" if min_lat is not None else "N/A",
                "max": f"{max_lat} ms" if max_lat is not None else "N/A",
                "average_latency": avg_lat,
                "min_latency": min_lat,
                "max_latency": max_lat,
                "timestamp": probes.get("timestamp")
            })

        conn.commit()

    return jsonify({
        "status": "success",
        "success": True,
        "count": len(table_rows),
        "results": table_rows,
        "summary": digital_twin.get_summary()
    })


@app.route("/api/devices/<device_id>/test", methods=["POST"])
@app.route("/api/monitor/<device_id>/test", methods=["POST"])
def api_test_device_probes(device_id):
    """
    Executes a controlled series of ICMP echo probes (5 packets) against a registered device
    to calculate packet loss, latency, and response statistics, updating the Digital Twin.
    """
    device_id = device_id.strip()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
            dev = cursor.fetchone()
            if not dev:
                net_info = get_current_network_info()
                if device_id == HOTSPOT_GATEWAY_ID and net_info.get("gateway"):
                    dev = {
                        "device_id": HOTSPOT_GATEWAY_ID,
                        "device_name": HOTSPOT_GATEWAY_NAME,
                        "ip_address": net_info.get("gateway"),
                        "network_id": net_info.get("network_id")
                    }
                else:
                    return jsonify({"error": f"Device '{device_id}' not found."}), 404

            ip = dev["ip_address"]
            name = dev["device_name"]
            net_id = dev["network_id"]

            active_session = get_active_session()
            session_id = active_session.get("session_id") if active_session else None

            # Run 5 controlled probes
            probes = ping_device_probes(ip, count=PING_COUNT, timeout_seconds=PING_TIMEOUT)

            # Update Digital Twin in-memory graph
            digital_twin.update_device_status(device_id, probes["status"], probes["average_latency"])

            # Update device record in devices table
            cursor.execute(
                """
                UPDATE devices
                SET status = ?,
                    last_seen = CASE WHEN ? = 'ONLINE' THEN CURRENT_TIMESTAMP ELSE last_seen END
                WHERE device_id = ?
                """,
                (probes["status"], probes["status"], device_id)
            )

            # Store in device_status
            cursor.execute(
                """
                INSERT INTO device_status (
                    device_id, status, latency, min_latency, max_latency, last_latency,
                    packets_sent, packets_received, packet_loss, network_id, session_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, probes["status"], probes["average_latency"], probes.get("min_latency"),
                 probes.get("max_latency"), probes["last_latency"], probes["packets_sent"],
                 probes["packets_received"], probes["packet_loss"], net_id, session_id)
            )

            if probes["status"] == "UNREACHABLE":
                log_event(device_id, EVENT_DEVICE_UNREACHABLE,
                          f"Manual test for {name} ({device_id} @ {ip}): UNREACHABLE / {probes['packet_loss']}% packet loss", conn=conn)
            elif probes["average_latency"] and probes["average_latency"] > LATENCY_ALERT_THRESHOLD:
                log_event(device_id, EVENT_LATENCY_THRESHOLD,
                          f"Manual test for {name} ({device_id}): High latency {probes['average_latency']} ms", conn=conn)

            conn.commit()

            return jsonify({
                "success": True,
                "device_id": device_id,
                "device_name": name,
                "ip_address": ip,
                "status": probes["status"],
                "packets_sent": probes["packets_sent"],
                "packets_received": probes["packets_received"],
                "packet_loss": probes["packet_loss"],
                "loss": f"{probes['packet_loss']}%",
                "average_latency": probes["average_latency"],
                "avg": f"{probes['average_latency']} ms" if probes["average_latency"] is not None else "N/A",
                "min_latency": probes.get("min_latency"),
                "min": f"{probes.get('min_latency')} ms" if probes.get("min_latency") is not None else "N/A",
                "max_latency": probes.get("max_latency"),
                "max": f"{probes.get('max_latency')} ms" if probes.get("max_latency") is not None else "N/A",
                "last_latency": probes["last_latency"],
                "timestamp": probes["timestamp"],
                "error": probes["error"],
                "summary": digital_twin.get_summary()
            })
    except Exception as e:
        logger.error("Error running manual probe for %s: %s", device_id, e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/discovery/status", methods=["GET"])
def api_discovery_status():
    """Returns current live progress of background or active discovery."""
    return jsonify(get_discovery_progress())


@app.route("/api/discovery", methods=["GET"])
@app.route("/api/discovery/devices", methods=["GET"])
def api_discovery_devices():
    """Returns the list of visible devices from latest discovery or ARP inspection."""
    global _last_discovered_cache
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    target_net = net_info.get("network")

    if _last_discovered_cache:
        monitored_ips = set()
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ip_address FROM devices WHERE network_id = ? AND is_monitored = 1", (current_net_id,))
                monitored_ips = {row["ip_address"] for row in cursor.fetchall()}
        except Exception:
            pass

        for d in _last_discovered_cache:
            d["is_monitored"] = d["ip_address"] in monitored_ips

        return jsonify({
            "success": True,
            "devices": _last_discovered_cache,
            "count": len(_last_discovered_cache),
            "target_network": target_net
        })

    success, devices, err = discover_local_devices(target_network=target_net)
    monitored_ips = set()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ip_address FROM devices WHERE network_id = ? AND is_monitored = 1", (current_net_id,))
            monitored_ips = {row["ip_address"] for row in cursor.fetchall()}
    except Exception:
        pass

    annotated = []
    for d in devices:
        annotated.append({
            "device_id": f"DEV_{d['ip_address'].replace('.', '_')}",
            "ip_address": d["ip_address"],
            "mac_address": d["mac_address"],
            "hostname": "Hotspot Gateway" if d["ip_address"] == net_info.get("gateway") else f"Host-{d['ip_address'].split('.')[-1]}",
            "device_type": "Gateway" if d["ip_address"] == net_info.get("gateway") else "Computer",
            "source": d.get("source", "ARP"),
            "discovery_latency": None,
            "network_id": current_net_id,
            "status": "VISIBLE",
            "visibility": "VISIBLE",
            "is_monitored": d["ip_address"] in monitored_ips
        })
    _last_discovered_cache = annotated
    return jsonify({
        "success": success,
        "devices": annotated,
        "count": len(annotated),
        "target_network": target_net,
        "error": err
    })


@app.route("/api/discovery/start", methods=["POST"])
@app.route("/api/discovery/active", methods=["POST"])
def api_discovery_start():
    """
    Triggers controlled active ICMP discovery across all dynamically calculated candidates
    (e.g. 253 for a /24 subnet, excluding host IP and broadcast), merges with ARP cache,
    and updates discovery cache.
    """
    global _last_discovered_cache
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")

    result = discover_devices_active(
        target_network=net_info.get("network"),
        gateway=net_info.get("gateway"),
        local_ip=net_info.get("local_ip")
    )

    monitored_ips = set()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ip_address FROM devices WHERE network_id = ? AND is_monitored = 1", (current_net_id,))
            monitored_ips = {row["ip_address"] for row in cursor.fetchall()}
    except Exception as e:
        logger.error("Error querying monitored devices: %s", e)

    for d in result.get("devices", []):
        d["is_monitored"] = d["ip_address"] in monitored_ips

    _last_discovered_cache = result.get("devices", [])

    if result.get("success") and result.get("count", 0) > 0:
        log_event("SYSTEM", EVENT_DEVICE_DISCOVERED,
                  f"Discovered {result['count']} visible device(s) across {result.get('scanned_hosts', 0)} candidates on {net_info.get('network')}")

    return jsonify(result)


# ============================================================================
# DEVICE MANAGEMENT APIS
# ============================================================================

@app.route("/api/devices", methods=["GET", "POST"])
def api_devices():
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")

    if request.method == "GET":
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices ORDER BY id ASC")
            all_devs = [dict(r) for r in cursor.fetchall()]
            current_devs = [d for d in all_devs if d.get("network_id") == current_net_id]
            historical_devs = [d for d in all_devs if d.get("network_id") != current_net_id]
            return jsonify({
                "current_network_id": current_net_id,
                "current_devices": current_devs,
                "historical_devices": historical_devs,
                "devices": all_devs
            })

    # POST: Add new device
    data = request.get_json() or {}
    dev_id = data.get("device_id", "").strip().upper()
    name = data.get("device_name", "").strip()
    ip = data.get("ip_address", "").strip()
    dev_type = data.get("device_type", "Computer").strip()
    desc = data.get("description", "").strip()
    mac = data.get("mac_address", "").strip() or None
    net_id = data.get("network_id", "").strip() or current_net_id
    is_monitored = int(data.get("is_monitored", 1))
    auth_status = data.get("authorization_status", "AUTHORIZED")
    source = data.get("source", "MANUAL")

    if not dev_id or not name or not ip:
        return jsonify({"error": "Device ID, Name, and IP Address are required."}), 400

    if not validate_ip(ip):
        return jsonify({"error": f"'{ip}' is not a valid IPv4 address."}), 400

    if dev_id == HOTSPOT_GATEWAY_ID:
        return jsonify({"error": "Cannot use reserved Gateway Device ID."}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO devices (
                    device_id, device_name, ip_address, device_type, description,
                    network_id, mac_address, status, is_monitored, authorization_status, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?, ?)
                """,
                (dev_id, name, ip, dev_type, desc, net_id, mac, is_monitored, auth_status, source)
            )
            # Log registration event
            cursor.execute(
                """
                INSERT INTO network_events (device_id, event_type, message)
                VALUES (?, ?, ?)
                """,
                (dev_id, "SYSTEM", f"New device authorized: {name} ({dev_id} @ {ip}) on {net_id}")
            )
            conn.commit()

        # Update in-memory Digital Twin graph if on current network and monitored
        if net_id == current_net_id and is_monitored == 1:
            digital_twin.add_device(dev_id, name, ip, dev_type, desc)
            simulator.clone_from_digital_twin(digital_twin)

        return jsonify({"status": "created", "device_id": dev_id}), 201
    except Exception as e:
        logger.error("Error creating device: %s", e)
        return jsonify({"error": "A device with this ID or IP address already exists or database error occurred."}), 400


@app.route("/api/devices/<device_id>/monitor", methods=["POST"])
def api_device_monitor(device_id):
    """Adds a device to the live monitoring set."""
    device_id = device_id.strip()
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
            dev = cursor.fetchone()

            if not dev:
                disc = next((d for d in _last_discovered_cache if d.get("device_id") == device_id or d.get("ip_address") == device_id), None)
                if disc:
                    cursor.execute(
                        """
                        INSERT INTO devices (
                            device_id, device_name, ip_address, device_type, description,
                            network_id, mac_address, status, is_monitored, authorization_status, source
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', 1, 'AUTHORIZED', ?)
                        """,
                        (disc.get("device_id", device_id), disc.get("hostname", device_id), disc["ip_address"],
                         disc.get("device_type", "Computer"), "Discovered Device", current_net_id,
                         disc.get("mac_address"), disc.get("source", "ICMP+ARP"))
                    )
                    conn.commit()
                    cursor.execute("SELECT * FROM devices WHERE device_id = ?", (disc.get("device_id", device_id),))
                    dev = cursor.fetchone()
                else:
                    return jsonify({"error": f"Device '{device_id}' not found."}), 404

            actual_id = dev["device_id"]
            cursor.execute(
                "UPDATE devices SET is_monitored = 1, network_id = ? WHERE device_id = ?",
                (current_net_id, actual_id)
            )
            log_event(actual_id, EVENT_DEVICE_ADDED_TO_MONITORING,
                      f"Device {dev['device_name']} ({actual_id} @ {dev['ip_address']}) added to live monitoring on {current_net_id}", conn=conn)
            conn.commit()

        digital_twin.add_device(dev["device_id"], dev["device_name"], dev["ip_address"], dev["device_type"], dev.get("description", ""))
        simulator.clone_from_digital_twin(digital_twin)
        return jsonify({"success": True, "device_id": actual_id, "is_monitored": 1, "status": "MONITORED"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/devices/<device_id>/unmonitor", methods=["POST"])
def api_device_unmonitor(device_id):
    """Stops continuous monitoring for a device without deleting historical telemetry."""
    device_id = device_id.strip()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE devices SET is_monitored = 0 WHERE device_id = ?", (device_id,))
            conn.commit()

        digital_twin.remove_device(device_id)
        simulator.clone_from_digital_twin(digital_twin)
        return jsonify({"success": True, "device_id": device_id, "is_monitored": 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/devices/<device_id>/toggle-monitoring", methods=["POST"])
def api_toggle_device_monitoring(device_id):
    """Toggles whether a device is actively monitored in the live Digital Twin."""
    device_id = device_id.strip()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
            dev = cursor.fetchone()
            if not dev:
                return jsonify({"error": f"Device '{device_id}' not found."}), 404

            new_monitored = 0 if dev["is_monitored"] else 1
            cursor.execute("UPDATE devices SET is_monitored = ? WHERE device_id = ?", (new_monitored, device_id))
            conn.commit()

        net_info = get_current_network_info()
        current_net_id = net_info.get("network_id")

        if new_monitored == 1 and dev["network_id"] == current_net_id:
            digital_twin.add_device(dev["device_id"], dev["device_name"], dev["ip_address"], dev["device_type"], dev["description"])
        else:
            digital_twin.remove_device(device_id)

        simulator.clone_from_digital_twin(digital_twin)
        return jsonify({"success": True, "device_id": device_id, "is_monitored": new_monitored})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/devices/<device_id>", methods=["PUT", "DELETE"])
def api_device_detail(device_id):
    device_id = device_id.strip()

    if request.method == "DELETE":
        if device_id == HOTSPOT_GATEWAY_ID:
            return jsonify({"error": "Cannot delete central Gateway node."}), 400

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
                cursor.execute(
                    """
                    INSERT INTO network_events (device_id, event_type, message)
                    VALUES (?, ?, ?)
                    """,
                    (device_id, "SYSTEM", f"Device removed from network: {device_id}")
                )
                conn.commit()

            # Remove from Digital Twin and update simulation
            digital_twin.remove_device(device_id)
            simulator.clone_from_digital_twin(digital_twin)

            return jsonify({"status": "deleted", "device_id": device_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # PUT: Edit device
    data = request.get_json() or {}
    name = data.get("device_name", "").strip()
    ip = data.get("ip_address", "").strip()
    dev_type = data.get("device_type", "Computer").strip()
    desc = data.get("description", "").strip()

    if not validate_ip(ip):
        return jsonify({"error": f"'{ip}' is not a valid IPv4 address."}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE devices
                SET device_name = ?, ip_address = ?, device_type = ?, description = ?
                WHERE device_id = ?
                """,
                (name, ip, dev_type, desc, device_id)
            )
            conn.commit()

        digital_twin.add_device(device_id, name, ip, dev_type, desc)
        simulator.clone_from_digital_twin(digital_twin)
        return jsonify({"status": "updated", "device_id": device_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/devices/<device_id>/adopt", methods=["POST"])
def api_adopt_device(device_id):
    """Adopts a historical/stale device into the current network session."""
    net_info = get_current_network_info()
    current_net_id = net_info.get("network_id")
    device_id = device_id.strip()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE devices
                SET network_id = ?, status = 'DISCOVERED', is_monitored = 1
                WHERE device_id = ?
                """,
                (current_net_id, device_id)
            )
            cursor.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
            dev = cursor.fetchone()
            if not dev:
                return jsonify({"error": f"Device '{device_id}' not found."}), 404

            cursor.execute(
                """
                INSERT INTO network_events (device_id, event_type, message)
                VALUES (?, ?, ?)
                """,
                (device_id, "SYSTEM", f"Device {device_id} adopted into active network {current_net_id}")
            )
            conn.commit()

        digital_twin.add_device(dev["device_id"], dev["device_name"], dev["ip_address"], dev["device_type"], dev["description"])
        simulator.clone_from_digital_twin(digital_twin)
        return jsonify({"success": True, "device_id": device_id, "network_id": current_net_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# WHAT-IF SIMULATION APIS
# ============================================================================

@app.route("/api/simulation/state", methods=["GET"])
@app.route("/api/simulation/impact", methods=["GET"])
def api_sim_state():
    """Returns the simulated graph elements and impact analysis."""
    net_info = get_current_network_info()
    # If sync=1 or if simulator is empty but digital_twin has nodes and no failures injected, auto-sync
    if request.args.get("sync") == "1" or (
        simulator.sim_graph.number_of_nodes() <= 1 and 
        digital_twin.graph.number_of_nodes() > 1 and 
        not simulator.failed_nodes and not simulator.failed_links
    ):
        simulator.clone_from_digital_twin(digital_twin, net_info)

    impact = simulator.analyze_impact()
    return jsonify({
        "mode": "WHAT_IF_SIMULATION",
        "topology_type": simulator.topology_type.upper(),
        "has_live_devices": impact.get("has_live_devices", True),
        "warning": impact.get("warning"),
        "elements": simulator.to_cytoscape_elements(),
        "impact": impact
    })


@app.route("/api/simulation/initialize", methods=["POST", "GET"])
@app.route("/api/simulation/init", methods=["POST"])
@app.route("/api/simulation/sync", methods=["POST"])
def api_sim_initialize():
    """Initializes or resets the simulation graph from the live Digital Twin."""
    net_info = get_current_network_info()
    simulator.clone_from_digital_twin(digital_twin, net_info)
    log_event("SIMULATION", EVENT_SIMULATION_RESET, "What-If simulation initialized from current live Digital Twin.")
    impact = simulator.analyze_impact()
    return jsonify({
        "status": "success",
        "message": "Simulation initialized from live Digital Twin.",
        "topology_type": simulator.topology_type.upper(),
        "has_live_devices": impact.get("has_live_devices", True),
        "warning": impact.get("warning"),
        "elements": simulator.to_cytoscape_elements(),
        "impact": impact
    })


@app.route("/api/simulation/device-failure", methods=["POST"])
@app.route("/api/simulation/fail-device", methods=["POST"])
def api_sim_fail_device():
    """Simulates a hypothetical device outage."""
    data = request.get_json() or {}
    dev_id = data.get("device_id")
    success, msg = simulator.simulate_device_failure(dev_id)
    if not success:
        return jsonify({"error": msg}), 400

    # Record event in network_events table for audit demonstration
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO network_events (device_id, event_type, message) VALUES (?, ?, ?)",
                (dev_id, "SIMULATION", f"[WHAT-IF] Simulated failure of device {dev_id}")
            )
            conn.commit()
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "message": msg,
        "impact": simulator.analyze_impact()
    })


@app.route("/api/simulation/recovery", methods=["POST"])
@app.route("/api/simulation/device-recovery", methods=["POST"])
@app.route("/api/simulation/recover-device", methods=["POST"])
def api_sim_recover_device():
    """Simulates hypothetical device recovery."""
    data = request.get_json() or {}
    dev_id = data.get("device_id")
    success, msg = simulator.simulate_device_recovery(dev_id)
    if not success:
        return jsonify({"error": msg}), 400

    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO network_events (device_id, event_type, message) VALUES (?, ?, ?)",
                (dev_id, "SIMULATION", f"[WHAT-IF] Simulated recovery of device {dev_id}")
            )
            conn.commit()
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "message": msg,
        "impact": simulator.analyze_impact()
    })


@app.route("/api/simulation/link-failure", methods=["POST"])
@app.route("/api/simulation/fail-link", methods=["POST"])
def api_sim_fail_link():
    """Simulates hypothetical severing of a network link."""
    data = request.get_json() or {}
    source = data.get("source")
    target = data.get("target")
    if not source and "link" in data and "|" in str(data["link"]):
        source, target = str(data["link"]).split("|", 1)

    if not source or not target:
        return jsonify({"error": "source and target (or link formatted as 'u|v') required"}), 400

    success, msg = simulator.simulate_link_failure(source, target)
    if not success:
        return jsonify({"error": msg}), 400

    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO network_events (device_id, event_type, message) VALUES (?, ?, ?)",
                (f"{source}-{target}", "SIMULATION", f"[WHAT-IF] Simulated failure of link {source} <-> {target}")
            )
            conn.commit()
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "message": msg,
        "impact": simulator.analyze_impact()
    })


@app.route("/api/simulation/link-recovery", methods=["POST"])
@app.route("/api/simulation/recover-link", methods=["POST"])
def api_sim_recover_link():
    """Simulates hypothetical recovery of a severed link."""
    data = request.get_json() or {}
    source = data.get("source")
    target = data.get("target")
    if not source and "link" in data and "|" in str(data["link"]):
        source, target = str(data["link"]).split("|", 1)

    if not source or not target:
        return jsonify({"error": "source and target (or link formatted as 'u|v') required"}), 400

    success, msg = simulator.simulate_link_recovery(source, target)
    if not success:
        return jsonify({"error": msg}), 400

    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO network_events (device_id, event_type, message) VALUES (?, ?, ?)",
                (f"{source}-{target}", "SIMULATION", f"[WHAT-IF] Simulated recovery of link {source} <-> {target}")
            )
            conn.commit()
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "message": msg,
        "impact": simulator.analyze_impact()
    })


@app.route("/api/simulation/topology", methods=["POST"])
def api_sim_topology():
    """Reconfigures the simulated topology (Star, Ring, Mesh)."""
    data = request.get_json() or {}
    topo = data.get("topology", "star")
    success, msg = simulator.set_simulation_topology(topo)
    if not success:
        return jsonify({"error": msg}), 400

    return jsonify({
        "status": "success",
        "message": msg,
        "impact": simulator.analyze_impact()
    })


@app.route("/api/simulation/reset", methods=["POST"])
def api_sim_reset():
    """Resets the simulation by re-cloning the live Digital Twin state."""
    net_info = get_current_network_info()
    simulator.clone_from_digital_twin(digital_twin, net_info)
    log_event("SYSTEM", EVENT_SIMULATION_RESET, "What-If simulation reset to current live Digital Twin state.")
    return jsonify({
        "status": "success",
        "message": "Simulation reset to current live Digital Twin state.",
        "impact": simulator.analyze_impact()
    })


@app.route("/api/simulation/path", methods=["GET", "POST"])
def api_sim_path():
    """Computes the shortest operational path between two simulated nodes."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        source = data.get("source")
        target = data.get("target") or data.get("destination")
    else:
        source = request.args.get("source")
        target = request.args.get("target") or request.args.get("destination")

    if not source or not target:
        return jsonify({"error": "source and target/destination parameters required"}), 400

    result = simulator.get_path(source, target)
    return jsonify(result)


@app.route("/api/simulation/distance-vector", methods=["GET"])
def api_sim_distance_vector():
    """Computes Distance Vector routing table for a simulated node."""
    node = request.args.get("node")
    if not node:
        return jsonify({"error": "node parameter required"}), 400

    table = simulator.get_distance_vector(node)
    return jsonify({"node": node, "table": table})


if __name__ == "__main__":
    logger.info("Starting Network Digital Twin on http://%s:%d", HOST, PORT)
    app.run(host=HOST, port=PORT, debug=DEBUG)
