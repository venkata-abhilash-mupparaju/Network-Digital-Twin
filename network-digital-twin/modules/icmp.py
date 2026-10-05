"""
ICMP Ping & Telemetry Module
-----------------------------
Executes safe, non-intrusive ICMP reachability checks, multi-packet loss calculations,
and round-trip time (RTT) statistics against authorized network devices.
Strictly:
- Uses OS-level ping utility via subprocess.run with argument list (shell=False).
- Validates every IP address format using Python's ipaddress module.
- Accurately computes packets sent, packets received, packet loss percentage,
  minimum latency, average latency, and maximum latency.
- When 0 packets are received, latency is returned as None (N/A), never 0 ms.
"""

import re
import ipaddress
import logging
import platform
import subprocess
from datetime import datetime, timezone

from config import DEFAULT_PING_TIMEOUT, PING_COUNT

logger = logging.getLogger(__name__)


def validate_ip(ip_str):
    """
    Validates that a string is a strictly valid IPv4 address.
    Returns True if valid, False otherwise.
    """
    if not ip_str or not isinstance(ip_str, str):
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_str.strip())
        return ip_obj.version == 4
    except ValueError:
        return False


def ping_host(ip, timeout_seconds=DEFAULT_PING_TIMEOUT):
    """
    Safely executes a single ICMP ping against an authorized IPv4 address.
    
    Returns:
        tuple: (is_online: bool, latency_ms: float or None, error_message: str or None)
    """
    clean_ip = ip.strip() if ip else ""
    if not validate_ip(clean_ip):
        return False, None, "Invalid IPv4 address format"

    system_os = platform.system().lower()
    timeout_ms = max(500, int(timeout_seconds * 1000))

    try:
        if system_os == "windows":
            # -n 1: 1 packet, -w timeout in milliseconds
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), clean_ip]
        else:
            # Linux / macOS: -c 1: 1 packet, -W timeout in seconds
            cmd = ["ping", "-c", "1", "-W", str(timeout_seconds), clean_ip]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds + 2
        )

        stdout = result.stdout

        if result.returncode == 0:
            time_match = re.search(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", stdout, re.IGNORECASE)
            if time_match:
                latency = float(time_match.group(1))
            elif "time<1ms" in stdout.lower() or "time<1 ms" in stdout.lower():
                latency = 0.5
            else:
                latency = 1.0

            if "unreachable" in stdout.lower() or "timed out" in stdout.lower():
                return False, None, "Host unreachable"

            return True, round(latency, 2), None
        else:
            return False, None, "Request timed out"

    except subprocess.TimeoutExpired:
        return False, None, "Ping subprocess timed out"
    except Exception as e:
        logger.error("Error pinging %s: %s", clean_ip, e)
        return False, None, str(e)


def ping_device_probes(ip, count=PING_COUNT, timeout_seconds=DEFAULT_PING_TIMEOUT):
    """
    Executes a controlled series of ICMP echo requests against an authorized IP
    to calculate packet loss, latency statistics (min, avg, max), and reachability.
    
    Args:
        ip (str): Target IPv4 address.
        count (int): Number of probe packets (default PING_COUNT = 5).
        timeout_seconds (float): Timeout per probe in seconds.
        
    Returns:
        dict: Detailed telemetry measurement:
            - packets_sent (int)
            - packets_received (int)
            - packet_loss (float, 0.0 - 100.0)
            - min_latency (float or None)
            - average_latency (float or None)
            - max_latency (float or None)
            - last_latency (float or None)
            - status ("ONLINE" or "UNREACHABLE")
            - timestamp (ISO 8601 string)
    """
    clean_ip = ip.strip() if ip else ""
    if not validate_ip(clean_ip):
        return {
            "ip_address": clean_ip,
            "status": "UNREACHABLE",
            "packets_sent": count,
            "packets_received": 0,
            "packet_loss": 100.0,
            "min_latency": None,
            "average_latency": None,
            "max_latency": None,
            "last_latency": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": "Invalid IPv4 address format"
        }

    system_os = platform.system().lower()
    timeout_ms = max(500, int(timeout_seconds * 1000))

    try:
        if system_os == "windows":
            cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), clean_ip]
        else:
            cmd = ["ping", "-c", str(count), "-W", str(timeout_seconds), clean_ip]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=(count * timeout_seconds) + 4
        )

        stdout = result.stdout

        # Parse packets sent and received
        stat_match = re.search(r"Packets:\s*Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", stdout, re.IGNORECASE)
        if stat_match:
            sent = int(stat_match.group(1))
            received = int(stat_match.group(2))
        else:
            # Fallback reply counting
            sent = count
            received = len(re.findall(r"Reply from", stdout, re.IGNORECASE))

        # Ensure received <= sent
        received = min(received, sent)

        # Exact packet loss formula: ((packets_sent - packets_received) / packets_sent) * 100
        packet_loss = round(((sent - received) / sent) * 100.0, 1) if sent > 0 else 100.0

        # Parse individual reply latencies
        reply_latencies = []
        for m in re.finditer(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", stdout, re.IGNORECASE):
            reply_latencies.append(float(m.group(1)))

        if not reply_latencies and received > 0 and ("time<1ms" in stdout.lower() or "time<1 ms" in stdout.lower()):
            reply_latencies = [0.5] * received

        # Calculate statistics
        if received > 0 and reply_latencies:
            min_latency = round(min(reply_latencies), 2)
            max_latency = round(max(reply_latencies), 2)
            last_latency = round(reply_latencies[-1], 2)
            avg_m = re.search(r"Average\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*ms", stdout, re.IGNORECASE)
            if avg_m:
                avg_latency = round(float(avg_m.group(1)), 2)
            else:
                avg_latency = round(sum(reply_latencies) / len(reply_latencies), 2)
            
            # Jitter calculation: mean absolute difference between consecutive RTT samples
            if len(reply_latencies) > 1:
                diffs = [abs(reply_latencies[i] - reply_latencies[i - 1]) for i in range(1, len(reply_latencies))]
                jitter = round(sum(diffs) / len(diffs), 2)
            else:
                jitter = 0.0
            is_online = True
        elif received > 0:
            min_latency = 1.0
            max_latency = 1.0
            avg_latency = 1.0
            last_latency = 1.0
            jitter = 0.0
            is_online = True
        else:
            # CRITICAL: If no packets received, latency must be None (N/A), NOT 0 ms
            min_latency = None
            average_latency = None
            max_latency = None
            last_latency = None
            avg_latency = None
            jitter = None
            is_online = False

        status = "ONLINE" if is_online else "UNREACHABLE"

        return {
            "ip_address": clean_ip,
            "status": status,
            "packets_sent": sent,
            "packets_received": received,
            "packet_loss": packet_loss,
            "min_latency": min_latency,
            "average_latency": avg_latency,
            "max_latency": max_latency,
            "last_latency": last_latency,
            "jitter": jitter,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": None if is_online else "Host unreachable from this host"
        }

    except subprocess.TimeoutExpired:
        return {
            "ip_address": clean_ip,
            "status": "UNREACHABLE",
            "packets_sent": count,
            "packets_received": 0,
            "packet_loss": 100.0,
            "min_latency": None,
            "average_latency": None,
            "max_latency": None,
            "last_latency": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": "Probe command timed out"
        }
    except Exception as e:
        logger.error("Error running probe against %s: %s", clean_ip, e)
        return {
            "ip_address": clean_ip,
            "status": "UNREACHABLE",
            "packets_sent": count,
            "packets_received": 0,
            "packet_loss": 100.0,
            "min_latency": None,
            "average_latency": None,
            "max_latency": None,
            "last_latency": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }
