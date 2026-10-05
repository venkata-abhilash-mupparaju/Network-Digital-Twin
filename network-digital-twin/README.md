# Network Digital Twin for Real-Time Network Monitoring and What-If Simulation

> **College Engineering Capstone Prototype &bull; Data Science & Computer Networks**  
> A production-grade, demonstrable Network Digital Twin built with Python 3.12, Flask, NetworkX, Cytoscape.js, and SQLite.

---

## 1. Project Overview & 5-Layer Architecture

This application is a complete **Network Digital Twin** structured across five interconnected layers:

```text
LAYER 1: REAL LOCAL TEST NETWORK
   │     - Windows monitoring laptop connected to Wi-Fi / Mobile Hotspot
   │     - Gateway Router / Access Point + client devices (phones, laptops)
   ▼
LAYER 2: CURRENT NETWORK DETECTION & ACTIVE/PASSIVE DISCOVERY
   │     - Dynamically fingerprints active adapter, IPv4, netmask, CIDR, gateway
   │     - Calculates usable host candidates via Python `ipaddress` (253 candidates on /24)
   │     - Merges concurrent ICMP echo sweeps with OS ARP neighbor cache
   │     - Controlled 5-packet ICMP probes (sent, received, loss %, min/avg/max latency)
   ▼
LAYER 3: LIVE DIGITAL TWIN (NETWORKX GRAPH)
   │     - In-memory graph of current network nodes and star-topology links
   │     - Scoped strictly to active network: previous devices marked STALE/HISTORICAL
   ▼
LAYER 4: HISTORICAL TELEMETRY & AUDIT LOGS (SQLITE)
   │     - Time-series device status records with packets sent/received & loss %
   │     - Deduplicated state transition event audit logs (`DEVICE_UNREACHABLE`, `DEVICE_RECOVERED`)
   ▼
LAYER 5: ISOLATED WHAT-IF SIMULATION
         - Operates strictly on an in-memory deep copy (`Graph.copy()`)
         - Fault injection (node drops, link cuts, star/ring/mesh reconfigurations)
         - Dijkstra shortest path rerouting & Distance Vector tables
         - NEVER transmits packets or affects physical devices
```

---

## 2. Key Technical Capabilities

1. **Dynamic Network Detection**:
   - Detects adapter switching between different hotspots or Wi-Fi networks (e.g., `192.168.43.x` &rarr; `192.168.1.x` &rarr; `192.168.8.x`).
   - Zero hardcoded IP addresses or subnets.
2. **Candidate Generation & Subnet Calculation**:
   - Uses Python `ipaddress.IPv4Network(target_network).hosts()` to calculate candidate targets dynamically.
   - Automatically excludes network address, broadcast address, and the monitoring laptop's own local IPv4 address.
   - For standard `/24` subnets (256 addresses, 254 usable hosts): exactly **253 remote candidate hosts** are probed. The previous 64-host restriction is completely eliminated.
3. **Large Network Protection**:
   - If the detected subnet has more than 512 addresses (e.g. a `/19` campus network with 8,192 addresses), full sweep is automatically disabled to prevent network saturation.
   - The system falls back to the OS ARP table plus a targeted candidate window around the gateway and local IP, logging a clear user warning.
4. **Controlled ICMP Probing (`Ping All Visible Devices`)**:
   - Sends exactly 5 ICMP echo requests per host with 1-second timeouts.
   - Formula: $\text{packet\_loss} = \frac{\text{sent} - \text{received}}{\text{sent}} \times 100\%$.
   - Latency statistics: min, avg, max round-trip times in milliseconds.
   - If 0 packets received: loss is reported as $100\%$ and latency is displayed as `N/A` (stored as `NULL`/`None`, never falsely reported as `0 ms`).
   - Every probe run persists telemetry in the SQLite `device_status` table.
5. **Strict Network & Stale Isolation**:
   - Live Digital Twin and monitoring loops filter strictly by `WHERE network_id = ? AND is_monitored = 1`.
   - Devices from prior networks are cleanly isolated as `STALE / HISTORICAL`, never reported as false "OFFLINE" nodes with 100% loss.
6. **Non-Destructive What-If Simulation**:
   - Uses an independent deep copy of the NetworkX graph.
   - Evaluates impact severity, partitions, isolated nodes, and reachability.
   - Safe for academic demonstrations.

---

## 3. Technology Stack

- **Backend**: Python 3.12, Flask 3.x
- **Graph Modeling**: NetworkX (in-memory graph structures, Dijkstra shortest paths, graph partitioning)
- **Database**: SQLite3 with WAL mode, foreign keys, and indexed telemetry tables
- **Frontend Visualization**: Cytoscape.js (bundled locally for fully offline hotspot operation)
- **Discovery**: Dynamic `ipaddress` candidate generation, concurrent ICMP ping sweeps (`ThreadPoolExecutor`), and OS ARP table parsing (`arp -a`)
- **Process Execution**: Python `subprocess.run` with list arguments (never `shell=True`)

---

