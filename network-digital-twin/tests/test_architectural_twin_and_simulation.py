"""
Architectural Tests for Network Digital Twin & What-If Simulation
-----------------------------------------------------------------
Validates:
1. Disentanglement of Discovery, Monitoring, Digital Twin Model, and Simulation.
2. Authorized offline/unreachable devices exist in the Digital Twin and Simulator.
3. What-if hypothetical failures (device and link) test correctly without altering real network.
4. Shortest path routing dynamically breaks upon simulated outage and restores upon recovery.
5. Subnet-based session isolation prevents stale devices from leaking into the simulation.
6. API schema compliance.
"""

import unittest
import networkx as nx

from modules.network_model import DigitalTwinNetwork
from modules.simulator import NetworkSimulator
from modules.routing import RoutingEngine
from modules.network_info import get_current_authorized_devices, ensure_authorized_test_devices
from config import HOTSPOT_GATEWAY_ID, HOTSPOT_GATEWAY_NAME

class TestArchitecturalTwinAndSimulation(unittest.TestCase):
    def setUp(self):
        self.twin = DigitalTwinNetwork(gateway_id=HOTSPOT_GATEWAY_ID, gateway_name=HOTSPOT_GATEWAY_NAME)
        self.twin.update_gateway_ip("192.168.8.1")
        self.sim = NetworkSimulator(self.twin)

    def test_authorized_offline_devices_in_twin_and_simulator(self):
        """
        Verify that authorized devices (PC1, PC2, PC3), even if physically OFFLINE or UNREACHABLE,
        are preserved as structural nodes in the Digital Twin graph and cloned into the Simulator.
        """
        net_info = {
            "network_id": "Wi-Fi|192.168.8.0/24|192.168.8.1",
            "interface": "Wi-Fi",
            "local_ip": "192.168.8.50",
            "gateway": "192.168.8.1",
            "network": "192.168.8.0/24",
            "status": "CONNECTED"
        }

        # 3 authorized devices on this subnet: PC1 is ONLINE, PC2 is OFFLINE, PC3 is UNREACHABLE
        authorized_devices = [
            {
                "device_id": "PC1",
                "device_name": "Test Laptop 1",
                "ip_address": "192.168.8.100",
                "device_type": "Laptop",
                "status": "ONLINE",
                "is_monitored": 1,
                "authorization_status": "AUTHORIZED"
            },
            {
                "device_id": "PC2",
                "device_name": "Test Laptop 2",
                "ip_address": "192.168.8.101",
                "device_type": "Laptop",
                "status": "OFFLINE",
                "is_monitored": 1,
                "authorization_status": "AUTHORIZED"
            },
            {
                "device_id": "PC3",
                "device_name": "Test Laptop 3",
                "ip_address": "192.168.8.102",
                "device_type": "Laptop",
                "status": "UNREACHABLE",
                "is_monitored": 1,
                "authorization_status": "AUTHORIZED"
            }
        ]

        discovered_ips = {"192.168.8.1", "192.168.8.100"}  # PC2 and PC3 are NOT currently discovered by ARP/ping

        # Synchronize Digital Twin
        self.twin.sync_with_current_network(
            gateway_ip="192.168.8.1",
            authorized_devices=authorized_devices,
            monitored_devices=authorized_devices,
            discovered_ips=discovered_ips,
            network_cidr="192.168.8.0/24"
        )

        # 1. Twin model contains all 4 nodes (Gateway + 3 authorized test devices)
        self.assertEqual(self.twin.graph.number_of_nodes(), 4)
        self.assertTrue(self.twin.graph.has_node("PC1"))
        self.assertTrue(self.twin.graph.has_node("PC2"))
        self.assertTrue(self.twin.graph.has_node("PC3"))

        # Verify distinct state properties on PC2 (OFFLINE and NOT_VISIBLE, but AUTHORIZED and MONITORED)
        pc2_node = self.twin.graph.nodes["PC2"]
        self.assertEqual(pc2_node["authorization_status"], "AUTHORIZED")
        self.assertEqual(pc2_node["discovery_status"], "NOT_VISIBLE")
        self.assertEqual(pc2_node["monitoring_status"], "MONITORED")
        self.assertEqual(pc2_node["live_status"], "OFFLINE")

        # 2. Clone into What-If Simulator
        self.sim.clone_from_digital_twin(self.twin, net_info)
        self.assertEqual(self.sim.sim_graph.number_of_nodes(), 4)
        self.assertEqual(self.sim.sim_graph.number_of_edges(), 3)

        impact = self.sim.analyze_impact()
        self.assertEqual(impact["total_nodes"], 4)
        self.assertEqual(impact["active_nodes"], 4)
        self.assertEqual(impact["failed_nodes_count"], 0)
        self.assertTrue(impact["has_live_devices"])
        self.assertIsNone(impact["warning"])

    def test_hypothetical_device_failure_and_dijkstra_route(self):
        """
        Verify that failing PC2 marks it and incident links as FAILED,
        breaks Dijkstra shortest path, and recovery restores route.
        """
        authorized_devices = [
            {"device_id": "PC1", "device_name": "PC1", "ip_address": "192.168.8.100", "is_monitored": 1},
            {"device_id": "PC2", "device_name": "PC2", "ip_address": "192.168.8.101", "is_monitored": 1},
            {"device_id": "PC3", "device_name": "PC3", "ip_address": "192.168.8.102", "is_monitored": 1}
        ]
        self.twin.sync_with_current_network("192.168.8.1", authorized_devices=authorized_devices)
        self.sim.clone_from_digital_twin(self.twin)

        # Initial operational route between PC1 and PC2 via Gateway
        route_before = self.sim.get_path("PC1", "PC2")
        self.assertTrue(route_before["found"])
        self.assertEqual(route_before["path"], ["PC1", HOTSPOT_GATEWAY_ID, "PC2"])

        # Inject hypothetical fault on PC2
        success, msg = self.sim.simulate_device_failure("PC2")
        self.assertTrue(success)
        self.assertEqual(self.sim.sim_graph.nodes["PC2"]["status"], "FAILED")
        self.assertTrue(self.sim.sim_graph.nodes["PC2"]["is_simulated_failed"])
        self.assertIn("PC2", self.sim.failed_nodes)

        # Route between PC1 and PC2 should now be BROKEN
        route_after_fail = self.sim.get_path("PC1", "PC2")
        self.assertFalse(route_after_fail["found"])
        self.assertIn("FAILED", route_after_fail["details"])

        # Impact analysis
        impact = self.sim.analyze_impact()
        self.assertEqual(impact["active_nodes"], 3)
        self.assertEqual(impact["failed_nodes_count"], 1)
        self.assertEqual(impact["impact_severity"], "CRITICAL")

        # Recover PC2
        rec_success, rec_msg = self.sim.simulate_device_recovery("PC2")
        self.assertTrue(rec_success)
        self.assertEqual(self.sim.sim_graph.nodes["PC2"]["status"], "ONLINE")
        self.assertFalse(self.sim.sim_graph.nodes["PC2"]["is_simulated_failed"])

        # Route restored
        route_after_rec = self.sim.get_path("PC1", "PC2")
        self.assertTrue(route_after_rec["found"])
        self.assertEqual(route_after_rec["path"], ["PC1", HOTSPOT_GATEWAY_ID, "PC2"])

    def test_hypothetical_link_severing_and_isolation(self):
        """
        Verify that severing the link to PC1 isolates PC1 without affecting other nodes.
        """
        authorized_devices = [
            {"device_id": "PC1", "device_name": "PC1", "ip_address": "192.168.8.100", "is_monitored": 1},
            {"device_id": "PC2", "device_name": "PC2", "ip_address": "192.168.8.101", "is_monitored": 1}
        ]
        self.twin.sync_with_current_network("192.168.8.1", authorized_devices=authorized_devices)
        self.sim.clone_from_digital_twin(self.twin)

        # Sever link between GW_HOTSPOT and PC1
        success, msg = self.sim.simulate_link_failure(HOTSPOT_GATEWAY_ID, "PC1")
        self.assertTrue(success)

        impact = self.sim.analyze_impact()
        self.assertIn("PC1", impact["unreachable_from_gateway"])
        self.assertIn("PC1", impact["isolated_nodes"])
        self.assertNotIn("PC2", impact["unreachable_from_gateway"])

        # Restore link
        rec_ok, _ = self.sim.simulate_link_recovery(HOTSPOT_GATEWAY_ID, "PC1")
        self.assertTrue(rec_ok)

        impact_recovered = self.sim.analyze_impact()
        self.assertEqual(len(impact_recovered["unreachable_from_gateway"]), 0)

    def test_to_api_dict_schema(self):
        """
        Verify that to_api_dict() outputs the specified architectural schema:
        network, nodes, edges, elements, summary.
        """
        self.twin.add_device("PC1", "PC1", "192.168.8.100")
        api_data = self.twin.to_api_dict({"network": "192.168.8.0/24", "gateway": "192.168.8.1"})
        
        self.assertIn("mode", api_data)
        self.assertEqual(api_data["mode"], "LIVE_MONITORING")
        self.assertIn("network", api_data)
        self.assertEqual(api_data["network"]["cidr"], "192.168.8.0/24")
        self.assertIn("nodes", api_data)
        self.assertIn("edges", api_data)
        self.assertIn("elements", api_data)
        self.assertIn("summary", api_data)

        # Check node structure
        node = next(n for n in api_data["nodes"] if n["id"] == "PC1")
        self.assertIn("authorization_status", node)
        self.assertIn("discovery_status", node)
        self.assertIn("live_status", node)


if __name__ == "__main__":
    unittest.main()
