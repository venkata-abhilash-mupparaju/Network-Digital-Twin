"""
Unit and Integration Tests for Controlled ICMP Probes and Device Testing
-------------------------------------------------------------------------
Validates packet loss calculation, latency averaging, probe statistics,
the manual /api/monitor/<device_id>/test endpoint, and device registration.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import subprocess

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app
from modules.monitor import ping_device_probes
from database import get_db_connection

SAMPLE_WINDOWS_PING_SUCCESS = """
Pinging 192.168.43.101 with 32 bytes of data:
Reply from 192.168.43.101: bytes=32 time=5ms TTL=64
Reply from 192.168.43.101: bytes=32 time=6ms TTL=64
Reply from 192.168.43.101: bytes=32 time=4ms TTL=64
Reply from 192.168.43.101: bytes=32 time=8ms TTL=64
Reply from 192.168.43.101: bytes=32 time=7ms TTL=64

Ping statistics for 192.168.43.101:
    Packets: Sent = 5, Received = 5, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 4ms, Maximum = 8ms, Average = 6ms
"""

SAMPLE_WINDOWS_PING_TIMEOUT = """
Pinging 192.168.43.200 with 32 bytes of data:
Request timed out.
Request timed out.
Request timed out.
Request timed out.
Request timed out.

Ping statistics for 192.168.43.200:
    Packets: Sent = 5, Received = 0, Lost = 5 (100% loss),
"""

SAMPLE_WINDOWS_PING_PARTIAL = """
Pinging 192.168.43.102 with 32 bytes of data:
Reply from 192.168.43.102: bytes=32 time=12ms TTL=64
Request timed out.
Reply from 192.168.43.102: bytes=32 time=15ms TTL=64
Reply from 192.168.43.102: bytes=32 time=14ms TTL=64
Reply from 192.168.43.102: bytes=32 time=11ms TTL=64

Ping statistics for 192.168.43.102:
    Packets: Sent = 5, Received = 4, Lost = 1 (20% loss),
Approximate round trip times in milli-seconds:
    Minimum = 11ms, Maximum = 15ms, Average = 13ms
"""


class TestProbesAndMonitoring(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    @patch("subprocess.run")
    def test_ping_device_probes_success(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout=SAMPLE_WINDOWS_PING_SUCCESS, stderr="")
        res = ping_device_probes("192.168.43.101", count=5, timeout_seconds=1)

        self.assertEqual(res["ip_address"], "192.168.43.101")
        self.assertEqual(res["status"], "ONLINE")
        self.assertEqual(res["packets_sent"], 5)
        self.assertEqual(res["packets_received"], 5)
        self.assertEqual(res["packet_loss"], 0.0)
        self.assertEqual(res["average_latency"], 6.0)
        self.assertEqual(res["last_latency"], 7.0)
        self.assertIsNone(res["error"])

    @patch("subprocess.run")
    def test_ping_device_probes_timeout(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=1, stdout=SAMPLE_WINDOWS_PING_TIMEOUT, stderr="")
        res = ping_device_probes("192.168.43.200", count=5, timeout_seconds=1)

        self.assertIn(res["status"], ["UNREACHABLE", "OFFLINE"])
        self.assertEqual(res["packets_sent"], 5)
        self.assertEqual(res["packets_received"], 0)
        self.assertEqual(res["packet_loss"], 100.0)
        self.assertIsNone(res["average_latency"])

    @patch("subprocess.run")
    def test_ping_device_probes_partial(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout=SAMPLE_WINDOWS_PING_PARTIAL, stderr="")
        res = ping_device_probes("192.168.43.102", count=5, timeout_seconds=1)

        self.assertEqual(res["status"], "ONLINE")
        self.assertEqual(res["packets_sent"], 5)
        self.assertEqual(res["packets_received"], 4)
        self.assertEqual(res["packet_loss"], 20.0)
        self.assertEqual(res["average_latency"], 13.0)

    def test_ping_device_probes_invalid_ip(self):
        res = ping_device_probes("invalid.ip.address", count=5)
        self.assertIn(res["status"], ["UNREACHABLE", "OFFLINE"])
        self.assertEqual(res["packet_loss"], 100.0)
        self.assertIn("Invalid", res["error"])

    @patch("subprocess.run")
    def test_api_monitor_device_test_endpoint(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout=SAMPLE_WINDOWS_PING_SUCCESS, stderr="")
        response = self.client.post("/api/monitor/PC1/test")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertTrue(data["success"])
        self.assertEqual(data["device_id"], "PC1")
        self.assertEqual(data["packets_sent"], 5)
        self.assertEqual(data["packets_received"], 5)
        self.assertEqual(data["packet_loss"], 0.0)

    def test_api_monitor_device_not_found(self):
        response = self.client.post("/api/monitor/UNKNOWN_DEVICE_999/test")
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertIn("error", data)

    def test_api_devices_post_with_metadata(self):
        payload = {
            "device_id": "TEST_PROBE_DEV",
            "device_name": "Probe Target",
            "ip_address": "192.168.43.77",
            "device_type": "Laptop",
            "description": "Probe unit test",
            "mac_address": "00-11-22-33-44-55",
            "network_id": "Wi-Fi|192.168.43.0/24|192.168.43.1"
        }
        res = self.client.post("/api/devices", json=payload)
        self.assertIn(res.status_code, [201, 400])

        # Clean up
        self.client.delete("/api/devices/TEST_PROBE_DEV")


if __name__ == "__main__":
    unittest.main()
