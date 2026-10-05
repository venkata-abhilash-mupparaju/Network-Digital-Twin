/**
 * Live Digital Twin Topology Visualizer (Cytoscape.js)
 * Renders the in-memory NetworkX model on an interactive HTML5 canvas.
 */

let cy = null;
let selectedNodeData = null;

document.addEventListener("DOMContentLoaded", () => {
    initCytoscape();
    fetchNetworkTopology();
    
    // Auto refresh topology every 4 seconds
    setInterval(() => {
        fetchNetworkTopology(true);
    }, 4000);

    // Zoom controls
    const btnFit = document.getElementById("btnFitGraph");
    if (btnFit) {
        btnFit.addEventListener("click", () => {
            if (cy) cy.fit(undefined, 30);
        });
    }

    // Manual ping trigger
    const btnPingNow = document.getElementById("btnTriggerPing");
    if (btnPingNow) {
        btnPingNow.addEventListener("click", triggerManualPing);
    }

    // Local ARP Discovery controls
    const btnRefreshDiscovery = document.getElementById("btnRefreshDiscovery");
    if (btnRefreshDiscovery) {
        btnRefreshDiscovery.addEventListener("click", fetchDiscoveredDevices);
    }
    const btnActiveDisc = document.getElementById("btnActiveDiscovery");
    if (btnActiveDisc) {
        btnActiveDisc.addEventListener("click", triggerActiveDiscovery);
    }
    const btnPingAll = document.getElementById("btnPingAllVisible");
    if (btnPingAll) {
        btnPingAll.addEventListener("click", triggerPingAllVisible);
    }
    fetchDiscoveredDevices();

    // Host Network Info controls
    const btnRefreshNetInfo = document.getElementById("btnRefreshNetInfo");
    if (btnRefreshNetInfo) {
        btnRefreshNetInfo.addEventListener("click", fetchNetworkInfo);
    }
    fetchNetworkInfo();
    setInterval(fetchNetworkInfo, 10000);
});

/**
 * Initializes the Cytoscape graph canvas with styles.
 */
function initCytoscape() {
    const container = document.getElementById("cyContainer");
    if (!container) return;

    cy = cytoscape({
        container: container,
        boxSelectionEnabled: false,
        autounselectify: false,
        style: [
            // Core node styling
            {
                selector: 'node',
                style: {
                    'content': 'data(label)',
                    'color': '#f9fafb',
                    'font-family': 'Inter, sans-serif',
                    'font-size': '11px',
                    'font-weight': '600',
                    'text-valign': 'bottom',
                    'text-margin-y': 6,
                    'text-outline-color': '#0b0f19',
                    'text-outline-width': 2,
                    'background-color': '#374151',
                    'border-width': 2,
                    'border-color': '#4b5563',
                    'width': 44,
                    'height': 44,
                    'transition-property': 'background-color, border-color, width, height',
                    'transition-duration': '0.3s'
                }
            },
            // Gateway / Router Node
            {
                selector: 'node[type = "Router"]',
                style: {
                    'shape': 'diamond',
                    'width': 54,
                    'height': 54,
                    'background-color': '#0284c7',
                    'border-color': '#38bdf8',
                    'border-width': 3
                }
            },
            // Online State
            {
                selector: 'node[status = "ONLINE"]',
                style: {
                    'background-color': '#059669',
                    'border-color': '#34d399',
                    'border-width': 3,
                    'shadow-blur': 12,
                    'shadow-color': '#10b981',
                    'shadow-opacity': 0.4
                }
            },
            // Offline / Unreachable State
            {
                selector: 'node[status = "OFFLINE"], node[status = "UNREACHABLE"]',
                style: {
                    'background-color': '#b91c1c',
                    'border-color': '#f87171',
                    'border-width': 3,
                    'shadow-blur': 10,
                    'shadow-color': '#ef4444',
                    'shadow-opacity': 0.5
                }
            },
            // Selected Node
            {
                selector: 'node:selected',
                style: {
                    'border-color': '#f59e0b',
                    'border-width': 4,
                    'width': 50,
                    'height': 50
                }
            },
            // Edge Styling
            {
                selector: 'edge',
                style: {
                    'curve-style': 'bezier',
                    'width': 2.5,
                    'line-color': '#334155',
                    'target-arrow-shape': 'none',
                    'transition-property': 'line-color, width',
                    'transition-duration': '0.3s'
                }
            },
            // Active Links
            {
                selector: 'edge[status = "ACTIVE"]',
                style: {
                    'line-color': '#10b981',
                    'width': 3,
                    'opacity': 0.85
                }
            },
            // Inactive Links
            {
                selector: 'edge[status = "INACTIVE"], edge[status = "FAILED"]',
                style: {
                    'line-color': '#ef4444',
                    'line-style': 'dashed',
                    'width': 2,
                    'opacity': 0.5
                }
            }
        ],
        layout: {
            name: 'concentric',
            concentric: function(node) {
                // Keep the hotspot gateway router in the center!
                return node.data('type') === 'Router' ? 2 : 1;
            },
            levelWidth: function() { return 1; },
            padding: 40,
            animate: false
        }
    });

    // Node click inspector event
    cy.on('tap', 'node', function(evt) {
        const node = evt.target;
        displayNodeDetails(node.data());
    });

    cy.on('tap', function(evt) {
        if (evt.target === cy) {
            clearNodeDetails();
        }
    });
}

