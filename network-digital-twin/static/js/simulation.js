/**
 * What-If Simulation Controller & Canvas
 * Operates strictly on the in-memory clone of the network graph.
 */

let simCy = null;

document.addEventListener("DOMContentLoaded", () => {
    initSimulationCytoscape();
    fetchSimulationState();

    // Bind action buttons
    document.getElementById("btnFailDevice").addEventListener("click", handleFailDevice);
    document.getElementById("btnRecoverDevice").addEventListener("click", handleRecoverDevice);
    document.getElementById("btnFailLink").addEventListener("click", handleFailLink);
    document.getElementById("btnResetSim").addEventListener("click", handleResetSim);
    document.getElementById("btnCalcPath").addEventListener("click", handleCalculatePath);
    document.getElementById("btnCalcDV").addEventListener("click", handleCalculateDV);

    // Bind topology buttons
    document.querySelectorAll(".btn-topo").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const topo = e.target.getAttribute("data-topo");
            setSimulationTopology(topo);
        });
    });
});

/**
 * Initializes Cytoscape instance for simulation.
 */
function initSimulationCytoscape() {
    const container = document.getElementById("simCyContainer");
    if (!container) return;

    simCy = cytoscape({
        container: container,
        style: [
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
                    'background-color': '#059669',
                    'border-width': 2,
                    'border-color': '#34d399',
                    'width': 44,
                    'height': 44
                }
            },
            {
                selector: 'node[type = "Router"]',
                style: {
                    'shape': 'diamond',
                    'width': 54,
                    'height': 54,
                    'background-color': '#0284c7',
                    'border-color': '#38bdf8'
                }
            },
            // Simulated Failed Node
            {
                selector: 'node[is_simulated_failed = "true"], node[status = "FAILED"]',
                style: {
                    'background-color': '#b91c1c',
                    'border-color': '#f87171',
                    'border-width': 4,
                    'shape': 'octagon',
                    'shadow-blur': 15,
                    'shadow-color': '#ef4444',
                    'shadow-opacity': 0.8
                }
            },
            // Partitioned / Unreachable Node
            {
                selector: 'node[is_unreachable = "true"]',
                style: {
                    'background-color': '#d97706',
                    'border-color': '#fcd34d',
                    'border-width': 3,
                    'border-style': 'dashed',
                    'opacity': 0.8
                }
            },
            // Path Highlighted Node
            {
                selector: 'node.path-highlight',
                style: {
                    'border-color': '#a855f7',
                    'border-width': 5,
                    'width': 52,
                    'height': 52
                }
            },
            // Edges
            {
                selector: 'edge',
                style: {
                    'curve-style': 'bezier',
                    'width': 2.5,
                    'line-color': '#10b981',
                    'transition-property': 'line-color, width'
                }
            },
            // Failed Links
            {
                selector: 'edge[is_simulated_failed = "true"], edge[status = "FAILED"]',
                style: {
                    'line-color': '#ef4444',
                    'line-style': 'dashed',
                    'width': 3,
                    'opacity': 0.8
                }
            },
            // Path Highlighted Edge
            {
                selector: 'edge.path-highlight',
                style: {
                    'line-color': '#c084fc',
                    'width': 5,
                    'z-index': 99
                }
            }
        ],
        layout: {
            name: 'concentric',
            padding: 40
        }
    });
}

/**
 * Fetches current simulation graph and impact analysis.
 */
async function fetchSimulationState() {
    try {
        const res = await fetch("/api/simulation/state");
        if (!res.ok) return;
        const data = await res.json();

        // Update Cytoscape elements
        if (simCy && data.elements) {
            simCy.elements().remove();
            simCy.add(data.elements.nodes);
            simCy.add(data.elements.edges);

            simCy.layout({
                name: 'concentric',
                concentric: function(n) {
                    return n.data('type') === 'Router' ? 2 : 1;
                },
                levelWidth: function() { return 1; },
                padding: 40,
                animate: false
            }).run();
        }

        // Update Impact Dashboard
        updateImpactUI(data.impact);

        // Update dropdown selectors
        populateDropdowns(data.elements.nodes, data.elements.edges, data.impact.failed_nodes);

    } catch (err) {
        console.error("Simulation state error:", err);
    }
}

