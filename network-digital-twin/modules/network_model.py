"""
Digital Twin Network Model
--------------------------
Maintains an in-memory graph representation of the physical network using NetworkX.
Every node represents a network device, and every edge represents a physical or logical link.
"""

import networkx as nx
import logging
from config import HOTSPOT_GATEWAY_ID, HOTSPOT_GATEWAY_NAME, HOTSPOT_GATEWAY_TYPE

logger = logging.getLogger(__name__)

class DigitalTwinNetwork:
    def __init__(self, gateway_id=HOTSPOT_GATEWAY_ID, gateway_name=HOTSPOT_GATEWAY_NAME):
        """
        Initialize the Digital Twin in-memory graph.
        By default, the physical test network uses a mobile hotspot,
        which forms a star topology with the gateway router at the center.
        """
        self.graph = nx.Graph()
        self.gateway_id = gateway_id
        self.gateway_name = gateway_name
        self._init_gateway()

    def _init_gateway(self):
        """Adds the central Mobile Hotspot Gateway to the graph."""
        self.graph.add_node(
            self.gateway_id,
            device_id=self.gateway_id,
            name=self.gateway_name,
            ip="192.168.43.1",  # Standard Android/iOS hotspot default IP
            type=HOTSPOT_GATEWAY_TYPE,
            status="ONLINE",
            latency=1.0,
            description="Central Access Point / Router"
        )
        logger.info("Gateway node initialized in Digital Twin: %s", self.gateway_id)

    def update_gateway_ip(self, gateway_ip, gateway_name=None):
        """Updates the central gateway node IP and name to reflect the active network."""
        if self.gateway_id in self.graph:
            if gateway_ip:
                self.graph.nodes[self.gateway_id]["ip"] = gateway_ip
            if gateway_name:
                self.graph.nodes[self.gateway_id]["name"] = gateway_name
            logger.info("Updated gateway in Digital Twin: IP=%s", gateway_ip)
            return True
        return False

    def add_device(self, device_id, name, ip, device_type="Computer", description=""):
        """
        Adds or updates a monitored device node in the Digital Twin graph,
        and establishes an edge to the central gateway (hotspot star topology).
        """
        self.graph.add_node(
            device_id,
            device_id=device_id,
            name=name,
            ip=ip,
            type=device_type,
            status="UNKNOWN",
            latency=None,
            description=description
        )
        # In the physical hotspot network, devices connect directly to the Gateway
        if device_id != self.gateway_id:
            self.graph.add_edge(
                self.gateway_id,
                device_id,
                id=f"link_{self.gateway_id}_{device_id}",
                link_type="wireless",
                status="ACTIVE",
                latency=None
            )
        logger.info("Device %s (%s) mapped to Digital Twin graph.", device_id, ip)

    def sync_with_current_network(self, gateway_ip, gateway_name=None, monitored_devices=None):
        """
        Strictly scopes the Digital Twin graph to the current active network.
        Removes any obsolete nodes from previous networks and establishes the current monitored set.
        """
        if monitored_devices is None:
            monitored_devices = []

        self.update_gateway_ip(gateway_ip, gateway_name)

        current_dev_ids = {d["device_id"] for d in monitored_devices}
        # Evict nodes that are no longer part of the current active network
        nodes_to_remove = [
            n for n in list(self.graph.nodes())
            if n != self.gateway_id and n not in current_dev_ids
        ]
        for n in nodes_to_remove:
            self.graph.remove_node(n)
            logger.info("Evicted non-current network node from Digital Twin: %s", n)

        # Synchronize current network monitored devices
        for d in monitored_devices:
            dev_id = d["device_id"]
            if dev_id not in self.graph:
                self.add_device(
                    device_id=dev_id,
                    name=d.get("device_name", dev_id),
                    ip=d.get("ip_address", ""),
                    device_type=d.get("device_type", "Computer"),
                    description=d.get("description", "")
                )
            else:
                self.graph.nodes[dev_id]["ip"] = d.get("ip_address", self.graph.nodes[dev_id].get("ip"))
                self.graph.nodes[dev_id]["name"] = d.get("device_name", self.graph.nodes[dev_id].get("name"))

    def update_device_status(self, device_id, status, latency=None):
        """
        Updates the real-time operational state of a device in the Digital Twin.
        Accepts ONLINE, UNREACHABLE, OFFLINE.
        """
        if device_id in self.graph:
            self.graph.nodes[device_id]["status"] = status
            self.graph.nodes[device_id]["latency"] = latency
            
            # Also update edge status
            if device_id != self.gateway_id and self.graph.has_edge(self.gateway_id, device_id):
                edge_status = "ACTIVE" if status == "ONLINE" else "INACTIVE"
                self.graph.edges[self.gateway_id, device_id]["status"] = edge_status
                self.graph.edges[self.gateway_id, device_id]["latency"] = latency
            return True
        return False

    def remove_device(self, device_id):
        """Removes a device and its incident edges from the graph."""
        if device_id != self.gateway_id and device_id in self.graph:
            self.graph.remove_node(device_id)
            logger.info("Device %s removed from Digital Twin graph.", device_id)
            return True
        return False

    def get_summary(self, stale_count=0):
        """Returns high-level statistics for dashboard cards."""
        nodes = list(self.graph.nodes(data=True))
        total_nodes = len(nodes)
        
        # Count statuses
        online_count = sum(1 for _, data in nodes if data.get("status") == "ONLINE")
        unreachable_count = sum(1 for _, data in nodes if data.get("status") in ("UNREACHABLE", "OFFLINE"))
        unknown_count = sum(1 for _, data in nodes if data.get("status") == "UNKNOWN")
        
        # Calculate mean latency among online nodes
        latencies = [
            data.get("latency") for _, data in nodes 
            if data.get("latency") is not None and data.get("status") == "ONLINE"
        ]
        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0

        # Monitored devices excluding the gateway hub
        monitored_devices_count = sum(1 for n, _ in nodes if n != self.gateway_id)

        return {
            "total_nodes": total_nodes,
            "monitored_devices": monitored_devices_count,
            "total_edges": self.graph.number_of_edges(),
            "online_count": online_count,
            "unreachable_count": unreachable_count,
            "offline_count": unreachable_count,  # backward compatibility
            "stale_count": stale_count,
            "unknown_count": unknown_count,
            "avg_latency": avg_latency,
            "is_connected": nx.is_connected(self.graph) if total_nodes > 0 else False
        }

    def to_cytoscape_elements(self):
        """
        Exports the graph data formatted for Cytoscape.js or JSON API consumption.
        """
        elements = {"nodes": [], "edges": []}
        
        for node_id, data in self.graph.nodes(data=True):
            elements["nodes"].append({
                "data": {
                    "id": str(node_id),
                    "label": data.get("name", str(node_id)),
                    "ip": data.get("ip", ""),
                    "type": data.get("type", "Computer"),
                    "status": data.get("status", "UNKNOWN"),
                    "latency": data.get("latency")
                }
            })
            
        for u, v, data in self.graph.edges(data=True):
            edge_id = data.get("id", f"edge_{u}_{v}")
            elements["edges"].append({
                "data": {
                    "id": edge_id,
                    "source": str(u),
                    "target": str(v),
                    "link_type": data.get("link_type", "wireless"),
                    "status": data.get("status", "ACTIVE"),
                    "latency": data.get("latency")
                }
            })
            
        return elements