/**
 * Fetches latest graph elements and metrics from /api/network.
 */
async function fetchNetworkTopology(isSilent = false) {
    try {
        const response = await fetch('/api/network');
        if (!response.ok) return;
        const data = await response.json();

        if (cy && data.elements) {
            // Update Cytoscape elements preserving positions
            updateCytoscapeGraph(data.elements);
        }

        // Update top metric indicators
        if (data.summary) {
            updateDashboardMetrics(data.summary);
        }
    } catch (err) {
        console.warn("Failed fetching topology update:", err);
    }
}

/**
 * Synchronizes Cytoscape graph nodes and edges smoothly without re-scrambling layout.
 */
function updateCytoscapeGraph(elements) {
    const isFirstLoad = cy.nodes().length === 0;

    elements.nodes.forEach(n => {
        const existing = cy.getElementById(n.data.id);
        if (existing.length > 0) {
            existing.data(n.data);
        } else {
            cy.add(n);
        }
    });

    elements.edges.forEach(e => {
        const existing = cy.getElementById(e.data.id);
        if (existing.length > 0) {
            existing.data(e.data);
        } else {
            cy.add(e);
        }
    });

    // Remove nodes that no longer exist
    const incomingNodeIds = new Set(elements.nodes.map(n => n.data.id));
    cy.nodes().forEach(node => {
        if (!incomingNodeIds.has(node.id())) {
            cy.remove(node);
        }
    });

    // On initial load or node count change, apply concentric star layout
    if (isFirstLoad) {
        cy.layout({
            name: 'concentric',
            concentric: function(node) {
                return node.data('type') === 'Router' ? 2 : 1;
            },
            levelWidth: function() { return 1; },
            padding: 50,
            animate: true,
            animationDuration: 500
        }).run();
    }

    // Refresh selected node panel if currently open
    if (selectedNodeData) {
        const refreshed = cy.getElementById(selectedNodeData.id);
        if (refreshed.length > 0) {
            displayNodeDetails(refreshed.data());
        }
    }
}

/**
 * Displays device telemetry details in the inspector sidebar panel.
 */
function displayNodeDetails(data) {
    selectedNodeData = data;
    const card = document.getElementById("nodeInspector");
    if (!card) return;

    card.style.display = "block";
    document.getElementById("inspName").textContent = data.label || data.id;
    document.getElementById("inspId").textContent = data.id;
    document.getElementById("inspIp").textContent = data.ip || "N/A";
    document.getElementById("inspType").textContent = data.type || "Device";
    
    const statusElem = document.getElementById("inspStatus");
    statusElem.textContent = data.status || "UNKNOWN";
    statusElem.className = "badge " + (data.status === "ONLINE" ? "badge-online" : "badge-offline");

    const latElem = document.getElementById("inspLatency");
    if (data.latency !== null && data.latency !== undefined) {
        latElem.textContent = `${data.latency} ms`;
    } else {
        latElem.textContent = data.status === "ONLINE" ? "< 1 ms" : "Unreachable";
    }
}

function clearNodeDetails() {
    selectedNodeData = null;
    const card = document.getElementById("nodeInspector");
    if (card) card.style.display = "none";
}

/**
 * Updates summary counter cards.
 */