/**
 * Updates impact analysis metrics cards and lists.
 */
function updateImpactUI(impact) {
    document.getElementById("simTopologyType").textContent = impact.topology_type;
    document.getElementById("simTotalNodes").textContent = impact.total_nodes;
    document.getElementById("simActiveNodes").textContent = impact.active_nodes;
    document.getElementById("simFailedCount").textContent = impact.failed_nodes.length;
    document.getElementById("simComponents").textContent = impact.connected_components_count;

    const sevBadge = document.getElementById("simSeverityBadge");
    if (impact.impact_severity === "CRITICAL") {
        sevBadge.className = "badge badge-offline";
        sevBadge.textContent = "CRITICAL / PARTITIONED";
    } else {
        sevBadge.className = "badge badge-online";
        sevBadge.textContent = "NORMAL / CONNECTED";
    }

    // Unreachable list
    const unreachBox = document.getElementById("unreachableList");
    if (impact.unreachable_from_gateway.length > 0) {
        unreachBox.innerHTML = impact.unreachable_from_gateway.map(id => `<span class="badge badge-warning">${id}</span>`).join(" ");
    } else {
        unreachBox.innerHTML = `<span class="text-muted">All operational nodes can reach the Gateway.</span>`;
    }

    // Failed devices list
    const failedBox = document.getElementById("failedDevicesList");
    if (impact.failed_nodes.length > 0) {
        failedBox.innerHTML = impact.failed_nodes.map(id => `<span class="badge badge-offline">${id} (FAILED)</span>`).join(" ");
    } else {
        failedBox.innerHTML = `<span class="text-muted">No simulated device failures.</span>`;
    }
}

/**
 * Fills device and link dropdown select boxes.
 */
function populateDropdowns(nodes, edges, failedNodes) {
    const selFailDev = document.getElementById("selectFailDevice");
    const selRecDev = document.getElementById("selectRecoverDevice");
    const selSource = document.getElementById("selPathSource");
    const selTarget = document.getElementById("selPathTarget");
    const selDvNode = document.getElementById("selDvNode");
    const selLink = document.getElementById("selectFailLink");

    const activeNodes = nodes.filter(n => !failedNodes.includes(n.data.id));

    // Fail Device dropdown (only currently active devices)
    selFailDev.innerHTML = activeNodes.map(n => `<option value="${n.data.id}">${n.data.label} (${n.data.id})</option>`).join("");
    
    // Recover Device dropdown (only failed devices)
    selRecDev.innerHTML = failedNodes.map(id => `<option value="${id}">${id}</option>`).join("");
    document.getElementById("btnRecoverDevice").disabled = failedNodes.length === 0;

    // Path analysis dropdowns
    const nodeOptions = nodes.map(n => `<option value="${n.data.id}">${n.data.label} (${n.data.id})</option>`).join("");
    selSource.innerHTML = nodeOptions;
    selTarget.innerHTML = nodeOptions;
    selDvNode.innerHTML = nodeOptions;

    // If possible, pick different source and target
    if (nodes.length > 1 && selTarget.options.length > 1) {
        selTarget.selectedIndex = 1;
    }

    // Fail Link dropdown
    selLink.innerHTML = edges.map(e => {
        return `<option value="${e.data.source}|${e.data.target}">${e.data.source} ⟷ ${e.data.target}</option>`;
    }).join("");
}

/**
 * Sends device failure to simulation API.
 */
async function handleFailDevice() {
    const devId = document.getElementById("selectFailDevice").value;
    if (!devId) return;

    const res = await fetch("/api/simulation/device-failure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: devId })
    });
    if (res.ok) {
        await fetchSimulationState();
    }
}

/**
 * Sends device recovery to simulation API.
 */
async function handleRecoverDevice() {
    const devId = document.getElementById("selectRecoverDevice").value;
    if (!devId) return;

    const res = await fetch("/api/simulation/recovery", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: devId })
    });
    if (res.ok) {
        await fetchSimulationState();
    }
}

/**
 * Sends link failure to simulation API.
 */
async function handleFailLink() {
    const linkVal = document.getElementById("selectFailLink").value;
    if (!linkVal) return;
    const [source, target] = linkVal.split("|");

    const res = await fetch("/api/simulation/link-failure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, target })
    });
    if (res.ok) {
        await fetchSimulationState();
    }
}

