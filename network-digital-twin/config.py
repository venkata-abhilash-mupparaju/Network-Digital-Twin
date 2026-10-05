import os

# Base directory of the project
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Data storage settings
DATA_DIR = os.path.join(BASE_DIR, "data")
DATABASE_PATH = os.path.join(DATA_DIR, "network.db")

# Network Monitoring settings
DEFAULT_MONITOR_INTERVAL = 10 # seconds between periodic pings
MONITOR_INTERVAL = 10         # alias for standard naming
DEFAULT_PING_TIMEOUT = 1.0    # seconds timeout for ICMP ping
PING_TIMEOUT = 1.0            # alias
PING_COUNT = 5                # probe packet count for manual tests
LATENCY_THRESHOLD = 150       # milliseconds (warning threshold)
LATENCY_ALERT_THRESHOLD = 150 # alias
PACKET_LOSS_THRESHOLD = 50.0  # percentage threshold for alerts

# Discovery & Subnet Safety limits
MAX_DISCOVERY_CANDIDATES = 512 # Networks larger than this use targeted protection (allows full /24 scan of 254 hosts)
MAX_DISCOVERY_HOSTS = 512      # backward compatibility alias
DISCOVERY_TIMEOUT = 1.0        # Timeout per active probe in seconds
DISCOVERY_WORKERS = 32         # Max concurrent probe threads
DISCOVERY_CONCURRENCY = 32     # alias

# Flask Application settings
SECRET_KEY = os.environ.get("SECRET_KEY", "network-digital-twin-dev-key")
DEBUG = True
HOST = "127.0.0.1"
PORT = 5000

# Central Node definition for hotspot star topology
HOTSPOT_GATEWAY_ID = "GW_HOTSPOT"
HOTSPOT_GATEWAY_NAME = "Mobile Hotspot Gateway"
HOTSPOT_GATEWAY_TYPE = "Router"
