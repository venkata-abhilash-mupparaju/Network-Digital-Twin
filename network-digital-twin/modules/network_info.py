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
            
            # Mark all devices NOT belonging to this network as STALE
            cursor.execute(
                """
                UPDATE devices
                SET status = 'STALE'
                WHERE (network_id != ? OR network_id IS NULL)
                  AND status != 'HISTORICAL'
                """,
                (current_net_id,)
            )
            stale_count = cursor.rowcount
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

