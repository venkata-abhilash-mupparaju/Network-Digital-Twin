# Network Digital Twin for Real-Time Network Monitoring and What-If Simulation

> **College Engineering Capstone Project &bull; Computer Networks & Data Science**  
> A production-grade, demonstrable Network Digital Twin built with **Python 3.12**, **Flask**, **NetworkX**, **Cytoscape.js**, and **SQLite3**.

---

## 📋 Table of Contents
1. [Project Overview](#-1-project-overview)
2. [5-Layer Architecture](#-2-5-layer-architecture)
3. [Key Technical Highlights](#-3-key-technical-highlights)
4. [Project Structure](#-4-project-structure)
5. [Prerequisites & Installation](#-5-prerequisites--installation)
6. [Running the Application](#-6-running-the-application)
7. [Web User Interface Guide](#-7-web-user-interface-guide)
8. [REST API Reference](#-8-rest-api-reference)
9. [Automated Unit & Integration Tests](#-9-automated-unit--integration-tests)
10. [Safety & Network Ethics](#-10-safety--network-ethics)
11. [College Viva & Examination Q&A](#-11-college-viva--examination-qa)

---

## 🌟 1. Project Overview

This project is a complete **Network Digital Twin**. It continuously observes an authorized physical test network (such as devices connected to a mobile phone hotspot or Wi-Fi router), mirrors real-time device telemetry and logical topology into an in-memory virtual graph model (the Digital Twin), and exposes an isolated **What-If Simulation Environment** where hypothetical node drops, link cuts, and alternative topologies can be evaluated without any disruption to the physical network.

---

## 🏗️ 2. 5-Layer Architecture

The system coordinates five connected layers:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                      LAYER 1: REAL TEST NETWORK                             │
│       Mobile Hotspot / Wi-Fi Access Point (e.g. Gateway 192.168.8.9)        │
│          ├── Laptop A (Flask Host @ 192.168.8.213)                          │
│          ├── Laptop B / Desktop                                             │
│          └── Smartphone / Peer Device                                       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│             LAYER 2: NETWORK DETECTION & ACTIVE/PASSIVE DISCOVERY           │
│  - modules/network_info.py: Parses ipconfig for adapter, IP, CIDR, gateway   │
│  - modules/discovery.py:                                                    │
│      • Dynamic candidate IP generation (exact 253 remote hosts on /24)       │
│      • Large subnet protection (> 512 hosts caps to targeted window)        │
│      • Concurrent ICMP echo sweeps (ThreadPoolExecutor with max_workers=32) │
│      • Windows OS ARP cache parser (`arp -a`)                               │
│      • Decoupled modular discovery subsystem & diagnostics summary          │
│  - modules/icmp.py: Controlled 5-packet burst probes (loss %, latency, jitter)│
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 LAYER 3: LIVE DIGITAL TWIN (NETWORKX GRAPH)                 │
│  - modules/network_model.py: In-memory live graph of nodes and edges        │
│  - Scoped strictly to current network (prior devices marked STALE)          │
│  - Real-time serialization to Cytoscape.js topology canvas                  │
└──────────────────┬──────────────────────────────────────┬───────────────────┘
                   │                                      │
                   ▼                                      ▼
┌──────────────────────────────────────┐  ┌───────────────────────────────────┐
│     LAYER 4: SQLITE PERSISTENCE      │  │   LAYER 5: WHAT-IF SIMULATION     │
│  - database/schema.sql & database.py │  │  - modules/simulator.py           │
│  - network_sessions (session history)│  │  - Isolated deep copy of live twin│
│  - devices (inventory & metadata)    │  │  - Fault injection (node & link)  │
│  - device_status (probe telemetry)   │  │  - Dijkstra shortest path routing │
│  - network_events (audit logs)       │  │  - Star / Ring / Mesh topologies  │
│  - Deduplicated state alerts         │  │  - NEVER transmits real packets   │
└──────────────────────────────────────┘  └───────────────────────────────────┘
```

---

## ⚡ 3. Key Technical Highlights

1. **Dynamic Network Adaptation (Zero Hardcoding)**:
   - When the host switches from Hotspot A (`192.168.43.x`) to Hotspot B (`192.168.8.x`), the system detects the transition, closes the prior session, and isolates old nodes as `STALE / HISTORICAL`.
   - Prevents stale devices from falsely appearing as "OFFLINE with 100% packet loss".
2. **Exact 253-Host Candidate Calculation**:
   - For a standard `/24` network (256 IPv4 addresses), excludes network (`.0`), broadcast (`.255`), and the monitoring laptop's own IP.
   - Computes: $254 \text{ usable} - 1 \text{ local host} = \mathbf{253} \text{ remote candidates}$.
   - Removes the arbitrary 64-host restriction of legacy scanners.
3. **Large Subnet Protection**:
   - For subnets with $> 512$ addresses (e.g. `/19` campus networks with 8,192 addresses), full sweeps are safely disabled to avoid network saturation, falling back to ARP inspection and a targeted window around the gateway and host.
4. **Controlled Multi-Packet Telemetry**:
   - `[ ⚡ Ping All Visible Devices ]` transmits exactly 5 ICMP echo requests per host.
   - Calculates Packets Sent, Packets Received, Packet Loss %, Min Latency, Average Latency, Max Latency, and Jitter.
   - Unreachable hosts correctly display `N/A` (never false `0 ms`).
5. **Non-Destructive Simulation Guarantee**:
   - Operates on `self.sim_graph = digital_twin.graph.copy()`.
   - Simulated device drops or link failures mutate only the in-memory copy. Real devices and live monitoring continue completely undisturbed.

---

## 📁 4. Project Structure

```text
network-digital-twin/
├── app.py                     # Main Flask web application & REST endpoints
├── config.py                  # Global parameters (timeouts, workers, thresholds)
├── requirements.txt           # Python library dependencies
├── README.md                  # Comprehensive project documentation
├── database/
│   ├── database.py            # SQLite connection helper & schema initializer
│   └── schema.sql             # Relational schema (WAL mode, foreign keys, indices)
├── modules/
│   ├── __init__.py            # Exported module interfaces
│   ├── network_info.py        # Windows ipconfig parser & network change detector
│   ├── discovery.py           # Modular discovery subsystem (ARP + ICMP + Gateway)
│   ├── icmp.py                # ICMP probe engine, packet loss & jitter calculator
│   ├── network_model.py       # Live NetworkX Digital Twin graph manager
│   ├── simulator.py           # Virtual What-If simulation engine
│   ├── routing.py             # Dijkstra shortest path & Distance Vector tables
│   └── events.py              # Event audit logging & threshold alerts
├── templates/
│   ├── base.html              # Base dark engineering layout & navigation
│   ├── index.html             # Live dashboard, canvas, discovery & diagnostics
│   ├── devices.html           # Device inventory (Active vs Historical)
│   ├── simulation.html        # What-If simulation studio
│   └── history.html           # Telemetry history & event audit log
├── static/
│   ├── css/
│   │   └── style.css          # Dark engineering theme styling
│   └── js/
│       ├── topology.js        # Cytoscape.js rendering & live polling engine
│       └── simulation.js      # Interactive fault injection & routing UI
└── tests/
    ├── test_complete_spec.py  # 21 tests covering all architectural specs
    ├── test_network_switching_and_stale.py  # Network migration & stale tests
    ├── test_probes.py         # ICMP parsing & packet loss formulas
    ├── test_twin.py           # NetworkX graph manipulation tests
    └── test_simulator.py      # Fault injection & recovery tests
```

---

## 🔧 5. Prerequisites & Installation

### Requirements
* **Operating System**: Windows 10 / 11 (uses Windows `ipconfig`, `arp -a`, and `ping`)
* **Python**: Version 3.10, 3.11, or 3.12

### Setup Instructions
1. Open PowerShell or Terminal and navigate to the project directory:
   ```powershell
   cd "network-digital-twin"
   ```
2. (Optional) Create and activate a Python virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. Install required packages:
   ```powershell
   pip install -r requirements.txt
   ```

---

## 🚀 6. Running the Application

1. Start the Flask application:
   ```powershell
   python app.py
   ```
2. Access the web interface in your browser:
   * **URL**: [http://127.0.0.1:5000](http://127.0.0.1:5000)

The server initializes SQLite at `data/network.db`, fingerprints the active network adapter, starts the 10-second background telemetry monitor, and serves the dashboard.

---

## 🖥️ 7. Web User Interface Guide

### 1. Dashboard (`/`)
* **Active Host Network Environment**: Displays active interface name, host IP, gateway IP, CIDR, and unique network fingerprint ID.
* **Live Digital Twin Canvas**: Interactive Cytoscape.js star topology with color-coded nodes (Green = Online, Red = Unreachable, Yellow = Warning).
* **Device Discovery Panel**:
  * `[ ⚡ Discover Devices ]`: Executes concurrent ICMP sweeps across all 253 candidate hosts and reads OS ARP cache.
  * `[ ⚡ Ping All Visible Devices ]`: Sends 5 ICMP packets per host and renders the comparison table.
  * **Discovery Diagnostics Summary**: Displays candidates checked, ARP entries, ICMP responders, gateway clients, and diagnostic explanations.

### 2. Device Manager (`/devices`)
* **Current Network Devices**: Displays visible hosts on the active network. Click **[+ Monitor]** to add a node to the continuous Digital Twin loop, or **[ Test Now ]** for an immediate 5-packet probe.
* **Historical Devices**: Displays devices registered during past network sessions (marked `STALE`). Old devices never corrupt active network telemetry. Click **[ Adopt ]** to re-assign an old device to the current network.

### 3. What-If Simulation Studio (`/simulation`)
* Clearly marked: `SIMULATION MODE — NO CHANGES ARE MADE TO THE REAL NETWORK`.
* **Fault Injection**: Select any node (e.g. Gateway or Client) and click **[ Fail Device ]** or **[ Fail Link ]**.
* **Impact Analysis**: Automatically calculates whether the network is partitioned, counts disconnected components, and lists isolated nodes.
* **Alternative Topologies**: Virtually reconfigures links to Star, Ring, or Mesh to compare fault tolerance.
* **Shortest Path Calculator**: Calculates Dijkstra shortest paths and hop counts between nodes.
* **Reset**: Click **[ Reset Simulation ]** to discard virtual edits and re-clone the live twin.

### 4. Telemetry & History (`/history`)
* Historical query table showing timestamps, device IDs, status, packet loss %, latency, and min/max RTT.
* Interactive client-side filters by Device ID, Network ID, and Status.
* Audit log of all state transitions and threshold alerts.

---

## 🔌 8. REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/network/current` | GET | Returns detected interface, host IP, gateway, subnet mask, CIDR, and network ID |
| `/api/network/refresh` | POST | Forces immediate re-detection of active network adapter |
| `/api/status` | GET | Live Digital Twin health, operational mode, gateway node info, and summary counts |
| `/api/devices` | GET | Returns current network devices vs historical/stale devices |
| `/api/devices/<id>/monitor` | POST | Adds device to active Digital Twin monitoring loop |
| `/api/devices/<id>/unmonitor` | POST | Removes device from active monitoring without deleting history |
| `/api/devices/<id>/test` | POST | Executes manual 5-packet ICMP probe on a specific registered device |
| `/api/discovery/start` | POST | Runs active ICMP sweep across all 253 candidate hosts (or targeted window on large subnets) |
| `/api/discovery/status` | GET | Returns real-time discovery progress (`checked`, `total`, `percent`, `visible_count`, `is_running`) |
| `/api/discovery/devices` | GET | Returns list of visible devices from latest discovery or ARP inspection |
| `/api/monitoring/ping-all` | POST | Probes all visible devices with 5 ICMP packets, logs telemetry to SQLite, and returns comparison table |
| `/api/digital-twin` | GET | Returns Cytoscape elements and summary for live digital twin |
| `/api/telemetry` | GET | Historical telemetry query filterable by `device_id`, `network_id`, and `limit` |
| `/api/simulation/state` | GET | Evaluates simulated graph partitions, isolated nodes, and connectivity |
| `/api/simulation/device-failure` | POST | Simulates hypothetical device outage |
| `/api/simulation/recovery` | POST | Simulates hypothetical device recovery |
| `/api/simulation/link-failure` | POST | Simulates hypothetical link cut between two nodes |
| `/api/simulation/topology` | POST | Reconfigures simulation to Star, Ring, or Mesh |
| `/api/simulation/path` | GET/POST | Computes shortest path and hop count on simulated topology |
| `/api/simulation/reset` | POST | Re-clones simulated graph from current live Digital Twin state |

---

## 🧪 9. Automated Unit & Integration Tests

The project includes a comprehensive automated test suite with **70 passing tests** using Python's `unittest` framework:

```powershell
python -m unittest discover tests
```

### Test Coverage Highlights
* **Architectural Decoupling**: Validates the separation of Device Discovery (`VISIBLE`/`NOT_VISIBLE`), Continuous Monitoring (`MONITORED`/`UNMONITORED`), Digital Twin Modeling (`PRESENT`), and Isolated What-If Simulation (`AVAILABLE`).
* **Authorized Offline Devices**: Verifies that authorized nodes (PC1, PC2, PC3) are retained in the Digital Twin and Simulator even when unreached by ARP/ping due to Wi-Fi client isolation or firewalls.
* **Candidate Calculation & Exclusions**: Verifies exact 253 candidate generation on `/24` subnets, with local host IP, network address, and broadcast address excluded.
* **Large Subnet Protection**: Verifies that `/19` subnets cap discovery to safe candidate counts and return warning messages.
* **Modular Discovery Architecture**: Tests `discover_from_arp`, `discover_from_icmp`, `discover_from_gateway_if_available`, and `merge_discovery_results`.
* **Telemetry & Formulas**: Tests exact packet loss formula, min/avg/max latency, and RFC 3550 jitter calculation.
* **Network Switching & Stale Isolation**: Tests session closure, event logging, and eviction of old devices from the live twin.
* **Non-Destructive Simulation**: Tests that simulated node and link cuts modify only in-memory copies, evaluate Dijkstra shortest paths, and calculate partition severity while live monitoring continues untouched.

---

## 🛡️ 10. Safety & Network Ethics

This application is strictly designed for **educational demonstration on personal authorized test networks**:
* **No Intrusive Scanning**: Does **not** perform SYN scans, port sweeps, or OS fingerprinting attacks.
* **No Packet Flooding**: Uses bounded thread concurrency (`max_workers=32`) with 1-second timeouts.
* **Private IPv4 Scoping**: Discovered devices are strictly validated against RFC 1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
* **Command Injection Protection**: Uses Python `subprocess.run` with list parameters and `shell=False`. User input is never passed to shell interpreters.

---

## 🎓 11. College Viva & Examination Q&A

### Q1: Why can a client laptop not discover every device connected to a mobile hotspot?
**Answer**:
A client device on a Wi-Fi network operates under OS and protocol constraints:
1. **Firewalls & ICMP Blocking**: Smartphones (iOS, Android) and modern Windows laptops routinely enable stealth mode or firewalls that silently drop unsolicited ICMP echo requests.
2. **Wi-Fi Client Isolation (AP Isolation)**: Many mobile hotspots and enterprise access points prevent wireless clients from communicating with each other directly; packets can only travel between client and gateway.
3. **Passive ARP Expiration**: The OS ARP table only contains entries for hosts the laptop has actively communicated with recently (aging out after 15–45 seconds).
Therefore, the application uses the accurate terminology **"Visible Devices"** rather than falsely claiming "All Connected Devices".

### Q2: Why are exactly 253 candidate hosts scanned on a /24 network?
**Answer**:
In IPv4 CIDR notation, a `/24` network has $32 - 24 = 8$ host bits, yielding $2^8 = 256$ total addresses:
* Less 1 Network Address (e.g. `192.168.8.0`) &rarr; reserved for network identity.
* Less 1 Broadcast Address (e.g. `192.168.8.255`) &rarr; reserved for subnet broadcast.
* Usable Host Addresses: $256 - 2 = 254$.
* Because the monitoring laptop is itself one of the 254 usable hosts (e.g. `192.168.8.213`), scanning its own IP as an external device would produce distorted zero-latency loopback results.
* Excluding the host leaves exactly $254 - 1 = \mathbf{253}$ remote candidate addresses.

### Q3: How is network switching handled, and why don't old devices show as 100% loss?
**Answer**:
Every network interface session receives a unique fingerprint `network_id = interface|network_cidr|gateway`. When the laptop connects to a different hotspot:
1. The background monitor detects the change via `check_network_change()`.
2. Closes the prior network session in SQLite (`is_active = 0`).
3. Automatically marks devices from previous networks as `STALE / HISTORICAL`.
4. Restricts background ICMP monitoring strictly to `WHERE network_id = ? AND is_monitored = 1`.
Because stale devices are never polled during the new network session, they are never falsely reported as "OFFLINE with 100% packet loss".

### Q4: What is the difference between Network Monitoring and a Digital Twin?
**Answer**:
* **Network Monitoring**: A reactive telemetry collection tool that records raw time-series metrics (latency, packet loss) for individual endpoints.
* **Network Digital Twin**: A living, graph-theoretic virtual replica (using NetworkX) that models topological relationships, adjacency matrices, and routing dependencies. The digital twin can compute graph connectivity, determine whether a gateway failure partitions dependent nodes, and execute predictive What-If simulations on an isolated virtual clone without touching the real physical network.

### Q5: How do you guarantee the What-If simulation never damages the physical network?
**Answer**:
The simulation engine creates an independent deep copy: `self.sim_graph = digital_twin.graph.copy()`. All simulated node failures, link cuts, and topology mutations modify only this in-memory NetworkX graph copy. No OS commands, socket packets, or ping requests are generated during simulation. Real devices and live monitoring continue completely undisturbed.

### Q6: Why do authorized offline or unreachable devices still appear in the Digital Twin and What-If Simulator?
**Answer**:
The architecture strictly decouples four distinct states:
1. **Device Discovery**: Observation status (`VISIBLE` vs `NOT_VISIBLE` via ARP/ICMP sweeps).
2. **Device Monitoring**: Telemetry polling status (`MONITORED` vs `UNMONITORED`).
3. **Digital Twin Model**: In-memory structural graph of all authorized devices belonging to the active network session (`PRESENT` even if `OFFLINE` or blocked by firewalls).
4. **What-If Simulation**: Isolated hypothetical experiment graph (`AVAILABLE` for fault injection).

In practical test scenarios (such as laptops connected to a mobile hotspot with client isolation enabled or Windows firewalls blocking ping), devices do not cease to exist as authorized nodes just because ICMP probes fail. Excluding offline nodes would collapse the simulation topology to only the gateway. By retaining authorized devices in the Digital Twin and initializing them as operational in the simulation clone, engineers can inject hypothetical failures (e.g. "What happens if PC2 drops?") and simulate recovery without needing live ICMP responses.
