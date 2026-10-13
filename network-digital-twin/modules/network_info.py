"""
Dynamic Network Detection Module
--------------------------------
Determines the active local network configuration on the machine running Flask
using native Windows networking utilities (ipconfig / route).
Computes a unique network_id and detects when the laptop switches between Wi-Fi networks/hotspots.
"""

import re
import ipaddress
import logging
import platform
import subprocess

from database import get_db_connection

logger = logging.getLogger(__name__)

# Cached state for network transition detection
_last_known_network_id = None

def parse_ipconfig_output(ipconfig_text):
    """
    Parses output from Windows 'ipconfig' command into a structured network dictionary.
    Identifies the active adapter connected to a gateway.
    
    Args:
        ipconfig_text (str): Raw stdout text from ipconfig.
        
    Returns:
        dict: Active network details or disconnected state.
    """
    if not ipconfig_text or not isinstance(ipconfig_text, str):
        return {
            "interface": "None",
            "local_ip": None,
            "subnet_mask": None,
            "gateway": None,
            "network": None,
            "network_id": "DISCONNECTED",
            "status": "DISCONNECTED"
        }

    current_adapter = None
    adapter_data = {}

    for line in ipconfig_text.splitlines():
        line_str = line.strip()
        if not line_str:
            continue

        # Match adapter headers: "Wireless LAN adapter Wi-Fi:", "Ethernet adapter Ethernet:", etc.
        adapter_match = re.match(r'^(?:Wireless LAN adapter|Ethernet adapter|Unknown adapter)\s+([^:]+):', line_str)
        if adapter_match:
            current_adapter = adapter_match.group(1).strip()
            adapter_data[current_adapter] = {}
            continue

        if current_adapter:
            # Match IPv4 Address (supports "(Preferred)" suffix on Windows)
            ip_m = re.search(r'IPv4 Address[.\s]+:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)', line_str, re.IGNORECASE)
            if ip_m:
                adapter_data[current_adapter]['ip'] = ip_m.group(1)

            # Match Subnet Mask
            mask_m = re.search(r'Subnet Mask[.\s]+:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)', line_str, re.IGNORECASE)
            if mask_m:
                adapter_data[current_adapter]['mask'] = mask_m.group(1)

            # Match Default Gateway (skips blank or empty gateway lines)
            gw_m = re.search(r'Default Gateway[.\s]+:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)', line_str, re.IGNORECASE)
            if gw_m:
                adapter_data[current_adapter]['gw'] = gw_m.group(1)

    # Find the active adapter with an IP address and a Default Gateway
    for name, data in adapter_data.items():
        ip = data.get("ip")
        gw = data.get("gw")
        mask = data.get("mask", "255.255.255.0")

        if ip and gw and ip != "0.0.0.0" and gw != "0.0.0.0":
            try:
                net_obj = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                network_cidr = str(net_obj)
            except ValueError:
                network_cidr = f"{ip}/24"

            network_id = f"{name}|{network_cidr}|{gw}"
            return {
                "interface": name,
                "local_ip": ip,
                "subnet_mask": mask,
                "gateway": gw,
                "network": network_cidr,
                "network_id": network_id,
                "status": "CONNECTED"
            }

    # Fallback if an interface has an IP but no gateway (e.g. ad-hoc / isolated link)
    for name, data in adapter_data.items():
        ip = data.get("ip")
        if ip and ip != "0.0.0.0" and not ip.startswith("169.254."):
            mask = data.get("mask", "255.255.255.0")
            try:
                net_obj = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                network_cidr = str(net_obj)
            except ValueError:
                network_cidr = f"{ip}/24"
            network_id = f"{name}|{network_cidr}|none"
            return {
                "interface": name,
                "local_ip": ip,
                "subnet_mask": mask,
                "gateway": None,
                "network": network_cidr,
                "network_id": network_id,
                "status": "CONNECTED_NO_GATEWAY"
            }

    return {
        "interface": "None",
        "local_ip": None,
        "subnet_mask": None,
        "gateway": None,
        "network": None,
        "network_id": "DISCONNECTED",
        "status": "DISCONNECTED"
    }


