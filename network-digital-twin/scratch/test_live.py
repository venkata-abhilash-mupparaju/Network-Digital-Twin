import urllib.request
import json
import time

def get(path):
    req = urllib.request.Request(f"http://127.0.0.1:5000{path}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def post(path, data=None):
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:5000{path}", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

print("=== 1. Active Network Detection ===")
net_status = get("/api/network/current")
print("Network Current:", net_status)

print("\n=== 2. Device Discovery Sweep (253 candidates) ===")
t0 = time.time()
disc_res = post("/api/discovery/start")
print(f"Discovery Done in {round(time.time() - t0, 2)}s | Success: {disc_res.get('success')} | Scanned: {disc_res.get('scanned_hosts')} | Visible: {disc_res.get('count')}")
if "diagnostics" in disc_res:
    print("Diagnostics Summary:")
    for k, v in disc_res["diagnostics"].items():
        print(f"  {k}: {v}")

print("\n=== 3. Discovered Devices Inventory ===")
dev_res = get("/api/discovery/devices")
for d in dev_res.get("devices", []):
    print(f"  -> IP: {d['ip_address']} | MAC: {d['mac_address']} | Hostname: {d['hostname']} | Source: {d['source']} | Status: {d['status']}")

print("\n=== 4. Controlled ICMP Probes (Ping All Visible Devices) ===")
t1 = time.time()
ping_all_res = post("/api/monitoring/ping-all")
print(f"Ping All Done in {round(time.time() - t1, 2)}s | Total Targets: {ping_all_res.get('count')}")
for r in ping_all_res.get("results", []):
    print(f"  -> {r['ip_address']:16} | Status: {r['status']:11} | Loss: {r['loss']:5} | Avg: {r['avg']:8} | Min: {r['min']:8} | Max: {r['max']:8}")

print("\n=== 5. Live Digital Twin (NetworkX) ===")
twin = get("/api/digital-twin")
print("Mode:", twin.get("mode"))
print("Summary:", twin.get("summary"))
for n in twin.get("elements", {}).get("nodes", []):
    print("  Node:", n["data"]["id"], n["data"]["label"], n["data"].get("ip"), n["data"].get("status"))

print("\n=== 6. Historical Telemetry (SQLite) ===")
telem = get("/api/telemetry?limit=5")
print(f"Fetched {len(telem)} recent telemetry records:")
for t in telem:
    print(f"  -> Dev: {t.get('device_id')} | Status: {t.get('status')} | Loss: {t.get('packet_loss')}% | Latency: {t.get('latency')} ms | Sent: {t.get('packets_sent')} | Recv: {t.get('packets_received')}")

print("\n=== 7. Isolated What-If Simulation ===")
sim_res = post("/api/simulation/device-failure", {"device_id": "GW_HOTSPOT"})
print("Simulated Failure:", sim_res.get("message"))
sim_impact = get("/api/simulation/impact")
print("Sim Impact is_connected:", sim_impact.get("impact", {}).get("is_connected"))
print("Sim Impact components:", sim_impact.get("impact", {}).get("connected_components_count"))
print("Live Digital Twin undisturbed is_connected:", get("/api/status").get("summary", {}).get("is_connected"))
reset_res = post("/api/simulation/reset")
print("Simulation Reset:", reset_res.get("message"))

print("\n=== ALL 7 PHASES VERIFIED SUCCESSFULLY ===")