function updateDashboardMetrics(summary) {
    const valTotal = document.getElementById("valTotalNodes");
    const valOnline = document.getElementById("valOnlineNodes");
    const valOffline = document.getElementById("valOfflineNodes");
    const valStale = document.getElementById("valStaleNodes");
    const valLatency = document.getElementById("valAvgLatency");
    const valTopo = document.getElementById("valTopologyStatus");

    if (valTotal) valTotal.textContent = summary.total_nodes;
    if (valOnline) valOnline.textContent = summary.online_count;
    if (valOffline) valOffline.textContent = summary.unreachable_count !== undefined ? summary.unreachable_count : summary.offline_count;
    if (valStale) valStale.textContent = summary.stale_count !== undefined ? summary.stale_count : 0;
    if (valLatency) valLatency.innerHTML = `${summary.avg_latency} <span class="unit">ms</span>`;
    if (valTopo) valTopo.textContent = summary.is_connected ? "Connected (1 Component)" : "Partitioned";
}

/**
 * Sends request to backend to perform an immediate ICMP ping sweep.
 */
async function triggerManualPing() {
    const btn = document.getElementById("btnTriggerPing");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Pinging Targets...";
    }

    try {
        const res = await fetch("/api/monitor/ping-all", { method: "POST" });
        if (res.ok) {
            await fetchNetworkTopology();
            refreshEventsList();
        }
    } catch (e) {
        console.error("Manual ping error:", e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ Ping Now";
        }
    }
}

/**
 * Refreshes the events list feed.
 */
async function refreshEventsList() {
    const list = document.getElementById("eventsList");
    if (!list) return;

    try {
        const res = await fetch("/api/events?limit=8");
        if (!res.ok) return;
        const events = await res.json();
        
        if (events.length === 0) return;

        list.innerHTML = events.map(ev => {
            const badgeClass = ev.event_type === 'STATUS_CHANGE' 
                ? (ev.message.includes('ONLINE') ? 'badge-online' : 'badge-offline')
                : (ev.event_type === 'ALERT' ? 'badge-warning' : 'badge-system');

            return `
                <div class="event-item">
                    <span class="event-time">${ev.timestamp}</span>
                    <span class="event-badge ${badgeClass}">${ev.event_type}</span>
                    <div class="event-body">
                        <strong>${ev.message}</strong>
                    </div>
                </div>
            `;
        }).join("");
    } catch (e) {
        console.warn("Failed updating events:", e);
    }
}

// Refresh events every 5 seconds
setInterval(refreshEventsList, 5000);

/**
 * Fetches host network interface details from /api/network/info.
 */
async function fetchNetworkInfo() {
    try {
        const res = await fetch("/api/network/info");
        if (!res.ok) return;
        const data = await res.json();
        if (data.success && data.network) {
            const net = data.network;
            const ifaceElem = document.getElementById("netInterface");
            const ipElem = document.getElementById("netLocalIp");
            const gwElem = document.getElementById("netGateway");
            const subElem = document.getElementById("netSubnet");
            const idElem = document.getElementById("netNetworkId");
            const badgeElem = document.getElementById("netStatusBadge");

            if (ifaceElem) ifaceElem.textContent = net.interface || "Unknown Interface";
            if (ipElem) ipElem.textContent = net.local_ip || "Disconnected";
            if (gwElem) gwElem.textContent = net.gateway || "None / Hotspot AP";
            if (subElem) subElem.textContent = net.network || "N/A";
            if (idElem) idElem.textContent = net.network_id || "DISCONNECTED";

            if (badgeElem) {
                if (net.status === "CONNECTED") {
                    badgeElem.className = "badge badge-online";
                    badgeElem.textContent = "CONNECTED";
                } else {
                    badgeElem.className = "badge badge-offline";
                    badgeElem.textContent = "DISCONNECTED";
                }
            }
        }
    } catch (e) {
        console.warn("Failed fetching network info:", e);
    }
}

/**
/**
 * Fetches visible devices on active network from /api/discovery/devices.
 */
