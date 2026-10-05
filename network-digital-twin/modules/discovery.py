"""
Local Device Discovery Module
-----------------------------
Discovers visible active devices on the local authorized test network
using controlled concurrent ICMP echo requests and Windows ARP/neighbor cache.

Strictly follows:
- Dynamic candidate IP calculation using Python's ipaddress module.
- For /24: exactly (254 usable - 1 local host) = 253 candidate probes.
- Excludes network address, broadcast address, and the monitoring host's own IP.
- Large Network Protection: if subnet hosts > MAX_DISCOVERY_CANDIDATES (e.g. /16, /19),
  full sweep is disabled, using ARP cache + targeted candidate window with clear warning.
- Concurrently probes via bounded ThreadPoolExecutor (max_workers=DISCOVERY_WORKERS=32).
- Non-blocking discovery progress tracking (checked / total).
- Merges ICMP responses with OS ARP/neighbor cache (MAC, latency, source).
- Deduplicates devices and flags Gateway.
- Never scans unauthorized or public subnets; operates only on detected local network.
"""

import re
import ipaddress
import logging
import platform
import subprocess
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    MAX_DISCOVERY_CANDIDATES,
    DISCOVERY_TIMEOUT,
    DISCOVERY_WORKERS,
    HOTSPOT_GATEWAY_ID,
    HOTSPOT_GATEWAY_NAME
)
from .network_info import get_current_network_info

logger = logging.getLogger(__name__)

# Global thread-safe discovery progress state
_progress_lock = threading.Lock()
_discovery_progress = {
    "is_running": False,
    "checked": 0,
    "total": 0,
    "percent": 0,
    "visible_count": 0,
    "target_network": None,
    "status_message": "Idle"
}


def get_discovery_progress():
    """Returns a snapshot of the current active discovery progress."""
    with _progress_lock:
        return dict(_discovery_progress)


def _update_progress(checked=None, total=None, visible_count=None, is_running=None, status_message=None, target_network=None):
    """Safely updates the discovery progress status."""
    with _progress_lock:
        if is_running is not None:
            _discovery_progress["is_running"] = is_running
        if target_network is not None:
            _discovery_progress["target_network"] = target_network
        if total is not None:
            _discovery_progress["total"] = total
        if checked is not None:
            _discovery_progress["checked"] = checked
        if visible_count is not None:
            _discovery_progress["visible_count"] = visible_count
        if status_message is not None:
            _discovery_progress["status_message"] = status_message

        tot = _discovery_progress["total"]
        chk = _discovery_progress["checked"]
        _discovery_progress["percent"] = int((chk / tot) * 100) if tot > 0 else 0


