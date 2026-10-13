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

    def sync_with_current_network(self, gateway_ip, gateway_name=None, monitored_devices=None,
                                 authorized_devices=None, discovered_ips=None, session_id=None, network_cidr=None):
        """
        Strictly scopes the Digital Twin graph to the current active network.
        Removes any obsolete nodes from previous networks and establishes the current authorized model.
        """
        if monitored_devices is None:
            monitored_devices = []
        if authorized_devices is None:
            authorized_devices = monitored_devices
        if discovered_ips is None:
            discovered_ips = set()

        self.update_gateway_ip(gateway_ip, gateway_name)

        # Build map of all authorized and monitored devices belonging to current network
        current_dev_dict = {}
        for d in authorized_devices:
            current_dev_dict[d["device_id"]] = d
        for d in monitored_devices:
            current_dev_dict[d["device_id"]] = d

        current_dev_ids = set(current_dev_dict.keys())

        # Evict nodes that are no longer part of the current active network
        nodes_to_remove = [
            n for n in list(self.graph.nodes())
            if n != self.gateway_id and n not in current_dev_ids
        ]
        for n in nodes_to_remove:
            self.graph.remove_node(n)
            logger.info("Evicted non-current network node from Digital Twin: %s", n)

        # Synchronize current network authorized devices
        for dev_id, d in current_dev_dict.items():
            ip = d.get("ip_address", "")
            is_disc = ip in discovered_ips
            is_mon = bool(d.get("is_monitored", 1))
            live_st = d.get("status", "UNKNOWN")
            if live_st in ("STALE", "HISTORICAL"):
                live_st = "UNKNOWN"

            if dev_id not in self.graph:
                self.add_device(
                    device_id=dev_id,
                    name=d.get("device_name", dev_id),
                    ip=ip,
                    device_type=d.get("device_type", "Computer"),
                    description=d.get("description", "")
                )
            else:
                self.graph.nodes[dev_id]["ip"] = ip
                self.graph.nodes[dev_id]["name"] = d.get("device_name", self.graph.nodes[dev_id].get("name", dev_id))

            # Enrich node attributes with separate architectural states
            self.graph.nodes[dev_id]["authorization_status"] = d.get("authorization_status", "AUTHORIZED")
            self.graph.nodes[dev_id]["discovery_status"] = "VISIBLE" if is_disc else "NOT_VISIBLE"
            self.graph.nodes[dev_id]["monitoring_status"] = "MONITORED" if is_mon else "UNMONITORED"
            self.graph.nodes[dev_id]["live_status"] = live_st
            self.graph.nodes[dev_id]["status"] = live_st
            if session_id:
                self.graph.nodes[dev_id]["session_id"] = session_id
            if network_cidr:
                self.graph.nodes[dev_id]["network_cidr"] = network_cidr

    def update_device_status(self, device_id, status, latency=None):
        """
        Updates the real-time operational state of a device in the Digital Twin.
        Accepts ONLINE, UNREACHABLE, OFFLINE.
        """
        if device_id in self.graph:
            self.graph.nodes[device_id]["status"] = status
            self.graph.nodes[device_id]["live_status"] = status
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
                    "live_status": data.get("live_status", data.get("status", "UNKNOWN")),
                    "authorization_status": data.get("authorization_status", "AUTHORIZED"),
                    "discovery_status": data.get("discovery_status", "NOT_VISIBLE"),
                    "monitoring_status": data.get("monitoring_status", "MONITORED"),
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

    def to_api_dict(self, network_info=None):
        """
        Returns full Digital Twin state according to the architectural specification.
        Separates network metadata, nodes with architectural attributes, edges, elements, and summary.
        """
        net_info = network_info or {}
        nodes_list = []
        for node_id, data in self.graph.nodes(data=True):
            nodes_list.append({
                "id": str(node_id),
                "name": data.get("name", str(node_id)),
                "ip": data.get("ip", ""),
                "type": data.get("type", "Computer"),
                "status": data.get("status", "UNKNOWN"),
                "live_status": data.get("live_status", data.get("status", "UNKNOWN")),
                "discovery": data.get("discovery_status", "NOT_VISIBLE"),
                "discovery_status": data.get("discovery_status", "NOT_VISIBLE"),
                "authorization_status": data.get("authorization_status", "AUTHORIZED"),
                "monitoring_status": data.get("monitoring_status", "MONITORED"),
                "latency": data.get("latency")
            })

        edges_list = []
        for u, v, data in self.graph.edges(data=True):
            edges_list.append({
                "source": str(u),
                "target": str(v),
                "status": data.get("status", "ACTIVE"),
                "link_type": data.get("link_type", "wireless"),
                "latency": data.get("latency")
            })

        return {
            "mode": "LIVE_MONITORING",
            "network": {
                "cidr": net_info.get("network"),
                "gateway": net_info.get("gateway") or self.gateway_ip,
                "interface": net_info.get("interface"),
                "local_ip": net_info.get("local_ip"),
                "network_id": net_info.get("network_id")
            },
            "network_id": net_info.get("network_id"),
            "nodes": nodes_list,
            "edges": edges_list,
            "elements": self.to_cytoscape_elements(),
            "summary": self.get_summary()
        }
