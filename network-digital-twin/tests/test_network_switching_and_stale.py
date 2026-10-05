"""
Test Network Switching, Stale Device Isolation, and Subnet Protection
---------------------------------------------------------------------
Verifies that:
1. When switching networks (Hotspot A -> Hotspot B), previous devices are marked STALE.
2. The Digital Twin in-memory graph only contains current network nodes.
3. The background monitor only pings devices on the active network.
4. Large subnets (> /24) are protected against unbounded sweeps.
5. Adopting a device safely brings it into the active Digital Twin.
"""

import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import sqlite3

from modules.network_model import DigitalTwinNetwork
from modules.network_info import parse_ipconfig_output, sync_network_session
from modules.monitor import NetworkMonitor
from modules.discovery import discover_devices_active

SAMPLE_HOTSPOT_A = """
Wireless LAN adapter Wi-Fi:
   Connection-specific DNS Suffix  . :
   IPv4 Address. . . . . . . . . . . : 192.168.43.50
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.43.1
"""

SAMPLE_CAMPUS_B = """
Wireless LAN adapter Wi-Fi:
   Connection-specific DNS Suffix  . : campus.edu
   IPv4 Address. . . . . . . . . . . : 172.16.8.225
   Subnet Mask . . . . . . . . . . . : 255.255.224.0
   Default Gateway . . . . . . . . . : 172.16.0.1
"""

class TestNetworkSwitchingAndStaleIsolation(unittest.TestCase):

    def setUp(self):
        self.digital_twin = DigitalTwinNetwork(gateway_id="GW_TEST", gateway_name="Test Gateway")
        self.net_a = parse_ipconfig_output(SAMPLE_HOTSPOT_A)
        self.net_b = parse_ipconfig_output(SAMPLE_CAMPUS_B)

    def test_stale_device_isolation_on_network_switch(self):
        """Verifies switching from Hotspot A to Campus B marks Hotspot A devices as STALE."""
        # 1. Sync session on Hotspot A
        session_a = sync_network_session(self.net_a)
        self.assertIsNotNone(session_a)

        # 2. Add devices on Hotspot A
        from database import get_db_connection
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM devices WHERE device_id LIKE 'TEST_SW_%'")
            c.execute(
                """
                INSERT INTO devices (device_id, device_name, ip_address, network_id, status, is_monitored)
                VALUES ('TEST_SW_1', 'Hotspot Laptop 1', '192.168.43.101', ?, 'ONLINE', 1)
                """,
                (self.net_a["network_id"],)
            )
            c.execute(
                """
                INSERT INTO devices (device_id, device_name, ip_address, network_id, status, is_monitored)
                VALUES ('TEST_SW_2', 'Hotspot Laptop 2', '192.168.43.102', ?, 'ONLINE', 1)
                """,
                (self.net_a["network_id"],)
            )
            conn.commit()

        # 3. Verify they were registered under Hotspot A
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM devices WHERE device_id = 'TEST_SW_1'")
            self.assertEqual(c.fetchone()["status"], "ONLINE")

        # 4. Switch network to Campus B (172.16.0.0/19)
        session_b = sync_network_session(self.net_b)
        self.assertNotEqual(session_a, session_b)

        # 5. Check that Hotspot A devices are now marked as STALE
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM devices WHERE device_id = 'TEST_SW_1'")
            self.assertEqual(c.fetchone()["status"], "STALE")
            c.execute("SELECT status FROM devices WHERE device_id = 'TEST_SW_2'")
            self.assertEqual(c.fetchone()["status"], "STALE")

            # Clean up test rows
            c.execute("DELETE FROM devices WHERE device_id LIKE 'TEST_SW_%'")
            conn.commit()

    def test_digital_twin_scoping_to_current_network(self):
        """Verifies Digital Twin graph only holds current network nodes."""
        twin = DigitalTwinNetwork(gateway_id="GW_HUB")
        twin.update_gateway_ip("192.168.43.1")
        twin.add_device("OLD_PC1", "Old PC 1", "192.168.43.101")
        twin.add_device("OLD_PC2", "Old PC 2", "192.168.43.102")

        self.assertIn("OLD_PC1", twin.graph)
        self.assertIn("OLD_PC2", twin.graph)

        # Sync to new network containing only NEW_PC
        current_monitored = [
            {"device_id": "NEW_PC", "device_name": "New Laptop", "ip_address": "172.16.8.50"}
        ]
        twin.sync_with_current_network(gateway_ip="172.16.0.1", monitored_devices=current_monitored)

        # Old devices must be evicted from the live graph
        self.assertNotIn("OLD_PC1", twin.graph)
        self.assertNotIn("OLD_PC2", twin.graph)
        # New device must be present
        self.assertIn("NEW_PC", twin.graph)
        self.assertEqual(twin.graph.nodes["GW_HUB"]["ip"], "172.16.0.1")

    def test_large_subnet_protection_during_active_discovery(self):
        """Verifies active discovery on /19 subnet restricts candidate count and returns warning."""
        with patch("modules.discovery.discover_local_devices") as mock_arp:
            mock_arp.return_value = (True, [], None)
            with patch("modules.discovery._quick_ping") as mock_ping:
                mock_ping.return_value = ("172.16.0.1", True)

                result = discover_devices_active(
                    target_network="172.16.0.0/19",
                    gateway="172.16.0.1",
                    local_ip="172.16.8.225",
                    max_hosts=64
                )

                self.assertTrue(result["success"])
                # Must not scan all 8190 hosts, must be capped to max_hosts
                self.assertLessEqual(result["scanned_hosts"], 64)
                self.assertIsNotNone(result["warning"])
                self.assertIn("Large subnet detected", result["warning"])

    def test_monitoring_cycle_does_not_ping_stale_devices(self):
        """Verifies monitor.run_single_cycle() ignores stale devices."""
        twin = DigitalTwinNetwork(gateway_id="GW_MON")
        monitor = NetworkMonitor(twin, interval=5)

        with patch("modules.monitor.check_network_change") as mock_chk:
            mock_chk.return_value = (self.net_b, False)
            with patch("modules.monitor.ping_host") as mock_ping:
                mock_ping.return_value = (True, 4.5, None)

                # Insert one current device and one stale device
                from database import get_db_connection
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("DELETE FROM devices WHERE device_id LIKE 'TEST_MON_%'")
                    c.execute(
                        """
                        INSERT INTO devices (device_id, device_name, ip_address, network_id, status, is_monitored)
                        VALUES ('TEST_MON_CUR', 'Current Device', '172.16.8.99', ?, 'ONLINE', 1)
                        """,
                        (self.net_b["network_id"],)
                    )
                    c.execute(
                        """
                        INSERT INTO devices (device_id, device_name, ip_address, network_id, status, is_monitored)
                        VALUES ('TEST_MON_STALE', 'Old Hotspot Device', '192.168.43.199', 'Wi-Fi|192.168.43.0/24|192.168.43.1', 'STALE', 1)
                        """,
                    )
                    conn.commit()

                results = monitor.run_single_cycle()

                # Stale device must NOT be in the results of the active cycle
                result_ids = [r["device_id"] for r in results]
                self.assertIn("TEST_MON_CUR", result_ids)
                self.assertNotIn("TEST_MON_STALE", result_ids)

                # Digital Twin graph must not contain the stale device
                self.assertNotIn("TEST_MON_STALE", twin.graph)

                # Clean up
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("DELETE FROM devices WHERE device_id LIKE 'TEST_MON_%'")
                    conn.commit()

if __name__ == "__main__":
    unittest.main()