def parse_arp_output(arp_text, target_network=None):
    """
    Parses output from the OS 'arp -a' command into a structured list of devices.
    Filters out broadcast, multicast, loopback, and non-private IP addresses.
    Optionally scopes discovery to target_network (e.g. '192.168.8.0/24').
    
    Args:
        arp_text (str): Raw stdout text from 'arp -a'.
        target_network (str or IPv4Network, optional): Subnet CIDR to filter by.
        
    Returns:
        list: Filtered and deduplicated dictionaries of discovered devices.
    """
    if not arp_text or not isinstance(arp_text, str):
        return []

    net_filter = None
    if target_network:
        try:
            if isinstance(target_network, str):
                net_filter = ipaddress.IPv4Network(target_network, strict=False)
            elif isinstance(target_network, ipaddress.IPv4Network):
                net_filter = target_network
        except ValueError:
            logger.warning("Invalid target_network filter: %s", target_network)

    discovered = []
    seen_ips = set()
    current_interface = None

    lines = arp_text.splitlines()
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Detect interface header, e.g., "Interface: 192.168.8.213 --- 0x14"
        interface_match = re.search(r"Interface:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", line_str, re.IGNORECASE)
        if interface_match:
            current_interface = interface_match.group(1)
            continue

        # Match table entries: IP address, physical address (MAC), type
        # Format example: "192.168.8.9    3c-64-cf-8b-98-11     dynamic"
        entry_match = re.search(
            r"^\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\s+([0-9a-fA-F[:-]{11,17})?\s*([a-zA-Z]+)?",
            line_str
        )
        if not entry_match:
            continue

        ip_str = entry_match.group(1).strip()
        mac_raw = entry_match.group(2)
        entry_type = (entry_match.group(3) or "dynamic").lower()

        mac_str = mac_raw.strip().upper().replace(":", "-") if mac_raw else "UNKNOWN"

        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.version != 4:
                continue
        except ValueError:
            continue

        # Strict safety filtering
        if not ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_multicast:
            continue

        if ip_str == "255.255.255.255" or ip_str.endswith(".255") or mac_str == "FF-FF-FF-FF-FF-FF":
            continue

        if ip_str.endswith(".0") or mac_str.startswith("01-00-5E"):
            continue

        if net_filter and ip_obj not in net_filter:
            continue

        if ip_str in seen_ips:
            continue
        seen_ips.add(ip_str)

        discovered.append({
            "ip_address": ip_str,
            "mac_address": mac_str,
            "interface": current_interface,
            "entry_type": entry_type,
            "visibility": "VISIBLE",
            "source": "ARP"
        })

    return discovered


def discover_local_devices(target_network=None, timeout_seconds=3):
    """
    Discovers active devices on the local network by reading the OS ARP cache.
    Returns: (success: bool, devices: list, error_message: str or None)
    """
    try:
        cmd = ["arp", "-a"]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"ARP exited with code {result.returncode}"
            return False, [], error_msg

        devices = parse_arp_output(result.stdout, target_network=target_network)
        return True, devices, None

    except subprocess.TimeoutExpired:
        return False, [], "ARP command timed out."
    except FileNotFoundError:
        return False, [], "The 'arp' command is not available in PATH."
    except Exception as e:
        return False, [], str(e)