async function fetchDiscoveredDevices() {
    const tbody = document.getElementById("discoveredTableBody");
    const countBadge = document.getElementById("discoveryCountBadge");
    const timeElem = document.getElementById("discoveryLastTime");
    const btn = document.getElementById("btnRefreshDiscovery");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Scanning...";
    }

    try {
        const res = await fetch("/api/discovery/devices");
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();

        if (timeElem) {
            const now = new Date();
            timeElem.textContent = now.toLocaleTimeString();
        }

        if (countBadge) {
            countBadge.textContent = `${data.count} Visible Device(s)`;
        }

        if (tbody) {
            if (!data.devices || data.devices.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No active visible devices found. Click <strong>⚡ Discover Devices</strong> to probe the active subnet.</td></tr>`;
            } else {
                tbody.innerHTML = data.devices.map(d => {
                    const devId = d.device_id || `DEV_${d.ip_address.replace(/\./g, '_')}`;
                    const statusBadge = d.is_monitored 
                        ? `<span class="badge badge-online">MONITORED</span>`
                        : `<span class="badge badge-system">VISIBLE</span>`;

                    const latDisplay = d.discovery_latency !== null && d.discovery_latency !== undefined
                        ? `${d.discovery_latency} ms`
                        : `<span class="text-muted">—</span>`;

                    const isGw = d.is_gateway || (d.device_type === "Gateway");
                    let actionCol = '';
                    if (isGw) {
                        actionCol = `<span class="badge badge-online">Gateway (Hub)</span>`;
                    } else if (d.is_monitored) {
                        actionCol = `
                            <button class="btn btn-secondary btn-sm" onclick="toggleDeviceMonitor('${devId}', false)" title="Stop monitoring">Stop</button>
                            <button class="btn btn-primary btn-sm" onclick="quickTestDevice('${devId}', '${d.ip_address}')" title="5 ICMP probes">⚡ Test Now</button>
                        `;
                    } else {
                        actionCol = `
                            <button class="btn btn-primary btn-sm" onclick="toggleDeviceMonitor('${devId}', true)" title="Start monitoring in Digital Twin">Monitor</button>
                            <button class="btn btn-secondary btn-sm" onclick="openQuickRegisterModal('${d.ip_address}', '${d.mac_address}', '${d.source || ''}')">Custom</button>
                        `;
                    }

                    return `
                        <tr>
                            <td><code>${d.ip_address}</code></td>
                            <td><span class="font-mono text-muted">${d.mac_address || 'UNKNOWN'}</span></td>
                            <td><strong>${d.hostname || 'Device'}</strong></td>
                            <td><span class="badge badge-system" style="font-size:0.7rem;">${d.source || 'ARP'}</span></td>
                            <td><span class="font-mono">${latDisplay}</span></td>
                            <td>${statusBadge}</td>
                            <td class="action-buttons">${actionCol}</td>
                        </tr>
                    `;
                }).join("");
            }
        }
    } catch (e) {
        console.error("Discovery fetch error:", e);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center text-danger">Failed to fetch discovery information.</td></tr>`;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🔄 Refresh ARP";
        }
    }
}

/**
 * Triggers controlled active ICMP discovery across all dynamically calculated candidates
 * with live progress polling (Hosts Checked: X / 253).
 */