## 4. REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/network/current` | GET | Returns detected interface, local IP, gateway, subnet mask, CIDR, and network fingerprint ID |
| `/api/network/refresh` | POST | Forces immediate re-detection of active network adapter and handles network transitions |
| `/api/status` | GET | Live Digital Twin health, operational mode, gateway node info, and summary counts |
| `/api/devices` | GET | Returns current network devices vs historical/stale devices |
| `/api/devices/<id>/monitor` | POST | Adds device to active Digital Twin monitoring set |
| `/api/devices/<id>/unmonitor` | POST | Removes device from active monitoring without deleting history |
| `/api/devices/<id>/test` | POST | Executes manual 5-packet ICMP probe on a specific registered device |
| `/api/discovery/start` | POST | Starts active ICMP sweep across all 253 candidate hosts (or targeted window on large subnets) |
| `/api/discovery/status` | GET | Returns real-time discovery progress (`checked`, `total`, `percent`, `visible_count`, `is_running`) |
| `/api/discovery/devices` | GET | Returns list of visible devices from latest discovery or ARP inspection |
| `/api/monitoring/ping-all` | POST | Probes all visible devices with 5 ICMP packets, logs telemetry to SQLite, and returns comparison table |
| `/api/digital-twin` | GET | Returns Cytoscape elements and summary for live digital twin |
| `/api/telemetry` | GET | Historical telemetry query filterable by `device_id`, `network_id`, and `limit` |
| `/api/simulation/state` | GET | Evaluates simulated graph partitions, isolated nodes, and connectivity |
| `/api/simulation/device-failure` | POST | Simulates hypothetical device outage (e.g. gateway or client drop) |
| `/api/simulation/recovery` | POST | Simulates hypothetical device recovery |
| `/api/simulation/link-failure` | POST | Simulates hypothetical link cut between two nodes |
| `/api/simulation/topology` | POST | Reconfigures simulation to Star, Ring, or Mesh |
| `/api/simulation/path` | GET/POST | Computes shortest path and hop count on simulated topology |
| `/api/simulation/reset` | POST | Re-clones simulated graph from current live Digital Twin state |

---

## 5. How to Run the Application

### 1. Launch the Server
```powershell
cd "network-digital-twin"
python app.py
```

### 2. Access the Web Dashboard
Open your browser and navigate to:
```text
http://127.0.0.1:5000
```

### 3. Run the Automated Test Suite
```powershell
python -m unittest discover tests
```
*Current test suite: **45/45 automated unit and integration tests passing (100% OK)**.*

---

## 6. College Examination & Viva Q&A Guide

### Q1: Why can a client laptop not discover every device connected to a mobile hotspot?
**Answer**:
A client device on a Wi-Fi network operates under OS and protocol constraints:
1. **Firewalls & ICMP Blocking**: Many modern smartphones (e.g. iPhones, Androids) and Windows machines have stealth mode or local firewalls that drop unsolicited ICMP echo requests.
2. **Wi-Fi Client Isolation (AP Isolation)**: Some mobile hotspots and enterprise routers isolate wireless clients from communicating with each other directly; packets can only travel between client and gateway.
3. **Passive ARP Expiration**: The OS ARP table only contains entries for hosts the laptop has actively communicated with recently (typically aging out after 15–45 seconds).
Therefore, the application strictly uses the term **"Visible Devices"** rather than falsely claiming "All Connected Devices".

### Q2: Why are exactly 253 candidate hosts scanned on a /24 network?
**Answer**:
In IPv4 CIDR notation, a `/24` network has 32 - 24 = 8 host bits, yielding $2^8 = 256$ total IPv4 addresses:
- 1 Network Address (e.g. `192.168.8.0`) &rarr; reserved, not assignable.
- 1 Broadcast Address (e.g. `192.168.8.255`) &rarr; reserved for subnet broadcast.
- This leaves $256 - 2 = 254$ usable host addresses.
- Because the monitoring laptop is itself one of the 254 usable hosts (e.g. `192.168.8.213`), scanning its own IP as an external device would produce distorted zero-latency loopback results.
- Excluding the local host leaves exactly $254 - 1 = \mathbf{253}$ remote candidate addresses.

### Q3: How is network switching handled, and why don't old devices show as 100% loss?
**Answer**:
Every network interface session receives a unique fingerprint `network_id = interface|network_cidr|gateway`.
When the laptop disconnects from Hotspot A (`192.168.43.0/24`) and connects to Hotspot B (`192.168.8.0/24`), the system:
1. Detects the new network fingerprint via `get_current_network_info()`.
2. Closes the prior network session in SQLite.
3. Automatically marks all devices belonging to previous networks as `STALE / HISTORICAL`.
4. Restricts background ICMP monitoring strictly to `WHERE network_id = ? AND is_monitored = 1`.
Because stale devices are never probed during the new network session, they are never falsely reported as "OFFLINE with 100% packet loss".

### Q4: What is the difference between Network Monitoring and a Digital Twin?
**Answer**:
- **Network Monitoring**: A passive or active telemetry collection tool (e.g. MRTG, basic ping) that records raw time-series data (latency, packet loss).
- **Network Digital Twin**: A living, graph-theoretic virtual replica (using NetworkX) that models topological relationships, adjacency matrices, and routing dependencies. The digital twin can compute graph connectivity, determine whether a gateway failure partitions the network, and execute predictive What-If simulations on an isolated virtual clone without touching the real physical infrastructure.

### Q5: How do you guarantee the What-If simulation never damages the physical network?
**Answer**:
The simulation engine uses `self.sim_graph = digital_twin.graph.copy()`. All simulated node failures, link cuts, and topology mutations modify only this in-memory NetworkX graph copy. No OS commands, socket packets, or ping requests are generated during simulation.
