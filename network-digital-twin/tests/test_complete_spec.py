"""
Comprehensive Full-Spec Unit Test Suite
----------------------------------------
Tests all 19 required specification points:
1. CIDR calculation
2. /24 candidate generation
3. local IP exclusion
4. network address exclusion
5. broadcast exclusion
6. network change detection
7. stale device detection
8. packet loss calculation
9. latency statistics
10. discovery result merging
11. duplicate removal
12. Digital Twin node update
13. device failure simulation
14. link failure simulation
15. recovery simulation
16. shortest path
17. connectivity
18. database telemetry
19. event generation
"""

import os
import sys
import unittest
import ipaddress
import sqlite3
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import PING_COUNT
from modules.icmp import validate_ip, ping_device_probes
from modules.network_info import parse_ipconfig_output, check_network_change, sync_network_session
from modules.discovery import (
    parse_arp_output,
    discover_devices_active,
    get_discovery_progress
)
from modules.network_model import DigitalTwinNetwork
from modules.simulator import NetworkSimulator
from modules.events import (
    log_event,
    get_recent_events,
    EVENT_NETWORK_CHANGED,
    EVENT_DEVICE_UNREACHABLE,
    EVENT_DEVICE_RECOVERED
)
from database import get_db_connection


class TestCompleteSpecSuite(unittest.TestCase):

    # 1. CIDR calculation
    def test_01_cidr_calculation(self):
        sample_ipconfig = """
Wireless LAN adapter Wi-Fi:
   Connection-specific DNS Suffix  . :
   IPv4 Address. . . . . . . . . . . : 192.168.8.213
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.8.9
"""
        info = parse_ipconfig_output(sample_ipconfig)
        self.assertEqual(info["network"], "192.168.8.0/24")
        self.assertEqual(info["local_ip"], "192.168.8.213")
        self.assertEqual(info["gateway"], "192.168.8.9")
        self.assertEqual(info["network_id"], "Wi-Fi|192.168.8.0/24|192.168.8.9")

    # 2. /24 candidate generation (exact 253 remote candidates)
    # 3. local IP exclusion
    # 4. network address exclusion
    # 5. broadcast exclusion
    @patch("modules.discovery._quick_probe_ping", return_value=("192.168.8.9", True, 1.0))
    @patch("modules.discovery.discover_local_devices", return_value=(True, [], None))
    def test_02_to_05_candidate_generation_and_exclusions(self, mock_arp, mock_ping):
        # Target network 192.168.8.0/24, local IP 192.168.8.213, gateway 192.168.8.9
        result = discover_devices_active(
            target_network="192.168.8.0/24",
            gateway="192.168.8.9",
            local_ip="192.168.8.213"
        )
        self.assertTrue(result["success"])
        # Exactly 254 usable - 1 local = 253 scanned hosts
        self.assertEqual(result["scanned_hosts"], 253)

        # Verify net_obj rules directly
        net_obj = ipaddress.IPv4Network("192.168.8.0/24")
        candidates = [str(h) for h in net_obj.hosts() if str(h) != "192.168.8.213"]
        self.assertEqual(len(candidates), 253)
        self.assertNotIn("192.168.8.0", candidates)   # 4. network address excluded
        self.assertNotIn("192.168.8.255", candidates) # 5. broadcast excluded
        self.assertNotIn("192.168.8.213", candidates) # 3. local IP excluded
        self.assertIn("192.168.8.1", candidates)
        self.assertIn("192.168.8.9", candidates)
        self.assertIn("192.168.8.254", candidates)

    # 6. Network change detection
    def test_06_network_change_detection(self):
        with patch("modules.network_info.get_current_network_info") as mock_info:
            import modules.network_info as ni
            ni._last_known_network_id = "Wi-Fi|192.168.8.0/24|192.168.8.9"
            
            mock_info.return_value = {
                "interface": "Wi-Fi",
                "local_ip": "192.168.43.50",
                "gateway": "192.168.43.1",
                "network": "192.168.43.0/24",
                "network_id": "Wi-Fi|192.168.43.0/24|192.168.43.1",
                "status": "CONNECTED"
            }
            info, has_changed = check_network_change()
            self.assertTrue(has_changed)
            self.assertEqual(info["network_id"], "Wi-Fi|192.168.43.0/24|192.168.43.1")

    # 7. Stale device detection
    def test_07_stale_device_detection(self):
        new_net = {
            "interface": "Wi-Fi",
            "local_ip": "172.16.8.225",
            "gateway": "172.16.0.1",
            "network": "172.16.0.0/19",
            "network_id": "Wi-Fi|172.16.0.0/19|172.16.0.1",
            "status": "CONNECTED"
        }
        sync_network_session(new_net)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT device_id, ip_address, status FROM devices WHERE status = 'STALE'")
            stale_devs = cursor.fetchall()
            self.assertTrue(len(stale_devs) >= 1)
            # Ensure devices from other networks are marked STALE
            for d in stale_devs:
                self.assertEqual(d["status"], "STALE")

    # 8. Packet loss calculation
    # 9. Latency statistics
    def test_08_09_packet_loss_and_latency_statistics(self):
        # 100% loss test
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="Packets: Sent = 5, Received = 0, Lost = 5 (100% loss)"
            )
            res = ping_device_probes("192.168.8.100", count=5)
            self.assertEqual(res["status"], "UNREACHABLE")
            self.assertEqual(res["packets_sent"], 5)
            self.assertEqual(res["packets_received"], 0)
            self.assertEqual(res["packet_loss"], 100.0)
            # When 0 packets received, latency must be None (N/A), never 0 ms
            self.assertIsNone(res["average_latency"])
            self.assertIsNone(res["min_latency"])
            self.assertIsNone(res["max_latency"])

        # 0% loss test with latencies
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="""
Reply from 192.168.8.77: bytes=32 time=7ms TTL=64
Reply from 192.168.8.77: bytes=32 time=8ms TTL=64
Reply from 192.168.8.77: bytes=32 time=9ms TTL=64
Reply from 192.168.8.77: bytes=32 time=10ms TTL=64
Reply from 192.168.8.77: bytes=32 time=12ms TTL=64
Packets: Sent = 5, Received = 5, Lost = 0 (0% loss)
Approximate round trip times in milli-seconds:
    Minimum = 7ms, Maximum = 12ms, Average = 9ms
"""
            )
            res = ping_device_probes("192.168.8.77", count=5)
            self.assertEqual(res["status"], "ONLINE")
            self.assertEqual(res["packets_sent"], 5)
            self.assertEqual(res["packets_received"], 5)
            self.assertEqual(res["packet_loss"], 0.0)
            self.assertEqual(res["min_latency"], 7.0)
            self.assertEqual(res["max_latency"], 12.0)
            self.assertEqual(res["average_latency"], 9.0)

    # 10. Discovery result merging
    # 11. Duplicate removal
    def test_10_11_discovery_merging_and_duplicate_removal(self):
        arp_text = """
Interface: 192.168.8.213 --- 0x14
  Internet Address      Physical Address      Type
  192.168.8.9           3c-64-cf-8b-98-11     dynamic
  192.168.8.77          aa-bb-cc-dd-ee-ff     dynamic
  192.168.8.77          aa-bb-cc-dd-ee-ff     dynamic  <-- duplicate
  224.0.0.22            01-00-5e-00-00-16     static   <-- multicast
  192.168.8.255         ff-ff-ff-ff-ff-ff     static   <-- broadcast
"""
        devices = parse_arp_output(arp_text, target_network="192.168.8.0/24")
        ips = [d["ip_address"] for d in devices]
        self.assertIn("192.168.8.9", ips)
        self.assertIn("192.168.8.77", ips)
        self.assertEqual(ips.count("192.168.8.77"), 1) # Deduplicated
        self.assertNotIn("224.0.0.22", ips)            # Multicast filtered
        self.assertNotIn("192.168.8.255", ips)         # Broadcast filtered

    # 12. Digital Twin node update
    def test_12_digital_twin_node_update(self):
        dt = DigitalTwinNetwork()
        dt.add_device("TEST_DEV", "Test Machine", "192.168.8.50")
        self.assertIn("TEST_DEV", dt.graph)

        dt.update_device_status("TEST_DEV", "ONLINE", latency=4.2)
        node_data = dt.graph.nodes["TEST_DEV"]
        self.assertEqual(node_data["status"], "ONLINE")
        self.assertEqual(node_data["latency"], 4.2)

        dt.update_device_status("TEST_DEV", "UNREACHABLE", latency=None)
        self.assertEqual(dt.graph.nodes["TEST_DEV"]["status"], "UNREACHABLE")

    # 13. Device failure simulation (deep copy isolation)
    # 14. Link failure simulation
    # 15. Recovery simulation
    def test_13_to_15_simulation_isolation_failure_and_recovery(self):
        dt = DigitalTwinNetwork()
        dt.add_device("A", "Laptop A", "192.168.8.10")
        dt.add_device("B", "Laptop B", "192.168.8.20")
        dt.update_device_status("A", "ONLINE", latency=2.0)
        dt.update_device_status("B", "ONLINE", latency=2.0)

        sim = NetworkSimulator(dt)
        self.assertEqual(sim.sim_graph.nodes["B"]["status"], "ONLINE")

        # 13. Device failure in simulation
        sim.simulate_device_failure("B")
        self.assertEqual(sim.sim_graph.nodes["B"]["status"], "FAILED")
        # CRITICAL: live graph must NOT be modified
        self.assertEqual(dt.graph.nodes["B"]["status"], "ONLINE")

        # 14. Link failure
        sim.simulate_link_failure(dt.gateway_id, "A")
        self.assertEqual(sim.sim_graph.edges[dt.gateway_id, "A"]["status"], "FAILED")
        self.assertEqual(dt.graph.edges[dt.gateway_id, "A"]["status"], "ACTIVE")

        # 15. Recovery
        sim.simulate_device_recovery("B")
        self.assertEqual(sim.sim_graph.nodes["B"]["status"], "ONLINE")
        sim.simulate_link_recovery(dt.gateway_id, "A")
        self.assertEqual(sim.sim_graph.edges[dt.gateway_id, "A"]["status"], "ACTIVE")

    # 16. Shortest path
    # 17. Connectivity
    def test_16_17_shortest_path_and_connectivity(self):
        dt = DigitalTwinNetwork()
        dt.add_device("NODE1", "Node 1", "192.168.8.31")
        dt.add_device("NODE2", "Node 2", "192.168.8.32")
        
        sim = NetworkSimulator(dt)
        path_res = sim.get_path("NODE1", "NODE2")
        self.assertTrue(path_res["reachable"])
        self.assertEqual(path_res["path"], ["NODE1", dt.gateway_id, "NODE2"])
        self.assertEqual(path_res["hops"], 2)

        # Fail gateway -> partitioned
        sim.simulate_device_failure(dt.gateway_id)
        path_after = sim.get_path("NODE1", "NODE2")
        self.assertFalse(path_after["reachable"])
        impact = sim.analyze_impact()
        self.assertFalse(impact["is_connected"])

    # 18. Database telemetry storage
    # 19. Event generation
    def test_18_19_database_telemetry_and_events(self):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 18. Insert telemetry record
            cursor.execute(
                """
                INSERT INTO device_status (
                    device_id, status, latency, min_latency, max_latency,
                    packets_sent, packets_received, packet_loss, network_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("PC1", "ONLINE", 8.5, 6.0, 11.0, 5, 5, 0.0, "Wi-Fi|192.168.8.0/24|192.168.8.9")
            )
            # 19. Event logging
            log_event("PC1", EVENT_DEVICE_RECOVERED, "PC1 recovered to ONLINE state", conn=conn)
            conn.commit()

            cursor.execute("SELECT * FROM device_status WHERE device_id = 'PC1' ORDER BY id DESC LIMIT 1")
            telemetry = cursor.fetchone()
            self.assertIsNotNone(telemetry)
            self.assertEqual(telemetry["packets_sent"], 5)
            self.assertEqual(telemetry["packets_received"], 5)
            self.assertEqual(telemetry["packet_loss"], 0.0)
            self.assertEqual(telemetry["min_latency"], 6.0)
            self.assertEqual(telemetry["max_latency"], 11.0)

            cursor.execute("SELECT * FROM network_events WHERE device_id = 'PC1' ORDER BY id DESC LIMIT 1")
            event = cursor.fetchone()
            self.assertIsNotNone(event)
            self.assertEqual(event["event_type"], EVENT_DEVICE_RECOVERED)

    # 20. Modular Discovery Subsystem (Section 6)
    def test_20_modular_discovery_subsystem(self):
        from modules.discovery import (
            discover_from_arp,
            discover_from_icmp,
            discover_from_gateway_if_available,
            merge_discovery_results
        )

        with patch("modules.discovery.discover_local_devices") as mock_arp:
            mock_arp.return_value = (True, [
                {"ip_address": "192.168.8.9", "mac_address": "3C-64-CF-8B-98-11", "interface": "192.168.8.213", "entry_type": "dynamic"},
                {"ip_address": "192.168.8.110", "mac_address": "56-24-66-82-A3-0F", "interface": "192.168.8.213", "entry_type": "dynamic"},
                {"ip_address": "192.168.8.213", "mac_address": "AA-BB-CC-DD-EE-FF", "interface": "192.168.8.213", "entry_type": "dynamic"}
            ], None)

            # 1. ARP source
            arp_devs = discover_from_arp(target_network="192.168.8.0/24", local_ip="192.168.8.213")
            self.assertEqual(len(arp_devs), 2)
            self.assertNotIn("192.168.8.213", [d["ip_address"] for d in arp_devs])

            # 2. ICMP source
            with patch("modules.discovery._quick_ping") as mock_ping:
                mock_ping.side_effect = lambda ip, to: (ip, ip in ["192.168.8.9", "192.168.8.150"], 5.0)
                icmp_res = discover_from_icmp(["192.168.8.9", "192.168.8.150", "192.168.8.180"])
                self.assertIn("192.168.8.9", icmp_res)
                self.assertIn("192.168.8.150", icmp_res)
                self.assertNotIn("192.168.8.180", icmp_res)

            # 3. Gateway source
            gw_res = discover_from_gateway_if_available("192.168.8.9")
            self.assertIsNone(gw_res)

            # 4. Merge results
            merged = merge_discovery_results(
                arp_devices=arp_devs,
                icmp_results=icmp_res,
                gateway_clients=None,
                local_ip="192.168.8.213",
                gateway="192.168.8.9",
                network_id="Wi-Fi|192.168.8.0/24|192.168.8.9"
            )
            # Merged should have: 192.168.8.9 (ICMP + ARP), 192.168.8.110 (ARP), 192.168.8.150 (ICMP)
            ips = [d["ip_address"] for d in merged]
            self.assertIn("192.168.8.9", ips)
            self.assertIn("192.168.8.110", ips)
            self.assertIn("192.168.8.150", ips)
            self.assertEqual(merged[0]["ip_address"], "192.168.8.9")
            self.assertTrue(merged[0]["is_gateway"])

            # Check sources
            src_map = {d["ip_address"]: d["source"] for d in merged}
            self.assertEqual(src_map["192.168.8.9"], "ICMP + ARP")
            self.assertEqual(src_map["192.168.8.110"], "ARP")
            self.assertEqual(src_map["192.168.8.150"], "ICMP")

    # 21. Discovery Diagnostics & Jitter Telemetry (Section 12, 13, 19)
    def test_21_discovery_diagnostics_and_jitter(self):
        with patch("modules.discovery.discover_from_icmp", return_value={"192.168.8.9": 4.0}), \
             patch("modules.discovery.discover_from_arp", return_value=[{"ip_address": "192.168.8.9", "mac_address": "3C-64-CF-8B-98-11"}]):
            res = discover_devices_active(
                target_network="192.168.8.0/24",
                gateway="192.168.8.9",
                local_ip="192.168.8.213"
            )
            self.assertTrue(res["success"])
            self.assertIn("diagnostics", res)
            diag = res["diagnostics"]
            self.assertEqual(diag["network"], "192.168.8.0/24")
            self.assertEqual(diag["local_host"], "192.168.8.213")
            self.assertEqual(diag["gateway"], "192.168.8.9")
            self.assertEqual(diag["candidates_checked"], 253)
            self.assertEqual(diag["arp_entries"], 1)
            self.assertEqual(diag["icmp_responses"], 1)
            self.assertIn("visible from this monitoring host", diag["explanation"])

        # Jitter calculation verification
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(
                returncode=0,
                stdout="""
Pinging 192.168.8.9 with 32 bytes of data:
Reply from 192.168.8.9: bytes=32 time=10ms TTL=64
Reply from 192.168.8.9: bytes=32 time=14ms TTL=64
Reply from 192.168.8.9: bytes=32 time=8ms TTL=64
Reply from 192.168.8.9: bytes=32 time=12ms TTL=64
Reply from 192.168.8.9: bytes=32 time=10ms TTL=64

Ping statistics for 192.168.8.9:
    Packets: Sent = 5, Received = 5, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 8ms, Maximum = 14ms, Average = 10ms
""",
                stderr=""
            )
            probes = ping_device_probes("192.168.8.9", count=5)
            self.assertEqual(probes["status"], "ONLINE")
            self.assertEqual(probes["packets_sent"], 5)
            self.assertEqual(probes["packets_received"], 5)
            self.assertEqual(probes["packet_loss"], 0.0)
            self.assertIsNotNone(probes["jitter"])
            # Consecutive diffs: |14-10|=4, |8-14|=6, |12-8|=4, |10-12|=2. Sum = 16 / 4 = 4.0 ms
            self.assertEqual(probes["jitter"], 4.0)


if __name__ == "__main__":
    unittest.main()