def _quick_probe_ping(ip, timeout_seconds=DISCOVERY_TIMEOUT):
    """
    Sends 1 fast ICMP ping probe to test reachability and populate the ARP cache.
    Returns: (ip: str, is_online: bool, latency: float or None)
    """
    system_os = platform.system().lower()
    timeout_ms = max(400, int(timeout_seconds * 1000))

    try:
        if system_os == "windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), str(ip)]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout_seconds), str(ip)]

        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds + 1
        )

        stdout = res.stdout
        is_online = (
            res.returncode == 0 and
            "unreachable" not in stdout.lower() and
            "timed out" not in stdout.lower() and
            ("reply from" in stdout.lower() or "time=" in stdout.lower() or "time<" in stdout.lower())
        )

        latency = None
        if is_online:
            m = re.search(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", stdout, re.IGNORECASE)
            if m:
                latency = round(float(m.group(1)), 2)
            elif "time<1ms" in stdout.lower() or "time<1 ms" in stdout.lower():
                latency = 0.5
            else:
                latency = 1.0

        return str(ip), is_online, latency
    except Exception:
        return str(ip), False, None

# Alias for backwards compatibility with tests
_quick_ping = _quick_probe_ping


def discover_from_arp(target_network=None, local_ip=None):
    """
    Independent Discovery Source 1: OS ARP & Neighbor Cache inspection.
    Retrieves IP, MAC, interface, and entry type from the OS neighbor table.
    Excludes the monitoring laptop's own IP address.
    """
    _, arp_devices, _ = discover_local_devices(target_network=target_network)
    if local_ip:
        arp_devices = [d for d in arp_devices if d["ip_address"] != local_ip]
    return arp_devices


def discover_from_icmp(candidate_ips, timeout=DISCOVERY_TIMEOUT, max_workers=DISCOVERY_WORKERS, on_progress=None):
    """
    Independent Discovery Source 2: Controlled concurrent ICMP echo probes.
    Sweeps candidate IPs using bounded worker threads.
    Returns: dict mapping responsive IPv4 string -> latency (ms).
    """
    online_responders = {}
    checked_count = 0
    total_candidates = len(candidate_ips)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {
            executor.submit(_quick_ping, ip, timeout): ip for ip in candidate_ips
        }
        for future in as_completed(future_to_ip):
            probe_res = future.result()
            if isinstance(probe_res, (list, tuple)) and len(probe_res) == 2:
                ip, is_online = probe_res
                latency = 1.0 if is_online else None
            elif isinstance(probe_res, (list, tuple)) and len(probe_res) >= 3:
                ip, is_online, latency = probe_res[0], probe_res[1], probe_res[2]
            else:
                ip, is_online, latency = str(future_to_ip[future]), False, None

            checked_count += 1
            if is_online:
                online_responders[ip] = latency

            if on_progress:
                on_progress(checked_count, total_candidates, len(online_responders))

    return online_responders


def discover_from_gateway_if_available(gateway_ip=None):
    """
    Independent Discovery Source 3: Hotspot / Gateway authoritative client query.
    Note: On mobile hotspots (Android/iOS tethering) and standard consumer Wi-Fi,
    administrative client tables are not exposed to normal client laptops due to security
    and client isolation constraints. Returns None when unavailable.
    """
    return None


def merge_discovery_results(arp_devices, icmp_results, gateway_clients=None, local_ip=None, gateway=None, network_id=None):
    """
    Merges independent discovery sources (ARP + ICMP + Gateway) into unified visible device records.
    Each record contains:
    - device_id, ip_address, mac_address, hostname, device_type
    - source ('ICMP + ARP', 'ARP', 'ICMP', or 'GATEWAY')
    - discovery_latency, network_id, status ('VISIBLE'), visibility ('VISIBLE')
    - first_seen, last_seen
    """
    merged = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    arp_map = {d["ip_address"]: d for d in (arp_devices or []) if d["ip_address"] != local_ip}
    icmp_map = icmp_results or {}

    # 1. Process ARP devices
    for ip, arp_dev in arp_map.items():
        is_gw = (ip == gateway)
        has_icmp = ip in icmp_map
        source = "ICMP + ARP" if has_icmp else "ARP"
        lat = icmp_map.get(ip)

        merged[ip] = {
            "device_id": HOTSPOT_GATEWAY_ID if is_gw else f"DEV_{ip.replace('.', '_')}",
            "ip_address": ip,
            "mac_address": arp_dev.get("mac_address", "UNKNOWN"),
            "hostname": "Hotspot Gateway" if is_gw else f"Host-{ip.split('.')[-1]}",
            "device_type": "Gateway" if is_gw else "Computer",
            "source": source,
            "discovery_latency": lat,
            "network_id": network_id,
            "status": "VISIBLE",
            "is_gateway": is_gw,
            "entry_type": arp_dev.get("entry_type", "dynamic"),
            "visibility": "VISIBLE",
            "first_seen": now_iso,
            "last_seen": now_iso
        }

    # 2. Process ICMP responders not yet in ARP table
    for ip, lat in icmp_map.items():
        if ip not in merged and ip != local_ip:
            is_gw = (ip == gateway)
            merged[ip] = {
                "device_id": HOTSPOT_GATEWAY_ID if is_gw else f"DEV_{ip.replace('.', '_')}",
                "ip_address": ip,
                "mac_address": "UNKNOWN",
                "hostname": "Hotspot Gateway" if is_gw else f"Host-{ip.split('.')[-1]}",
                "device_type": "Gateway" if is_gw else "Computer",
                "source": "ICMP",
                "discovery_latency": lat,
                "network_id": network_id,
                "status": "VISIBLE",
                "is_gateway": is_gw,
                "entry_type": "dynamic",
                "visibility": "VISIBLE",
                "first_seen": now_iso,
                "last_seen": now_iso
            }

    # 3. Process gateway-reported clients if available
    if gateway_clients:
        for client in gateway_clients:
            ip = client.get("ip_address")
            if ip and ip != local_ip and ip not in merged:
                merged[ip] = {
                    "device_id": f"DEV_{ip.replace('.', '_')}",
                    "ip_address": ip,
                    "mac_address": client.get("mac_address", "UNKNOWN"),
                    "hostname": client.get("hostname", f"Host-{ip.split('.')[-1]}"),
                    "device_type": client.get("device_type", "UNKNOWN"),
                    "source": "GATEWAY",
                    "discovery_latency": None,
                    "network_id": network_id,
                    "status": "VISIBLE",
                    "is_gateway": False,
                    "entry_type": "managed",
                    "visibility": "VISIBLE",
                    "first_seen": now_iso,
                    "last_seen": now_iso
                }

    results = list(merged.values())
    results.sort(key=lambda d: (not d.get("is_gateway", False), [int(x) for x in d["ip_address"].split(".")]))
    return results


def discover_devices_active(target_network=None, gateway=None, local_ip=None,
                            max_candidates=MAX_DISCOVERY_CANDIDATES, timeout=DISCOVERY_TIMEOUT,
                            max_hosts=None, **kwargs):
    """
    Performs controlled active local device discovery across the active subnet.
    
    Candidate generation rules:
    - Calculated dynamically via ipaddress.IPv4Network(target_network).
    - Usable host addresses from net_obj.hosts().
    - Excludes network address, broadcast address, and the local host IP.
    - For /24 (256 addresses): all 253 remote usable hosts are scanned.
    - Large Network Protection: if num_addresses > max_candidates (e.g. /19 with 8,192 addresses),
      disables full sweep, relies on ARP + targeted window around Gateway and Local IP, and sets warning.
    - ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS=32).
    - Progress is reported via get_discovery_progress().
    
    Returns:
        dict: {
            "success": bool,
            "devices": list of dicts,
            "count": int,
            "scanned_hosts": int,
            "warning": str or None,
            "error": str or None
        }
    """
    if max_hosts is not None:
        max_candidates = max_hosts

    try:
        # Detect active network context if not provided
        net_info = get_current_network_info()
        target_network = target_network or net_info.get("network")
        gateway = gateway or net_info.get("gateway")
        local_ip = local_ip or net_info.get("local_ip")
        network_id = net_info.get("network_id", "")

        if not target_network or target_network == "None":
            return {
                "success": False,
                "devices": [],
                "count": 0,
                "scanned_hosts": 0,
                "warning": None,
                "error": "No active network detected for device discovery."
            }

        try:
            net_obj = ipaddress.IPv4Network(target_network, strict=False)
        except ValueError:
            return {
                "success": False,
                "devices": [],
                "count": 0,
                "scanned_hosts": 0,
                "warning": None,
                "error": f"Invalid network CIDR: {target_network}"
            }

        num_addresses = net_obj.num_addresses
        warning = None
        candidate_ips = []

        # Subnet Protection check:
        # For standard /24 (256 addresses) or smaller (/25, /26, /27, /28), num_addresses <= max_candidates (512)
        if num_addresses > max_candidates:
            warning = (
                f"Large subnet detected ({target_network} with {num_addresses} addresses). "
                f"Full active discovery is disabled to prevent network saturation. "
                f"Showing visible ARP/neighbor hosts and using a targeted discovery window."
            )
            logger.warning(warning)

            # Build targeted candidate set around gateway and local IP
            target_ips = set()
            if gateway:
                try:
                    gw_int = int(ipaddress.IPv4Address(gateway))
                    for offset in range(-4, 33):
                        cand = ipaddress.IPv4Address(gw_int + offset)
                        if cand in net_obj and cand != net_obj.network_address and cand != net_obj.broadcast_address:
                            if str(cand) != local_ip:
                                target_ips.add(str(cand))
                except Exception:
                    pass

            if local_ip:
                try:
                    loc_int = int(ipaddress.IPv4Address(local_ip))
                    for offset in range(-16, 17):
                        cand = ipaddress.IPv4Address(loc_int + offset)
                        if cand in net_obj and cand != net_obj.network_address and cand != net_obj.broadcast_address:
                            if str(cand) != local_ip:
                                target_ips.add(str(cand))
                except Exception:
                    pass

            candidate_ips = sorted(list(target_ips))
            if len(candidate_ips) > max_candidates:
                candidate_ips = candidate_ips[:max_candidates]
        else:
            # Standard /24 or smaller subnet: dynamically calculate all usable remote candidates
            # net_obj.hosts() excludes network address and broadcast address automatically
            # Exclude local machine's own IP
            candidate_ips = [
                str(h) for h in net_obj.hosts()
                if str(h) != local_ip
            ]
            if len(candidate_ips) > max_candidates:
                candidate_ips = candidate_ips[:max_candidates]

        total_candidates = len(candidate_ips)
        logger.info("Executing active discovery across %d dynamically calculated candidate(s) on %s...",
                    total_candidates, target_network)

        # Initialize progress tracker
        _update_progress(
            checked=0,
            total=total_candidates,
            visible_count=0,
            is_running=True,
            status_message="DISCOVERY IN PROGRESS",
            target_network=target_network
        )

        online_responders = {}
        checked_count = 0

        # Concurrent probe sweep using ThreadPoolExecutor
        def _on_progress(chk, tot, vis):
            if chk % 8 == 0 or chk == tot:
                _update_progress(
                    checked=chk,
                    visible_count=vis,
                    status_message=f"DISCOVERY IN PROGRESS ({chk}/{tot})"
                )

        online_responders = discover_from_icmp(
            candidate_ips, timeout=timeout, max_workers=DISCOVERY_WORKERS, on_progress=_on_progress
        )

        # After ICMP sweep refreshes OS neighbor cache, inspect the OS ARP table
        arp_devices = discover_from_arp(target_network=target_network, local_ip=local_ip)

        # Query gateway if available
        gw_clients = discover_from_gateway_if_available(gateway)

        # Merge independent discovery sources
        results = merge_discovery_results(
            arp_devices=arp_devices,
            icmp_results=online_responders,
            gateway_clients=gw_clients,
            local_ip=local_ip,
            gateway=gateway,
            network_id=network_id
        )

        # Build comprehensive discovery diagnostics (Section 12 & 13)
        diagnostics = {
            "network": target_network,
            "local_host": local_ip,
            "gateway": gateway,
            "candidates_checked": total_candidates,
            "arp_entries": len(arp_devices),
            "icmp_responses": len(online_responders),
            "gateway_clients": "N/A (Hotspot client isolation / no router API)",
            "visible_devices": len(results),
            "explanation": (
                "Other hotspot clients may be connected to the hotspot, but are not visible from this monitoring host "
                "due to Wi-Fi client isolation, AP isolation, or device ICMP firewalls."
                if len(results) <= 1 else
                f"Successfully resolved {len(results)} visible host(s) via dual-source ICMP probing and OS ARP cache inspection."
            )
        }

        # Finalize progress status
        _update_progress(
            checked=total_candidates,
            visible_count=len(results),
            is_running=False,
            status_message=f"DISCOVERY COMPLETE (Found {len(results)} visible device(s))"
        )

        return {
            "success": True,
            "devices": results,
            "count": len(results),
            "scanned_hosts": total_candidates,
            "warning": warning,
            "diagnostics": diagnostics,
            "error": None
        }

    except Exception as e:
        logger.error("Error during active device discovery: %s", e)
        _update_progress(is_running=False, status_message=f"Discovery Error: {str(e)}")
        return {
            "success": False,
            "devices": [],
            "count": 0,
            "scanned_hosts": 0,
            "warning": None,
            "error": str(e)
        }
