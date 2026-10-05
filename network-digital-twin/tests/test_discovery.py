"""
Test Suite for Local Device Discovery (Step 2)
----------------------------------------------
Verifies safe parsing of ARP cache output, safety filtering of non-private/multicast/broadcast
addresses, mock command execution, and the /api/discovery endpoint.
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
from modules.discovery import parse_arp_output, discover_local_devices

SAMPLE_WINDOWS_ARP_OUTPUT = """
Interface: 192.168.43.100 --- 0x14
  Internet Address      Physical Address      Type
  192.168.43.1          00-1a-2b-3c-4d-01     dynamic   
  192.168.43.101        00-1a-2b-3c-4d-02     dynamic   
  192.168.43.102        00-1a-2b-3c-4d-03     dynamic   
  192.168.43.255        ff-ff-ff-ff-ff-ff     static    
  224.0.0.22            01-00-5e-00-00-16     static    
  239.255.255.250       01-00-5e-7f-ff-fa     static    
  255.255.255.255       ff-ff-ff-ff-ff-ff     static    

Interface: 10.0.0.5 --- 0x1f
  Internet Address      Physical Address      Type
  8.8.8.8               00-1a-2b-3c-4d-99     dynamic   
  10.0.0.1              00-1a-2b-3c-4d-04     dynamic   
  10.0.0.1              00-1a-2b-3c-4d-04     dynamic   
"""

class TestDiscoveryModule(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_parse_valid_arp_output(self):
        """Test extraction of valid private IPv4 entries from sample ARP table."""
        devices = parse_arp_output(SAMPLE_WINDOWS_ARP_OUTPUT)
        
        # Verify valid private IPs were extracted
        extracted_ips = [d["ip_address"] for d in devices]
        self.assertIn("192.168.43.1", extracted_ips)
        self.assertIn("192.168.43.101", extracted_ips)
        self.assertIn("192.168.43.102", extracted_ips)
        self.assertIn("10.0.0.1", extracted_ips)
        
        # Verify MAC normalization to uppercase hyphenated format
        target = next(d for d in devices if d["ip_address"] == "192.168.43.101")
        self.assertEqual(target["mac_address"], "00-1A-2B-3C-4D-02")
        self.assertEqual(target["interface"], "192.168.43.100")

    def test_safety_filtering(self):
        """Verify strict safety filtering excludes broadcasts, multicasts, and public IPs."""
        devices = parse_arp_output(SAMPLE_WINDOWS_ARP_OUTPUT)
        extracted_ips = [d["ip_address"] for d in devices]
        
        # 1. No broadcast
        self.assertNotIn("192.168.43.255", extracted_ips)
        self.assertNotIn("255.255.255.255", extracted_ips)
        
        # 2. No multicast
        self.assertNotIn("224.0.0.22", extracted_ips)
        self.assertNotIn("239.255.255.250", extracted_ips)
        
        # 3. No public Internet IPs (e.g., 8.8.8.8)
        self.assertNotIn("8.8.8.8", extracted_ips)

    def test_deduplication(self):
        """Verify duplicate IP entries in ARP output are filtered to a single entry."""
        devices = parse_arp_output(SAMPLE_WINDOWS_ARP_OUTPUT)
        ips = [d["ip_address"] for d in devices]
        self.assertEqual(ips.count("10.0.0.1"), 1)

    def test_malformed_and_empty_output(self):
        """Verify parser handles empty, None, and garbage input gracefully."""
        self.assertEqual(parse_arp_output(""), [])
        self.assertEqual(parse_arp_output(None), [])
        self.assertEqual(parse_arp_output("Garbage text without any valid table entries"), [])

    @patch("subprocess.run")
    def test_discover_local_devices_mock_success(self, mock_run):
        """Test discover_local_devices with mocked subprocess call."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = SAMPLE_WINDOWS_ARP_OUTPUT
        mock_run.return_value = mock_result

        success, devices, err = discover_local_devices(timeout_seconds=2)
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertGreaterEqual(len(devices), 4)

    @patch("subprocess.run")
    def test_discover_local_devices_timeout(self, mock_run):
        """Test discover_local_devices handles timeout without crashing."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["arp", "-a"], timeout=1)

        success, devices, err = discover_local_devices(timeout_seconds=1)
        self.assertFalse(success)
        self.assertEqual(devices, [])
        self.assertIn("timed out", err.lower())

    def test_api_discovery_endpoint(self):
        """Test GET /api/discovery returns structured JSON with monitored status."""
        response = self.client.get("/api/discovery")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        self.assertIn("success", data)
        self.assertIn("devices", data)
        self.assertIn("count", data)
        self.assertIsInstance(data["devices"], list)

        # Check structure of items if any exist
        if data["devices"]:
            dev = data["devices"][0]
            self.assertIn("ip_address", dev)
            self.assertIn("mac_address", dev)
            self.assertIn("is_monitored", dev)

if __name__ == "__main__":
    unittest.main()
