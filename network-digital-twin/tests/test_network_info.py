"""
Unit Test Suite for Dynamic Network Information (Step 2)
--------------------------------------------------------
Tests parsing of Windows ipconfig output, subnet CIDR extraction,
network_id generation, network change detection, and /api/network/info.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app
from modules.network_info import parse_ipconfig_output, check_network_change, get_current_network_info

SAMPLE_IPCONFIG_WIFI = """
Windows IP Configuration

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . : 
   IPv4 Address. . . . . . . . . . . : 192.168.43.105
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.43.1

Ethernet adapter Bluetooth Network Connection:

   Media State . . . . . . . . . . . : Media disconnected
   Connection-specific DNS Suffix  . : 
"""

SAMPLE_IPCONFIG_SWITCHED = """
Windows IP Configuration

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . : 
   IPv4 Address. . . . . . . . . . . : 192.168.1.25
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.1.1
"""

SAMPLE_IPCONFIG_DISCONNECTED = """
Windows IP Configuration

Wireless LAN adapter Wi-Fi:

   Media State . . . . . . . . . . . : Media disconnected
   Connection-specific DNS Suffix  . : 
"""

class TestNetworkInfoModule(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_parse_active_wifi_network(self):
        """Test extraction of active Wi-Fi interface, IP, subnet, and gateway."""
        info = parse_ipconfig_output(SAMPLE_IPCONFIG_WIFI)
        self.assertEqual(info["interface"], "Wi-Fi")
        self.assertEqual(info["local_ip"], "192.168.43.105")
        self.assertEqual(info["gateway"], "192.168.43.1")
        self.assertEqual(info["network"], "192.168.43.0/24")
        self.assertEqual(info["network_id"], "Wi-Fi|192.168.43.0/24|192.168.43.1")
        self.assertEqual(info["status"], "CONNECTED")

    def test_parse_disconnected_network(self):
        """Test handling of disconnected media without crashing."""
        info = parse_ipconfig_output(SAMPLE_IPCONFIG_DISCONNECTED)
        self.assertEqual(info["status"], "DISCONNECTED")
        self.assertIsNone(info["local_ip"])
        self.assertIsNone(info["gateway"])
        self.assertEqual(info["network_id"], "DISCONNECTED")

    def test_parse_empty_or_none(self):
        """Test handling of empty or None output."""
        self.assertEqual(parse_ipconfig_output(""), {
            "interface": "None", "local_ip": None, "subnet_mask": None,
            "gateway": None, "network": None, "network_id": "DISCONNECTED", "status": "DISCONNECTED"
        })
        self.assertEqual(parse_ipconfig_output(None)["status"], "DISCONNECTED")

    @patch("modules.network_info.get_current_network_info")
    def test_network_change_detection(self, mock_get_info):
        """Test that switching from Network A to Network B triggers network_changed flag."""
        import modules.network_info as ni
        # Reset last known network
        ni._last_known_network_id = None

        # First connection (Network A)
        mock_get_info.return_value = parse_ipconfig_output(SAMPLE_IPCONFIG_WIFI)
        info1, changed1 = check_network_change()
        self.assertFalse(changed1)
        self.assertEqual(info1["network_id"], "Wi-Fi|192.168.43.0/24|192.168.43.1")

        # Same network again (no change)
        info2, changed2 = check_network_change()
        self.assertFalse(changed2)

        # Switch to Network B (Hotspot B)
        mock_get_info.return_value = parse_ipconfig_output(SAMPLE_IPCONFIG_SWITCHED)
        info3, changed3 = check_network_change()
        self.assertTrue(changed3)
        self.assertEqual(info3["network_id"], "Wi-Fi|192.168.1.0/24|192.168.1.1")

    def test_api_network_info_endpoint(self):
        """Test GET /api/network/info returns valid JSON schema."""
        resp = self.client.get("/api/network/info")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("network", data)
        net = data["network"]
        self.assertIn("interface", net)
        self.assertIn("local_ip", net)
        self.assertIn("gateway", net)
        self.assertIn("network", net)
        self.assertIn("network_id", net)
        self.assertIn("status", net)

if __name__ == "__main__":
    unittest.main()
