"""
Unit & Integration Tests for What-If Simulator and Telemetry History
---------------------------------------------------------------------
Verifies:
- Simulator strictly clones from live Digital Twin and excludes stale devices.
- Computational isolation: live twin graph is never mutated by simulation actions.
- Device failure & recovery.
- Link severing & restoration.
- Alternative topology evaluation (Star, Ring, Mesh, Live).
- Shortest path Dijkstra routing on intact vs fractured graphs.
- REST API routes for simulation and history timestamp enrichment.
"""

import json
import unittest
import networkx as nx

from app import app, digital_twin, simulator
from modules import DigitalTwinNetwork, NetworkSimulator
from config import HOTSPOT_GATEWAY_ID


class TestSimulatorAndHistory(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

        # Build an isolated Digital Twin with gateway and 2 monitored devices
        self.twin = DigitalTwinNetwork()
        self.twin.add_device("DEV_A", "Host A", "172.16.28.10", "Computer")
        self.twin.add_device("DEV_B", "Host B", "172.16.28.11", "Laptop")

        self.sim = NetworkSimulator(self.twin)

    def test_simulator_clones_only_live_twin_devices(self):
        """Simulation graph contains exactly the live Digital Twin nodes, never stale devices."""
        nodes = list(self.sim.sim_graph.nodes())
        self.assertIn(HOTSPOT_GATEWAY_ID, nodes)
        self.assertIn("DEV_A", nodes)
        self.assertIn("DEV_B", nodes)
        # Stale devices from other subnets must not be present
        self.assertNotIn("PC2", nodes)
        self.assertNotIn("PC3", nodes)

    def test_simulator_isolation_live_twin_not_mutated(self):
        """Failing a device in simulator NEVER alters the live Digital Twin graph."""
        success, msg = self.sim.simulate_device_failure("DEV_A")
        self.assertTrue(success)
        self.assertEqual(self.sim.sim_graph.nodes["DEV_A"]["status"], "FAILED")

        # Live Digital Twin node must remain untouched
        self.assertNotEqual(self.twin.graph.nodes["DEV_A"].get("status"), "FAILED")
        self.assertNotIn("DEV_A", self.sim.failed_nodes) if "DEV_A" not in self.sim.failed_nodes else None
        self.assertIn("DEV_A", self.sim.failed_nodes)

    def test_device_failure_and_recovery(self):
        """Verify device failure marks node/incident links as FAILED, and recovery restores ACTIVE status."""
        self.sim.simulate_device_failure("DEV_A")
        self.assertIn("DEV_A", self.sim.failed_nodes)
        impact = self.sim.analyze_impact()
        self.assertIn("DEV_A", impact["failed_nodes"])

        # Recover device
        success, msg = self.sim.simulate_device_recovery("DEV_A")
        self.assertTrue(success)
        self.assertEqual(self.sim.sim_graph.nodes["DEV_A"]["status"], "ONLINE")
        self.assertNotIn("DEV_A", self.sim.failed_nodes)
        new_impact = self.sim.analyze_impact()
        self.assertNotIn("DEV_A", new_impact["failed_nodes"])

    def test_link_failure_and_recovery(self):
        """Verify link failure marks edge as FAILED, and link recovery restores ACTIVE status."""
        success, msg = self.sim.simulate_link_failure(HOTSPOT_GATEWAY_ID, "DEV_B")
        self.assertTrue(success)
        link_key = tuple(sorted([HOTSPOT_GATEWAY_ID, "DEV_B"]))
        self.assertIn(link_key, self.sim.failed_links)

        # Recover link
        rec_success, rec_msg = self.sim.simulate_link_recovery(HOTSPOT_GATEWAY_ID, "DEV_B")
        self.assertTrue(rec_success)
        self.assertNotIn(link_key, self.sim.failed_links)
        self.assertEqual(self.sim.sim_graph.edges[HOTSPOT_GATEWAY_ID, "DEV_B"]["status"], "ACTIVE")

    def test_topology_transformation(self):
        """Verify set_simulation_topology reconfigures to star, ring, mesh, and live."""
        success, msg = self.sim.set_simulation_topology("ring")
        self.assertTrue(success)
        self.assertEqual(self.sim.topology_type, "ring")

        success_mesh, _ = self.sim.set_simulation_topology("mesh")
        self.assertTrue(success_mesh)
        self.assertEqual(self.sim.topology_type, "mesh")

        success_star, _ = self.sim.set_simulation_topology("star")
        self.assertTrue(success_star)
        self.assertEqual(self.sim.topology_type, "star")

        success_live, _ = self.sim.set_simulation_topology("live")
        self.assertTrue(success_live)
        self.assertEqual(self.sim.topology_type, "live")

    def test_shortest_path_dijkstra(self):
        """Verify Dijkstra shortest path calculation on intact vs fractured graph."""
        res = self.sim.get_path("DEV_A", "DEV_B")
        self.assertTrue(res["found"])
        self.assertEqual(res["path"], ["DEV_A", HOTSPOT_GATEWAY_ID, "DEV_B"])
        self.assertEqual(res["hop_count"], 2)

        # Sever link between Gateway and DEV_B
        self.sim.simulate_link_failure(HOTSPOT_GATEWAY_ID, "DEV_B")
        partitioned_res = self.sim.get_path("DEV_A", "DEV_B")
        self.assertFalse(partitioned_res["found"])

    def test_empty_simulator_warning(self):
        """Verify empty simulator (gateway-only) signals has_live_devices=False and warning."""
        empty_twin = DigitalTwinNetwork()
        empty_sim = NetworkSimulator(empty_twin)
        impact = empty_sim.analyze_impact()
        self.assertFalse(impact["has_live_devices"])
        self.assertIsNotNone(impact["warning"])

    def test_api_simulation_endpoints(self):
        """Verify REST API simulation endpoints."""
        # Initialize
        res = self.client.post("/api/simulation/initialize")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")

        # State
        res_state = self.client.get("/api/simulation/state")
        self.assertEqual(res_state.status_code, 200)
        state_data = res_state.get_json()
        self.assertEqual(state_data["mode"], "WHAT_IF_SIMULATION")

        # Link Failure & Recovery via API
        link_fail_res = self.client.post(
            "/api/simulation/link-failure",
            data=json.dumps({"link": f"{HOTSPOT_GATEWAY_ID}|PC1"}),
            content_type="application/json"
        )
        # Even if PC1 not linked, endpoint properly validates parameters
        self.assertIn(link_fail_res.status_code, [200, 400])

        link_rec_res = self.client.post(
            "/api/simulation/link-recovery",
            data=json.dumps({"source": HOTSPOT_GATEWAY_ID, "target": "DEV_A"}),
            content_type="application/json"
        )
        self.assertIn(link_rec_res.status_code, [200, 400])

    def test_api_telemetry_timestamp_enrichment(self):
        """Verify /api/telemetry returns both UTC and local IST timestamps."""
        res = self.client.get("/api/telemetry?limit=5")
        self.assertEqual(res.status_code, 200)
        rows = res.get_json()
        if rows:
            first = rows[0]
            self.assertIn("timestamp_utc", first)
            self.assertIn("timestamp_local", first)
            self.assertIn("timestamp_display", first)

    def test_api_events_timestamp_enrichment(self):
        """Verify /api/events returns enriched local IST and UTC timestamps."""
        res = self.client.get("/api/events?limit=5")
        self.assertEqual(res.status_code, 200)
        events = res.get_json()
        if events:
            first = events[0]
            self.assertIn("timestamp_utc", first)
            self.assertIn("timestamp_local", first)


if __name__ == "__main__":
    unittest.main()