async function triggerActiveDiscovery() {
    const btn = document.getElementById("btnActiveDiscovery");
    const alertBox = document.getElementById("discoveryAlert");
    const progContainer = document.getElementById("discoveryProgressContainer");
    const progBar = document.getElementById("discoveryProgressBar");
    const progTitle = document.getElementById("discoveryProgressTitle");
    const progText = document.getElementById("discoveryProgressText");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Discovering...";
    }
    if (progContainer) progContainer.style.display = "block";
    if (progBar) progBar.style.width = "0%";
    if (progTitle) progTitle.textContent = "DISCOVERY IN PROGRESS";
    if (progText) progText.textContent = "Initializing candidate sweep...";
    if (alertBox) alertBox.style.display = "none";

    // Poll discovery status every 250ms
    const pollInterval = setInterval(async () => {
        try {
            const sRes = await fetch("/api/discovery/status");
            if (sRes.ok) {
                const sData = await sRes.json();
                if (progBar && sData.percent !== undefined) {
                    progBar.style.width = `${sData.percent}%`;
                }
                if (progText && sData.total > 0) {
                    progText.textContent = `Hosts Checked: ${sData.checked} / ${sData.total}`;
                }
            }
        } catch (_) {}
    }, 250);

    try {
        const res = await fetch("/api/discovery/start", { method: "POST" });
        const data = await res.json();
        clearInterval(pollInterval);

        if (res.ok && data.success) {
            if (progBar) progBar.style.width = "100%";
            if (progTitle) progTitle.textContent = "DISCOVERY COMPLETE";
            if (progText) {
                progText.textContent = `Hosts Checked: ${data.scanned_hosts} | Visible Devices: ${data.count}`;
            }

            let msg = `Discovery complete: identified ${data.count} visible device(s) across ${data.scanned_hosts} candidates.`;
            if (data.warning) {
                msg += ` [Protection: ${data.warning}]`;
            }
            if (alertBox) {
                alertBox.className = "alert-box alert-success";
                alertBox.textContent = msg;
                alertBox.style.display = "block";
            }

            // Render Diagnostics Summary Card (Section 12 & 13)
            const diagCard = document.getElementById("discoveryDiagnosticsCard");
            if (diagCard && data.diagnostics) {
                const d = data.diagnostics;
                const elNet = document.getElementById("diagNetwork");
                const elHost = document.getElementById("diagLocalHost");
                const elGw = document.getElementById("diagGateway");
                const elChk = document.getElementById("diagChecked");
                const elArp = document.getElementById("diagArp");
                const elIcmp = document.getElementById("diagIcmp");
                const elGwCl = document.getElementById("diagGwClients");
                const elVis = document.getElementById("diagVisible");
                const elExp = document.getElementById("diagExplanation");

                if (elNet) elNet.textContent = d.network || '—';
                if (elHost) elHost.textContent = d.local_host || '—';
                if (elGw) elGw.textContent = d.gateway || '—';
                if (elChk) elChk.textContent = `${d.candidates_checked || data.scanned_hosts} hosts`;
                if (elArp) elArp.textContent = `${d.arp_entries || 0} entries`;
                if (elIcmp) elIcmp.textContent = `${d.icmp_responses || 0} responsive`;
                if (elGwCl) elGwCl.textContent = d.gateway_clients || 'N/A';
                if (elVis) elVis.textContent = `${d.visible_devices || data.count} visible`;
                if (elExp) elExp.innerHTML = d.explanation ? `<strong>Diagnostic Evaluation:</strong> ${d.explanation}` : '';
                diagCard.style.display = "block";
            }

            fetchDiscoveredDevices();
            fetchNetworkTopology();
            refreshEventsList();
        } else {
            if (alertBox) {
                alertBox.className = "alert-box alert-error";
                alertBox.textContent = data.error || "Active discovery failed.";
                alertBox.style.display = "block";
            }
        }
    } catch (err) {
        clearInterval(pollInterval);
        if (alertBox) {
            alertBox.className = "alert-box alert-error";
            alertBox.textContent = "Communication error during device discovery.";
            alertBox.style.display = "block";
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ Discover Devices";
        }
    }
}

/**
 * Pings all currently visible devices with 5 ICMP packets and renders the comparison table.
 */
async function triggerPingAllVisible() {
    const btn = document.getElementById("btnPingAllVisible");
    const container = document.getElementById("pingAllResultsContainer");
    const tbody = document.getElementById("pingAllTableBody");
    const badge = document.getElementById("pingAllSummaryBadge");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Pinging (5 pkts/host)...";
    }
    if (container) container.style.display = "block";
    if (badge) {
        badge.className = "badge badge-warning";
        badge.textContent = "Sending 5 ICMP probes...";
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">Executing 5-packet ICMP probes across all visible devices...</td></tr>`;
    }

    try {
        const res = await fetch("/api/monitoring/ping-all", { method: "POST" });
        const data = await res.json();

        if (res.ok && data.success && data.results) {
            if (badge) {
                badge.className = "badge badge-online";
                badge.textContent = `Completed (${data.count} Hosts)`;
            }

            tbody.innerHTML = data.results.map(r => {
                const isOnline = r.status === "ONLINE";
                const statusBadge = isOnline
                    ? `<span class="badge badge-online">ONLINE</span>`
                    : `<span class="badge badge-offline">UNREACHABLE</span>`;

                const lossBadge = r.packet_loss === 0
                    ? `<span class="badge badge-online">${r.loss}</span>`
                    : (r.packet_loss < 50 ? `<span class="badge badge-warning">${r.loss}</span>` : `<span class="badge badge-offline">${r.loss}</span>`);

                const avgColor = isOnline ? "#38bdf8" : "#94a3b8";

                return `
                    <tr>
                        <td><code>${r.device}</code> <small class="text-muted">(${r.hostname || 'Device'})</small></td>
                        <td>${statusBadge}</td>
                        <td>${r.sent}</td>
                        <td>${r.received}</td>
                        <td>${lossBadge}</td>
                        <td><strong style="color: ${avgColor};">${r.avg}</strong></td>
                        <td><span class="text-muted font-mono">${r.min}</span></td>
                        <td><span class="text-muted font-mono">${r.max}</span></td>
                    </tr>
                `;
            }).join("");

            fetchNetworkTopology();
            refreshEventsList();
        } else {
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="8" class="text-center text-danger">Ping All operation failed: ${data.error || 'Server error'}</td></tr>`;
            }
        }
    } catch (e) {
        console.error("Ping all error:", e);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center text-danger">Network communication error while executing probe sweeps.</td></tr>`;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ Ping All Visible Devices";
        }
    }
}

