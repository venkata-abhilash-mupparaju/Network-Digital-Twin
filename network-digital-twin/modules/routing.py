"""
Routing & Path Analysis Module
------------------------------
Educational routing algorithms for the Network Digital Twin.
Demonstrates Shortest Path (Dijkstra) and Distance Vector Routing table calculation.
"""

import networkx as nx
import logging

logger = logging.getLogger(__name__)

class RoutingEngine:
    @staticmethod
    def calculate_shortest_path(graph, source, target):
        """
        Calculates the shortest operational path between source and target nodes.
        Considers only ACTIVE nodes and links.
        Returns: {
            "found": bool,
            "path": list of node IDs or [],
            "path_names": list of node names or [],
            "hop_count": int,
            "total_latency": float,
            "details": str
        }
        """
        if source not in graph or target not in graph:
            return {
                "found": False,
                "path": [],
                "path_names": [],
                "hop_count": 0,
                "total_latency": 0.0,
                "details": f"One or both nodes ('{source}', '{target}') do not exist in the topology."
            }

        # Verify operational status of endpoints
        source_data = graph.nodes[source]
        target_data = graph.nodes[target]
        source_failed = source_data.get("status") == "FAILED" or source_data.get("is_simulated_failed")
        target_failed = target_data.get("status") == "FAILED" or target_data.get("is_simulated_failed")

        if source_failed:
            return {
                "found": False,
                "path": [],
                "path_names": [],
                "hop_count": 0,
                "total_latency": 0.0,
                "details": f"Source node '{source}' is currently FAILED (simulated outage)."
            }

        if target_failed:
            return {
                "found": False,
                "path": [],
                "path_names": [],
                "hop_count": 0,
                "total_latency": 0.0,
                "details": f"Destination node '{target}' is currently FAILED (simulated outage)."
            }

        # Build an operational subgraph containing only active, operational nodes and links
        active_subgraph = nx.Graph()
        for node, data in graph.nodes(data=True):
            if not data.get("is_simulated_failed") and data.get("status") != "FAILED":
                active_subgraph.add_node(node, **data)

        for u, v, data in graph.edges(data=True):
            if (u in active_subgraph and v in active_subgraph and 
                not data.get("is_simulated_failed") and data.get("status") != "FAILED"):
                weight = data.get("latency") or 1.0
                active_subgraph.add_edge(u, v, weight=weight, **data)

        try:
            path = nx.shortest_path(active_subgraph, source=source, target=target, weight="weight")
            names = [graph.nodes[n].get("name", n) for n in path]
            hops = len(path) - 1
            
            # Calculate total path latency
            total_lat = 0.0
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                edge_data = active_subgraph.get_edge_data(u, v, {})
                lat = edge_data.get("latency") or 1.0
                total_lat += lat

            return {
                "found": True,
                "reachable": True,
                "path": path,
                "path_names": names,
                "hop_count": hops,
                "hops": hops,
                "total_latency": round(total_lat, 2),
                "cost": round(total_lat, 2),
                "details": f"Optimal path found via {hops} hop(s)."
            }
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {
                "found": False,
                "reachable": False,
                "path": [],
                "path_names": [],
                "hop_count": 0,
                "hops": 0,
                "total_latency": 0.0,
                "cost": 0.0,
                "details": f"No operational route between '{source}' and '{target}' (Network Partitioned)."
            }

    @staticmethod
    def compute_distance_vector_table(graph, node_id):
        """
        Simulates Distance Vector Routing (Bellman-Ford / RIP concept) for a given node.
        Computes the routing table showing:
        - Destination
        - Next Hop
        - Metric / Cost (Hops)
        """
        if node_id not in graph:
            return []

        # Filter active nodes/links
        active_subgraph = nx.Graph()
        for n, data in graph.nodes(data=True):
            if not data.get("is_simulated_failed") and data.get("status") != "FAILED":
                active_subgraph.add_node(n, **data)
        for u, v, data in graph.edges(data=True):
            if (u in active_subgraph and v in active_subgraph and 
                not data.get("is_simulated_failed") and data.get("status") != "FAILED"):
                active_subgraph.add_edge(u, v)

        table = []
        for target in graph.nodes():
            if target == node_id:
                continue

            target_name = graph.nodes[target].get("name", target)
            target_status = graph.nodes[target].get("status", "UNKNOWN")

            if target in active_subgraph and nx.has_path(active_subgraph, node_id, target):
                path = nx.shortest_path(active_subgraph, source=node_id, target=target)
                next_hop = path[1] if len(path) > 1 else target
                cost = len(path) - 1
                table.append({
                    "destination": target,
                    "destination_name": target_name,
                    "next_hop": next_hop,
                    "next_hop_name": graph.nodes[next_hop].get("name", next_hop),
                    "cost": cost,
                    "status": "REACHABLE"
                })
            else:
                table.append({
                    "destination": target,
                    "destination_name": target_name,
                    "next_hop": "None",
                    "next_hop_name": "Unreachable",
                    "cost": "Infinity",
                    "status": f"UNREACHABLE ({target_status})"
                })

        return table