/**
 * Re-clones fresh state from the live Digital Twin.
 */
async function handleResetSim() {
    const res = await fetch("/api/simulation/reset", { method: "POST" });
    if (res.ok) {
        await fetchSimulationState();
        document.getElementById("pathResultCard").style.display = "none";
        document.getElementById("dvResultCard").style.display = "none";
    }
}

/**
 * Reconfigures simulation topology to star, ring, or mesh.
 */
async function setSimulationTopology(topo) {
    document.querySelectorAll(".btn-topo").forEach(b => b.classList.remove("active"));
    const activeBtn = document.querySelector(`.btn-topo[data-topo="${topo}"]`);
    if (activeBtn) activeBtn.classList.add("active");

    const res = await fetch("/api/simulation/topology", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topology: topo })
    });
    if (res.ok) {
        await fetchSimulationState();
    }
}

/**
 * Calculates Shortest Path (Dijkstra) on the simulated graph.
 */
async function handleCalculatePath() {
    const source = document.getElementById("selPathSource").value;
    const target = document.getElementById("selPathTarget").value;

    if (source === target) {
        alert("Please select two distinct nodes for path calculation.");
        return;
    }

    try {
        const res = await fetch(`/api/simulation/path?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`);
        const data = await res.json();

        const card = document.getElementById("pathResultCard");
        card.style.display = "block";

        // Remove previous highlights
        simCy.elements().removeClass("path-highlight");

        if (data.found) {
            document.getElementById("pathStatusBadge").className = "badge badge-online";
            document.getElementById("pathStatusBadge").textContent = "ROUTE ACTIVE";
            document.getElementById("pathRouteDisplay").innerHTML = data.path.map(id => `<code>${id}</code>`).join(" ➔ ");
            document.getElementById("pathMetricsDisplay").textContent = `Hops: ${data.hop_count} | Estimated Latency: ${data.total_latency} ms`;
            document.getElementById("pathDetailDisplay").textContent = data.details;

            // Highlight path in Cytoscape canvas
            for (let i = 0; i < data.path.length; i++) {
                const node = simCy.getElementById(data.path[i]);
                if (node) node.addClass("path-highlight");

                if (i < data.path.length - 1) {
                    const u = data.path[i];
                    const v = data.path[i + 1];
                    const edges = simCy.edges().filter(e => {
                        const s = e.data('source');
                        const t = e.data('target');
                        return (s === u && t === v) || (s === v && t === u);
                    });
                    edges.addClass("path-highlight");
                }
            }
        } else {
            document.getElementById("pathStatusBadge").className = "badge badge-offline";
            document.getElementById("pathStatusBadge").textContent = "ROUTE BROKEN / PARTITIONED";
            document.getElementById("pathRouteDisplay").innerHTML = `<span class="text-danger">No Path Available</span>`;
            document.getElementById("pathMetricsDisplay").textContent = "Cost: Infinity (No Operational Route)";
            document.getElementById("pathDetailDisplay").textContent = data.details;
        }
    } catch (err) {
        console.error("Path calculation error:", err);
    }
}

/**
 * Computes Distance Vector routing table for a selected node.
 */
async function handleCalculateDV() {
    const node = document.getElementById("selDvNode").value;
    if (!node) return;

    try {
        const res = await fetch(`/api/simulation/distance-vector?node=${encodeURIComponent(node)}`);
        const data = await res.json();

        const card = document.getElementById("dvResultCard");
        card.style.display = "block";
        document.getElementById("dvTargetNodeName").textContent = node;

        const tbody = document.getElementById("dvTableBody");
        tbody.innerHTML = data.table.map(row => {
            const isReachable = row.status === "REACHABLE";
            const badgeClass = isReachable ? "badge-online" : "badge-offline";
            return `
                <tr>
                    <td><strong>${row.destination_name}</strong> (<code>${row.destination}</code>)</td>
                    <td><code>${row.next_hop}</code></td>
                    <td>${row.cost}</td>
                    <td><span class="badge ${badgeClass}">${row.status}</span></td>
                </tr>
            `;
        }).join("");
    } catch (err) {
        console.error("Distance Vector error:", err);
    }
}