/**
 * Toggles monitoring state for a discovered or registered device.
 */
async function toggleDeviceMonitor(devId, shouldMonitor) {
    const endpoint = shouldMonitor ? `/api/devices/${encodeURIComponent(devId)}/monitor` : `/api/devices/${encodeURIComponent(devId)}/unmonitor`;
    try {
        const res = await fetch(endpoint, { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            fetchDiscoveredDevices();
            fetchNetworkTopology();
            refreshEventsList();
        } else {
            alert(data.error || "Failed to update device monitoring state.");
        }
    } catch (e) {
        alert("Server communication error.");
    }
}

/**
 * Runs 5-packet ICMP test on a specific device and reloads topology.
 */
async function quickTestDevice(devId, ip) {
    try {
        const res = await fetch(`/api/devices/${encodeURIComponent(devId)}/test`, { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            alert(`Test Results for ${data.device_name} (${ip}):\nStatus: ${data.status}\nPackets: ${data.packets_received}/${data.packets_sent} (${data.loss} loss)\nAverage Latency: ${data.avg}\nMin: ${data.min} | Max: ${data.max}`);
            fetchNetworkTopology();
            refreshEventsList();
        } else {
            alert(data.error || "Probe test failed.");
        }
    } catch (e) {
        alert("Communication error during test.");
    }
}

/**
 * Opens Quick Register Modal with prefilled discovered device info.
 */
function openQuickRegisterModal(ip, mac, iface) {
    const modal = document.getElementById("quickRegisterModal");
    if (!modal) {
        window.location.href = `/devices?prefill_ip=${encodeURIComponent(ip)}&mac=${encodeURIComponent(mac)}`;
        return;
    }
    const form = document.getElementById("quickRegisterForm");
    if (form) form.reset();

    const devIp = document.getElementById("quickDevIp");
    const devMac = document.getElementById("quickDevMac");
    const devId = document.getElementById("quickDevId");
    const devName = document.getElementById("quickDevName");
    const devDesc = document.getElementById("quickDevDesc");

    if (devIp) devIp.value = ip;
    if (devMac) devMac.value = mac;

    // Suggest clean node id from IP's last octet
    const parts = ip.split(".");
    const lastOctet = parts[parts.length - 1];
    if (devId) devId.value = `NODE_${lastOctet}`;
    if (devName) devName.value = `Device ${ip}`;
    if (devDesc) devDesc.value = `Discovered via ${iface || 'Local ARP'}`;

    const alertBox = document.getElementById("quickRegisterAlert");
    if (alertBox) alertBox.style.display = "none";

    modal.style.display = "flex";
}

function closeQuickRegisterModal() {
    const modal = document.getElementById("quickRegisterModal");
    if (modal) modal.style.display = "none";
}

/**
 * Handles Quick Registration form submission directly from Dashboard.
 */
async function submitQuickRegister(e) {
    e.preventDefault();
    const btn = document.getElementById("btnQuickRegisterSubmit");
    const alertBox = document.getElementById("quickRegisterAlert");
    if (btn) btn.disabled = true;

    const payload = {
        device_id: document.getElementById("quickDevId").value.trim().toUpperCase(),
        device_name: document.getElementById("quickDevName").value.trim(),
        ip_address: document.getElementById("quickDevIp").value.trim(),
        device_type: document.getElementById("quickDevType").value,
        description: document.getElementById("quickDevDesc").value.trim(),
        mac_address: document.getElementById("quickDevMac").value.trim()
    };

    try {
        const res = await fetch("/api/devices", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (res.ok) {
            closeQuickRegisterModal();
            // Refresh discovery, topology, and events
            fetchDiscoveredDevices();
            fetchNetworkTopology();
            refreshEventsList();
        } else {
            if (alertBox) {
                alertBox.textContent = data.error || "Failed to register device.";
                alertBox.style.display = "block";
            }
        }
    } catch (err) {
        if (alertBox) {
            alertBox.textContent = "Network error while saving device.";
            alertBox.style.display = "block";
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

