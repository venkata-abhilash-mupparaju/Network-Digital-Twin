import os
import sys
import unittest
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app, digital_twin, simulator
from modules.monitor import validate_ip, ping_host
from modules.network_model import DigitalTwinNetwork
from modules.simulator import NetworkSimulator
from modules.routing import RoutingEngine

class TestNetworkDigitalTwinFull(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_ip_validation(self):
        """Test strict IPv4 validation."""
        self.assertTrue(validate_ip("192.168.43.1"))
        self.assertTrue(validate_ip("127.0.0.1"))
        self.assertTrue(validate_ip("10.0.0.5"))
        self.assertFalse(validate_ip("999.999.999.999"))
        self.assertFalse(validate_ip("192.168.1"))
        self.assertFalse(validate_ip("google.com"))
        self.assertFalse(validate_ip("; rm -rf /"))
        self.assertFalse(validate_ip(""))

    def test_ping_host_loopback(self):
        """Test ICMP ping on local loopback."""
        is_online, latency, err = ping_host("127.0.0.1", timeout_seconds=1)
        self.assertTrue(is_online)
        self.assertIsNotNone(latency)
        self.assertGreaterEqual(latency, 0.0)

    def test_simulation_fault_injection_and_isolation(self):
        """Verify that simulation actions NEVER mutate the live Digital Twin."""
        live_model = DigitalTwinNetwork("TEST_GW", "Gateway")
        live_model.add_device("LAP_1", "Laptop 1", "192.168.43.101")
        live_model.add_device("LAP_2", "Laptop 2", "192.168.43.102")
        live_model.update_device_status("LAP_1", "ONLINE", 12.0)
        live_model.update_device_status("LAP_2", "ONLINE", 15.0)

        # Create simulator
        sim = NetworkSimulator(live_model)
        
        # Verify initial clone
        self.assertEqual(sim.sim_graph.nodes["LAP_1"]["status"], "ONLINE")
        
        # Simulate failure of LAP_1
        success, msg = sim.simulate_device_failure("LAP_1")
        self.assertTrue(success)
        self.assertEqual(sim.sim_graph.nodes["LAP_1"]["status"], "FAILED")
        
        # CRITICAL TEST: Live Digital Twin MUST REMAIN ONLINE!
        self.assertEqual(live_model.graph.nodes["LAP_1"]["status"], "ONLINE")

        # Test impact analysis
        impact = sim.analyze_impact()
        self.assertIn("LAP_1", impact["failed_nodes"])
        self.assertEqual(impact["impact_severity"], "CRITICAL")

        # Simulate recovery
        rec_success, _ = sim.simulate_device_recovery("LAP_1")
        self.assertTrue(rec_success)
        self.assertEqual(sim.sim_graph.nodes["LAP_1"]["status"], "ONLINE")

    def test_simulation_topologies(self):
        """Test switching logical topologies (Star, Ring, Mesh)."""
        live_model = DigitalTwinNetwork("GW", "Gateway")
        live_model.add_device("N1", "Node 1", "192.168.43.10")
        live_model.add_device("N2", "Node 2", "192.168.43.11")
        live_model.add_device("N3", "Node 3", "192.168.43.12")
        
        sim = NetworkSimulator(live_model)
        
        # Star: 4 nodes, 3 edges
        self.assertEqual(sim.sim_graph.number_of_edges(), 3)

        # Switch to Ring: 4 nodes, 4 edges
        sim.set_simulation_topology("ring")
        self.assertEqual(sim.topology_type, "ring")
        self.assertEqual(sim.sim_graph.number_of_edges(), 4)

        # Switch to Mesh: 4 nodes, (4*3)/2 = 6 edges
        sim.set_simulation_topology("mesh")
        self.assertEqual(sim.topology_type, "mesh")
        self.assertEqual(sim.sim_graph.number_of_edges(), 6)

    def test_routing_shortest_path_and_dv(self):
        """Test Dijkstra Shortest Path and Distance Vector table."""
        live_model = DigitalTwinNetwork("GW", "Gateway")
        live_model.add_device("PC_A", "PC A", "192.168.43.10")
        live_model.add_device("PC_B", "PC B", "192.168.43.20")
        live_model.update_device_status("PC_A", "ONLINE", 2.0)
        live_model.update_device_status("PC_B", "ONLINE", 2.0)

        sim = NetworkSimulator(live_model)
        path_res = sim.get_path("PC_A", "PC_B")
        self.assertTrue(path_res["found"])
        self.assertEqual(path_res["path"], ["PC_A", "GW", "PC_B"])
        self.assertEqual(path_res["hop_count"], 2)

        # If GW fails, path should become unavailable
        sim.simulate_device_failure("GW")
        broken_path = sim.get_path("PC_A", "PC_B")
        self.assertFalse(broken_path["found"])

        # Distance vector routing table test
        dv_table = sim.get_distance_vector("PC_A")
        self.assertTrue(any(row["destination"] == "PC_B" for row in dv_table))

    def test_web_routes_and_pages(self):
        """Test that all frontend pages render 200 OK."""
        for route in ["/", "/devices", "/simulation", "/history"]:
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 200, f"Failed loading {route}")

    def test_device_crud_api(self):
        """Test adding, editing, and deleting a device."""
        test_dev = {
            "device_id": "TEST_NODE_99",
            "device_name": "Unit Test Machine",
            "ip_address": "192.168.43.99",
            "device_type": "Server",
            "description": "Created by automated test"
        }
        # Add
        post_resp = self.client.post("/api/devices", json=test_dev)
        self.assertIn(post_resp.status_code, [201, 200])

        # Verify in Digital Twin
        self.assertIn("TEST_NODE_99", digital_twin.graph.nodes)

        # Edit
        edit_payload = {
            "device_name": "Updated Test Machine",
            "ip_address": "192.168.43.99",
            "device_type": "Server",
            "description": "Updated description"
        }
        put_resp = self.client.put("/api/devices/TEST_NODE_99", json=edit_payload)
        self.assertEqual(put_resp.status_code, 200)

        # Delete
        del_resp = self.client.delete("/api/devices/TEST_NODE_99")
        self.assertEqual(del_resp.status_code, 200)
        self.assertNotIn("TEST_NODE_99", digital_twin.graph.nodes)

if __name__ == "__main__":
    unittest.main()
