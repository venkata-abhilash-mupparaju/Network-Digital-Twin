-- Network Digital Twin Database Schema (SQLite)

-- 1. Monitored Networks: Track different hotspots and Wi-Fi subnets
CREATE TABLE IF NOT EXISTS monitored_networks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    network_id TEXT UNIQUE NOT NULL,       -- e.g. 'Wi-Fi|192.168.43.0/24|192.168.43.1'
    interface TEXT,
    local_ip TEXT,
    gateway TEXT,
    network_cidr TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Network Sessions: Track connection sessions per network
CREATE TABLE IF NOT EXISTS network_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    network_id TEXT NOT NULL,
    interface TEXT,
    local_ip TEXT,
    gateway TEXT,
    network_cidr TEXT,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP DEFAULT NULL,
    is_active INTEGER DEFAULT 1
);

-- 3. Devices: Registered test devices on the authorized network
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT UNIQUE NOT NULL,
    device_name TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    device_type TEXT NOT NULL DEFAULT 'Computer',
    description TEXT,
    network_id TEXT DEFAULT NULL,          -- The network where device was registered
    mac_address TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'DISCOVERED', -- 'ONLINE', 'UNREACHABLE', 'STALE', 'HISTORICAL', 'DISCOVERED', 'NOT_VISIBLE'
    is_monitored INTEGER NOT NULL DEFAULT 1,   -- 1 = Monitored in Live Digital Twin, 0 = Unmonitored
    authorization_status TEXT NOT NULL DEFAULT 'AUTHORIZED', -- 'AUTHORIZED', 'PENDING'
    source TEXT NOT NULL DEFAULT 'MANUAL',     -- 'ARP', 'ICMP', 'MANUAL'
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Device Status: Historical telemetry captured during periodic monitoring
CREATE TABLE IF NOT EXISTS device_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL,                  -- 'ONLINE', 'UNREACHABLE', 'OFFLINE', 'UNKNOWN'
    latency REAL DEFAULT NULL,             -- Average round-trip time in milliseconds
    last_latency REAL DEFAULT NULL,        -- Most recent ping response time
    min_latency REAL DEFAULT NULL,         -- Minimum RTT observed in probe batch
    max_latency REAL DEFAULT NULL,         -- Maximum RTT observed in probe batch
    packets_sent INTEGER DEFAULT 1,        -- Probes transmitted
    packets_received INTEGER DEFAULT 1,    -- Probes returned
    packet_loss REAL DEFAULT 0.0,          -- Percentage 0.0 to 100.0
    network_id TEXT DEFAULT NULL,          -- Associated network context
    session_id INTEGER DEFAULT NULL,       -- Associated network session
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

-- 5. Network Events: Audit log of transitions, alerts, and what-if simulation occurrences
CREATE TABLE IF NOT EXISTS network_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    event_type TEXT NOT NULL,              -- 'STATUS_CHANGE', 'ALERT', 'NETWORK_CHANGE', 'SIMULATION', 'SYSTEM'
    message TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indices for performance on timestamp and device lookups
CREATE INDEX IF NOT EXISTS idx_device_status_device_id ON device_status(device_id);
CREATE INDEX IF NOT EXISTS idx_device_status_timestamp ON device_status(timestamp);
CREATE INDEX IF NOT EXISTS idx_network_events_timestamp ON network_events(timestamp);

