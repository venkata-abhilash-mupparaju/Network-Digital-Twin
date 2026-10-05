"""
What-If Simulation Engine
-------------------------
Operates on an isolated in-memory deep copy of the Digital Twin graph.
Permits hypothetical testing of device failures, link cuts, recoveries,
and logical topologies (Star, Ring, Mesh).
NEVER mutates the live Digital Twin or sends commands to physical devices.
"""

import copy
import logging
import networkx as nx

from config import HOTSPOT_GATEWAY_ID, HOTSPOT_GATEWAY_NAME
from .routing import RoutingEngine

logger = logging.getLogger(__name__)

class NetworkSimulator:
    def __init__(self, digital_twin=None):
        """
        Initializes the simulation environment.
        Holds an independent copy of the Digital Twin graph.
        """
        self.sim_graph = nx.Graph()
        self.topology_type = "star"  # Default physical topology
        self.failed_nodes = set()
        self.failed_links = set()   # set of tuples (u, v) sorted
        self.simulation_actions_log = []
        
        if digital_twin:
            self.clone_from_digital_twin(digital_twin)

    def clone_from_digital_twin(self, digital_twin):
        """
        Creates an independent deep copy of the live Digital Twin graph.
        Resets any previous simulation changes.
        """
        # nx.Graph.copy() creates a complete copy of nodes, edges, and their attribute dictionaries
        self.sim_graph = digital_twin.graph.copy()
        self.failed_nodes.clear()
        self.failed_links.clear()
        self.topology_type = "star"
        self._log_action("CLONE", "Cloned fresh state from live Digital Twin graph.")
        logger.info("Simulation initialized with %d nodes and %d edges.", 
                    self.sim_graph.number_of_nodes(), self.sim_graph.number_of_edges())

    def _log_action(self, action_type, description):
        self.simulation_actions_log.append({
            "action": action_type,
            "description": description
        })

    def simulate_device_failure(self, device_id):
        """
        Hypothetically fails a device in the simulation.
        Marks node as FAILED and its connected edges as FAILED.
        """
        if device_id not in self.sim_graph:
            return False, f"Device '{device_id}' does not exist in simulation."

        self.sim_graph.nodes[device_id]["status"] = "FAILED"
        self.failed_nodes.add(device_id)

        # Mark all incident links as FAILED
        for neighbor in list(self.sim_graph.neighbors(device_id)):
            self.sim_graph.edges[device_id, neighbor]["status"] = "FAILED"
            link_key = tuple(sorted([device_id, neighbor]))
            self.failed_links.add(link_key)

        msg = f"Simulated failure of device: {device_id}."
        self._log_action("DEVICE_FAILURE", msg)
        logger.info(msg)
        return True, msg

    def simulate_device_recovery(self, device_id):
        """
        Restores a failed device back to operational ONLINE status in the simulation.
        """
        if device_id not in self.sim_graph:
            return False, f"Device '{device_id}' does not exist in simulation."

        self.sim_graph.nodes[device_id]["status"] = "ONLINE"
        if device_id in self.failed_nodes:
            self.failed_nodes.remove(device_id)

        # Restore incident links where the other endpoint is also operational
        for neighbor in list(self.sim_graph.neighbors(device_id)):
            if neighbor not in self.failed_nodes and self.sim_graph.nodes[neighbor].get("status") != "FAILED":
                self.sim_graph.edges[device_id, neighbor]["status"] = "ACTIVE"
                link_key = tuple(sorted([device_id, neighbor]))
                if link_key in self.failed_links:
                    self.failed_links.remove(link_key)

        msg = f"Simulated recovery of device: {device_id}."
        self._log_action("DEVICE_RECOVERY", msg)
        logger.info(msg)
        return True, msg

    def simulate_link_failure(self, u, v):
        """
        Simulates the severing of a physical/wireless link between node u and node v.
        """
        if not self.sim_graph.has_edge(u, v):
            return False, f"No link exists between '{u}' and '{v}'."

        self.sim_graph.edges[u, v]["status"] = "FAILED"
        link_key = tuple(sorted([u, v]))
        self.failed_links.add(link_key)

        msg = f"Simulated failure of link between {u} and {v}."
        self._log_action("LINK_FAILURE", msg)
        logger.info(msg)
        return True, msg

    def simulate_link_recovery(self, u, v):
        """
        Restores a failed link between node u and node v.
        """
        if not self.sim_graph.has_edge(u, v):
            return False, f"No link exists between '{u}' and '{v}'."

        self.sim_graph.edges[u, v]["status"] = "ACTIVE"
        link_key = tuple(sorted([u, v]))
        if link_key in self.failed_links:
            self.failed_links.remove(link_key)

        msg = f"Simulated recovery of link between {u} and {v}."
        self._log_action("LINK_RECOVERY", msg)
        logger.info(msg)
        return True, msg

    def set_simulation_topology(self, topology_name):
        """
        Reconfigures the simulated logical topology to Star, Ring, or Mesh.
        Maintains the existing nodes, but rebuilds edges to demonstrate
        how alternative architectural topologies react to failure.
        """
        topo = topology_name.lower().strip()
        nodes = list(self.sim_graph.nodes())
        
        if len(nodes) < 2:
            return False, "Insufficient nodes to build alternative topology."

        # Remove existing edges in simulation
        self.sim_graph.clear_edges()
        self.failed_links.clear()

        gateway = HOTSPOT_GATEWAY_ID if HOTSPOT_GATEWAY_ID in self.sim_graph else nodes[0]
        other_nodes = [n for n in nodes if n != gateway]

        if topo == "star":
            # Star topology: Gateway connects to every peripheral node
            for node in other_nodes:
                self.sim_graph.add_edge(gateway, node, id=f"link_{gateway}_{node}", link_type="wireless", status="ACTIVE", latency=2.0)
            self.topology_type = "star"

        elif topo == "ring":
            # Ring topology: Circular loop connecting all nodes in sequence
            ring_order = [gateway] + other_nodes
            n_count = len(ring_order)
            for i in range(n_count):
                u = ring_order[i]
                v = ring_order[(i + 1) % n_count]
                self.sim_graph.add_edge(u, v, id=f"link_{u}_{v}", link_type="ring_link", status="ACTIVE", latency=3.5)
            self.topology_type = "ring"

        elif topo == "mesh":
            # Mesh topology: Highly redundant links among all nodes
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    u = nodes[i]
                    v = nodes[j]
                    self.sim_graph.add_edge(u, v, id=f"link_{u}_{v}", link_type="mesh_link", status="ACTIVE", latency=2.5)
            self.topology_type = "mesh"

        else:
            return False, f"Unsupported topology '{topology_name}'. Choose star, ring, or mesh."

        # Re-apply any active failed nodes to the new edges
        for fn in self.failed_nodes:
            for neighbor in self.sim_graph.neighbors(fn):
                self.sim_graph.edges[fn, neighbor]["status"] = "FAILED"
                self.failed_links.add(tuple(sorted([fn, neighbor])))

        msg = f"Simulation topology reconfigured to: {topo.upper()} (SIMULATION ONLY)."
        self._log_action("TOPOLOGY_CHANGE", msg)
        return True, msg

    def analyze_impact(self):
        """
        Performs graph connectivity and partition analysis under the simulated condition.
        Evaluates:
        - Network connectivity
        - Isolated nodes
        - Unreachable nodes from gateway
        - Connected components count
        """
        # Build active operational subgraph
        active_subgraph = nx.Graph()
        for node, data in self.sim_graph.nodes(data=True):
            if data.get("status") not in ["FAILED", "OFFLINE"]:
                active_subgraph.add_node(node, **data)

        for u, v, data in self.sim_graph.edges(data=True):
            if (u in active_subgraph and v in active_subgraph and 
                data.get("status") not in ["FAILED", "INACTIVE"]):
                active_subgraph.add_edge(u, v, **data)

        total_nodes = self.sim_graph.number_of_nodes()
        active_nodes_count = active_subgraph.number_of_nodes()
        
        # Check connected components of active subgraph
        components = list(nx.connected_components(active_subgraph)) if active_nodes_count > 0 else []
        is_fully_connected = (len(components) == 1 and active_nodes_count == total_nodes)

        # Check reachability from gateway
        gateway = HOTSPOT_GATEWAY_ID if HOTSPOT_GATEWAY_ID in self.sim_graph else None
        unreachable_from_gw = []
        if gateway and gateway in active_subgraph:
            reachable_nodes = nx.descendants(active_subgraph, gateway) | {gateway}
            for n in self.sim_graph.nodes():
                if n not in reachable_nodes:
                    unreachable_from_gw.append(n)
        elif gateway:
            # Gateway itself failed: ALL other nodes are unreachable from gateway!
            unreachable_from_gw = [n for n in self.sim_graph.nodes() if n != gateway]

        # Isolated nodes (degree 0 in active graph)
        isolated_nodes = [n for n in self.sim_graph.nodes() if n not in active_subgraph or active_subgraph.degree(n) == 0]

        return {
            "topology_type": self.topology_type.upper(),
            "total_nodes": total_nodes,
            "active_nodes": active_nodes_count,
            "failed_nodes": list(self.failed_nodes),
            "failed_links": [f"{u} <-> {v}" for u, v in self.failed_links],
            "connected_components_count": len(components),
            "is_fully_connected": is_fully_connected,
            "is_connected": is_fully_connected,
            "isolated_nodes": isolated_nodes,
            "unreachable_from_gateway": unreachable_from_gw,
            "impact_severity": "CRITICAL" if not is_fully_connected or len(self.failed_nodes) > 0 else "NORMAL"
        }

    def get_path(self, source, target):
        """Delegates to the routing engine on the simulated graph."""
        return RoutingEngine.calculate_shortest_path(self.sim_graph, source, target)

    def get_distance_vector(self, node_id):
        """Delegates to distance vector routing table generator on simulated graph."""
        return RoutingEngine.compute_distance_vector_table(self.sim_graph, node_id)

    def to_cytoscape_elements(self):
        """Exports the simulated graph elements for Cytoscape.js visualization."""
        elements = {"nodes": [], "edges": []}
        
        impact = self.analyze_impact()
        unreachable_set = set(impact["unreachable_from_gateway"])

        for node_id, data in self.sim_graph.nodes(data=True):
            status = data.get("status", "ONLINE")
            is_unreachable = node_id in unreachable_set and status != "FAILED"
            
            elements["nodes"].append({
                "data": {
                    "id": str(node_id),
                    "label": data.get("name", str(node_id)),
                    "ip": data.get("ip", ""),
                    "type": data.get("type", "Computer"),
                    "status": status,
                    "is_simulated_failed": status == "FAILED",
                    "is_unreachable": is_unreachable,
                    "latency": data.get("latency")
                }
            })

        for u, v, data in self.sim_graph.edges(data=True):
            edge_id = data.get("id", f"sim_edge_{u}_{v}")
            status = data.get("status", "ACTIVE")
            elements["edges"].append({
                "data": {
                    "id": edge_id,
                    "source": str(u),
                    "target": str(v),
                    "link_type": data.get("link_type", "wireless"),
                    "status": status,
                    "is_simulated_failed": status == "FAILED"
                }
            })

        return elements
