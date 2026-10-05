import os
import sys
import unittest

# Ensure the project root is in the python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app, digital_twin
from database import get_db_connection
from modules import DigitalTwinNetwork

class TestPhase1(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_database_tables_exist(self):
        """Verify that SQLite tables were created properly."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row["name"] for row in cursor.fetchall()]
            self.assertIn("devices", tables)
            self.assertIn("device_status", tables)
            self.assertIn("network_events", tables)

    def test_networkx_graph_model(self):
        """Verify NetworkX Digital Twin graph functionality."""
        model = DigitalTwinNetwork(gateway_id="TEST_GW", gateway_name="Test Gateway")
        summary = model.get_summary()
        self.assertEqual(summary["total_nodes"], 1)  # Only gateway
        
        # Add a node
        model.add_device("NODE_A", "Node A", "192.168.43.10", "Computer")
        summary = model.get_summary()
        self.assertEqual(summary["total_nodes"], 2)
        self.assertEqual(summary["total_edges"], 1)
        self.assertTrue(summary["is_connected"])
        
        # Update node status
        model.update_device_status("NODE_A", "ONLINE", 15.5)
        self.assertEqual(model.graph.nodes["NODE_A"]["status"], "ONLINE")
        self.assertEqual(model.graph.nodes["NODE_A"]["latency"], 15.5)

        # Export Cytoscape elements
        elements = model.to_cytoscape_elements()
        self.assertEqual(len(elements["nodes"]), 2)
        self.assertEqual(len(elements["edges"]), 1)

    def test_dashboard_endpoint(self):
        """Verify GET / returns 200 and loads HTML dashboard."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NET-TWIN", response.data)
        self.assertIn(b"LIVE MONITORING MODE", response.data)

    def test_api_status_endpoint(self):
        """Verify GET /api/status returns JSON summary."""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "operational")
        self.assertEqual(data["mode"], "LIVE_MONITORING")
        self.assertIn("summary", data)

    def test_api_network_endpoint(self):
        """Verify GET /api/network returns graph elements."""
        response = self.client.get("/api/network")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("elements", data)
        self.assertIn("nodes", data["elements"])
        self.assertIn("edges", data["elements"])

if __name__ == "__main__":
    unittest.main()