def get_current_network_info(timeout_seconds=3):
    """
    Executes 'ipconfig' on Windows and parses the current network configuration.
    Fails safely without throwing unhandled exceptions.
    
    Returns:
        dict: Active network details including interface, local_ip, gateway, network, and network_id.
    """
    try:
        # Use argument list (NEVER shell=True)
        res = subprocess.run(
            ["ipconfig"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds
        )
        return parse_ipconfig_output(res.stdout)
    except subprocess.TimeoutExpired:
        logger.error("ipconfig command timed out.")
        return {
            "interface": "Unknown",
            "local_ip": None,
            "subnet_mask": None,
            "gateway": None,
            "network": None,
            "network_id": "ERROR_TIMEOUT",
            "status": "TIMEOUT"
        }
    except Exception as e:
        logger.error("Error reading network info: %s", e)
        return {
            "interface": "Unknown",
            "local_ip": None,
            "subnet_mask": None,
            "gateway": None,
            "network": None,
            "network_id": "ERROR",
            "status": "ERROR"
        }


def check_network_change():
    """
    Checks if the active local network has changed since the last check.
    
    Returns:
        tuple: (current_network_dict, has_changed: bool)
    """
    global _last_known_network_id
    current_info = get_current_network_info()
    current_id = current_info.get("network_id")

    has_changed = False
    if _last_known_network_id is not None and current_id != _last_known_network_id:
        has_changed = True
        logger.info("Network change detected! Previous: %s -> Current: %s", _last_known_network_id, current_id)

    _last_known_network_id = current_id
    current_info["network_changed"] = has_changed
    return current_info, has_changed


def sync_network_session(net_info):
    """
    Synchronizes active network connection with SQLite sessions.
    If the network changed or no active session exists:
    - Closes previous session
    - Opens new session in network_sessions
    - Marks devices from older networks as STALE
    
    Returns:
        int: active session_id
    """
    current_net_id = net_info.get("network_id")
    if not current_net_id or current_net_id == "DISCONNECTED":
        return None

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check for active session
            cursor.execute(
                "SELECT session_id, network_id FROM network_sessions WHERE is_active = 1 ORDER BY session_id DESC LIMIT 1"
            )
            active = cursor.fetchone()
            
            if active and active["network_id"] == current_net_id:
                return active["session_id"]
                
            # If active session was for a different network, close it
            if active:
                cursor.execute(
                    "UPDATE network_sessions SET is_active = 0, end_time = CURRENT_TIMESTAMP WHERE session_id = ?",
                    (active["session_id"],)
                )
                logger.info("Closed previous network session %d (%s)", active["session_id"], active["network_id"])

            # Create new session
            cursor.execute(
                """
                INSERT INTO network_sessions (network_id, interface, local_ip, gateway, network_cidr, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    current_net_id,
                    net_info.get("interface"),
                    net_info.get("local_ip"),
                    net_info.get("gateway"),
                    net_info.get("network")
                )
            )
            session_id = cursor.lastrowid
            
            # Subnet-aware device synchronization:
            # Check devices against current network CIDR
            curr_cidr = net_info.get("network")
            curr_net_obj = None
            if curr_cidr:
                try:
                    curr_net_obj = ipaddress.IPv4Network(curr_cidr, strict=False)
                except ValueError:
                    pass

            cursor.execute("SELECT id, device_id, ip_address, network_id, status FROM devices WHERE status != 'HISTORICAL'")
            dev_rows = cursor.fetchall()
            stale_count = 0
            for d in dev_rows:
                dev_ip = d["ip_address"]
                is_in_subnet = False
                if curr_net_obj and dev_ip:
                    try:
                        is_in_subnet = ipaddress.IPv4Address(dev_ip) in curr_net_obj
                    except ValueError:
                        is_in_subnet = False

                belongs_to_current = is_in_subnet if curr_net_obj else (d["network_id"] == current_net_id)
                if belongs_to_current:
                    if d["network_id"] != current_net_id:
                        cursor.execute("UPDATE devices SET network_id = ? WHERE id = ?", (current_net_id, d["id"]))
                    if d["status"] == "STALE":
                        cursor.execute("UPDATE devices SET status = 'UNKNOWN' WHERE id = ?", (d["id"],))
                else:
                    if d["status"] != "STALE":
                        cursor.execute("UPDATE devices SET status = 'STALE' WHERE id = ?", (d["id"],))
                        stale_count += 1

            if stale_count > 0:
                logger.info("Marked %d device(s) from previous networks as STALE.", stale_count)

            # Record event in network_events
            cursor.execute(
                """
                INSERT INTO network_events (device_id, event_type, message)
                VALUES (?, ?, ?)
                """,
                ("SYSTEM", "NETWORK_CHANGE", f"Network session #{session_id} active on {net_info.get('network')} (Gateway: {net_info.get('gateway')})")
            )

            # Record or update in monitored_networks
            cursor.execute(
                """
                INSERT INTO monitored_networks (network_id, interface, local_ip, gateway, network_cidr, last_seen)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(network_id) DO UPDATE SET
                    local_ip = excluded.local_ip,
                    gateway = excluded.gateway,
                    last_seen = CURRENT_TIMESTAMP
                """,
                (
                    current_net_id,
                    net_info.get("interface"),
                    net_info.get("local_ip"),
                    net_info.get("gateway"),
                    net_info.get("network")
                )
            )
            
            conn.commit()
            return session_id
    except Exception as e:
        logger.error("Error synchronizing network session: %s", e)
        return None


def get_active_session():
    """Returns the current active network session record as a dictionary or None."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM network_sessions WHERE is_active = 1 ORDER BY session_id DESC LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("Error retrieving active session: %s", e)
        return None


def get_current_authorized_devices(net_info):
    """
    Retrieves all authorized devices belonging to the current network session.
    A device belongs to the current network session if:
    1. Its network_id matches current_net_id, OR
    2. Its ip_address falls within the current network's CIDR subnet.
    AND its status is not 'HISTORICAL' and authorization_status is 'AUTHORIZED'.
    Excludes the gateway node ID ('GW_HOTSPOT').
    """
    current_net_id = net_info.get("network_id")
    if not current_net_id or current_net_id == "DISCONNECTED":
        return []

    curr_cidr = net_info.get("network")
    curr_net_obj = None
    if curr_cidr:
        try:
            curr_net_obj = ipaddress.IPv4Network(curr_cidr, strict=False)
        except ValueError:
            pass

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, device_id, device_name, ip_address, device_type, description,
                       network_id, mac_address, status, is_monitored, authorization_status, source, last_seen
                FROM devices
                WHERE status != 'HISTORICAL'
                  AND device_id != 'GW_HOTSPOT'
                  AND authorization_status = 'AUTHORIZED'
                ORDER BY id ASC
                """
            )
            rows = [dict(r) for r in cursor.fetchall()]

            authorized = []
            for d in rows:
                dev_ip = d.get("ip_address")
                is_in_subnet = False
                if curr_net_obj and dev_ip:
                    try:
                        is_in_subnet = ipaddress.IPv4Address(dev_ip) in curr_net_obj
                    except ValueError:
                        is_in_subnet = False

                belongs_to_current = is_in_subnet if curr_net_obj else (d.get("network_id") == current_net_id)
                if belongs_to_current:
                    if d.get("network_id") != current_net_id:
                        cursor.execute("UPDATE devices SET network_id = ? WHERE id = ?", (current_net_id, d["id"]))
                        d["network_id"] = current_net_id
                    authorized.append(d)

            conn.commit()
            return authorized
    except Exception as e:
        logger.error("Error retrieving authorized devices for current network: %s", e)
        return []


def ensure_authorized_test_devices(net_info):
    """
    Ensures that baseline authorized test devices (PC1, PC2, PC3)
    exist in the database for the active network session.
    If 0 authorized devices exist on the current subnet, provisions them with
    valid IP addresses calculated dynamically from the active subnet.
    """
    current_net_id = net_info.get("network_id")
    if not current_net_id or current_net_id == "DISCONNECTED":
        return []

    existing = get_current_authorized_devices(net_info)
    if existing:
        return existing

    # No authorized devices exist for current network - generate 3 baseline test devices
    curr_cidr = net_info.get("network")
    gateway_ip = net_info.get("gateway")
    local_ip = net_info.get("local_ip")

    candidate_ips = []
    if curr_cidr:
        try:
            net_obj = ipaddress.IPv4Network(curr_cidr, strict=False)
            gw_obj = ipaddress.IPv4Address(gateway_ip) if gateway_ip else None
            loc_obj = ipaddress.IPv4Address(local_ip) if local_ip else None
            base_int = int(net_obj.network_address)
            test_offsets = [101, 102, 103, 10, 11, 12, 20, 21, 22]
            for offset in test_offsets:
                candidate = ipaddress.IPv4Address(base_int + offset)
                if candidate in net_obj and candidate != gw_obj and candidate != loc_obj:
                    candidate_ips.append(str(candidate))
                    if len(candidate_ips) == 3:
                        break
        except Exception as e:
            logger.warning("Could not calculate subnet candidate IPs: %s", e)

    if len(candidate_ips) < 3:
        prefix = ".".join(gateway_ip.split(".")[:3]) if gateway_ip else "192.168.1"
        candidate_ips = [f"{prefix}.101", f"{prefix}.102", f"{prefix}.103"]

    sample_specs = [
        ("PC1", "Test Laptop 1 (Host PC)", candidate_ips[0], "Laptop", "Authorized baseline test device"),
        ("PC2", "Test Laptop 2 (Client PC)", candidate_ips[1], "Laptop", "Authorized baseline test device"),
        ("PC3", "Test Workstation 3", candidate_ips[2], "Computer", "Authorized baseline test device")
    ]

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for dev_id, name, ip, dev_type, desc in sample_specs:
                cursor.execute(
                    """
                    INSERT INTO devices (
                        device_id, device_name, ip_address, device_type, description,
                        network_id, status, is_monitored, authorization_status, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'UNKNOWN', 1, 'AUTHORIZED', 'MANUAL')
                    ON CONFLICT(device_id) DO UPDATE SET
                        device_name = excluded.device_name,
                        ip_address = excluded.ip_address,
                        network_id = excluded.network_id,
                        status = 'UNKNOWN',
                        is_monitored = 1,
                        authorization_status = 'AUTHORIZED'
                    """,
                    (dev_id, name, ip, dev_type, desc, current_net_id)
                )
            conn.commit()
            logger.info("Provisioned %d baseline authorized test devices for %s", len(sample_specs), current_net_id)
    except Exception as e:
        logger.error("Error provisioning baseline authorized test devices: %s", e)

    return get_current_authorized_devices(net_info)


