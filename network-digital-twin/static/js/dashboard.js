/**
 * Dashboard Client Script
 * Handles asynchronous telemetry updates and user interactions.
 */

document.addEventListener("DOMContentLoaded", () => {
    const btnRefresh = document.getElementById("btnRefreshStatus");
    
    // Poll API status on button click
    if (btnRefresh) {
        btnRefresh.addEventListener("click", () => {
            fetchSystemStatus(true);
        });
    }

    // Initial status fetch
    fetchSystemStatus(false);
});

/**
 * Fetches Digital Twin and backend system metrics from Flask API.
 * @param {boolean} userTriggered - Whether the fetch was triggered by user click
 */
async function fetchSystemStatus(userTriggered = false) {
    const btnRefresh = document.getElementById("btnRefreshStatus");
    if (userTriggered && btnRefresh) {
        btnRefresh.disabled = true;
        btnRefresh.textContent = "Updating...";
    }

    try {
        const response = await fetch("/api/status");
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Update metric values in DOM
        if (data.summary) {
            document.getElementById("valTotalNodes").textContent = data.summary.total_nodes;
            document.getElementById("valOnlineNodes").textContent = data.summary.online_count;
            document.getElementById("valOfflineNodes").textContent = data.summary.unreachable_count !== undefined ? data.summary.unreachable_count : data.summary.offline_count;
            const staleElem = document.getElementById("valStaleNodes");
            if (staleElem) staleElem.textContent = data.summary.stale_count !== undefined ? data.summary.stale_count : 0;
            document.getElementById("valAvgLatency").innerHTML = `${data.summary.avg_latency} <span class="unit">ms</span>`;
            document.getElementById("valTopologyStatus").textContent = data.summary.is_connected ? "Connected" : "Partitioned";
            
            const quickNodes = document.getElementById("quickNodes");
            const quickEdges = document.getElementById("quickEdges");
            if (quickNodes) quickNodes.textContent = data.summary.total_nodes;
            if (quickEdges) quickEdges.textContent = data.summary.total_edges;
        }

        console.log("Digital Twin status refreshed:", data);
    } catch (error) {
        console.error("Failed to fetch system status:", error);
    } finally {
        if (userTriggered && btnRefresh) {
            btnRefresh.disabled = false;
            btnRefresh.textContent = "Refresh State";
        }
    }
}
