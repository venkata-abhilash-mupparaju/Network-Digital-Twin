import os
import sys
import sqlite3
import logging

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH, DATA_DIR, BASE_DIR

logger = logging.getLogger(__name__)

def get_db_connection():
    """
    Establish a connection to the SQLite database.
    Configures row_factory to sqlite3.Row for column-name access.
    Enforces foreign key constraints.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    """
    Initializes the SQLite database using schema.sql.
    Idempotent: will not overwrite existing data if tables exist.
    """
    schema_path = os.path.join(BASE_DIR, "database", "schema.sql")
    logger.info("Initializing database schema from %s", schema_path)
    
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema file not found at: {schema_path}")
        
    os.makedirs(DATA_DIR, exist_ok=True)
    
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
        
    with get_db_connection() as conn:
        conn.executescript(schema_sql)
        
        # Safely migrate existing tables if columns are missing
        cursor = conn.cursor()
        
        # Check devices table columns
        cursor.execute("PRAGMA table_info(devices)")
        dev_cols = [r["name"] for r in cursor.fetchall()]
        if "network_id" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN network_id TEXT DEFAULT NULL")
        if "mac_address" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN mac_address TEXT DEFAULT NULL")
        if "status" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN status TEXT NOT NULL DEFAULT 'DISCOVERED'")
        if "is_monitored" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN is_monitored INTEGER NOT NULL DEFAULT 1")
        if "authorization_status" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN authorization_status TEXT NOT NULL DEFAULT 'AUTHORIZED'")
        if "source" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN source TEXT NOT NULL DEFAULT 'MANUAL'")
        if "last_seen" not in dev_cols:
            cursor.execute("ALTER TABLE devices ADD COLUMN last_seen TIMESTAMP DEFAULT NULL")
            cursor.execute("UPDATE devices SET last_seen = CURRENT_TIMESTAMP WHERE last_seen IS NULL")
            
        # Check device_status table columns
        cursor.execute("PRAGMA table_info(device_status)")
        stat_cols = [r["name"] for r in cursor.fetchall()]
        if "packets_sent" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN packets_sent INTEGER DEFAULT 1")
        if "packets_received" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN packets_received INTEGER DEFAULT 1")
        if "last_latency" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN last_latency REAL DEFAULT NULL")
        if "min_latency" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN min_latency REAL DEFAULT NULL")
        if "max_latency" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN max_latency REAL DEFAULT NULL")
        if "network_id" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN network_id TEXT DEFAULT NULL")
        if "session_id" not in stat_cols:
            cursor.execute("ALTER TABLE device_status ADD COLUMN session_id INTEGER DEFAULT NULL")
            
        # Create indexes on migrated columns
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_network_id ON devices(network_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_status_network_id ON device_status(network_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_status_session_id ON device_status(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_network_sessions_network_id ON network_sessions(network_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_network_sessions_is_active ON network_sessions(is_active)")

        conn.commit()
        
    logger.info("Database initialized successfully at %s", DATABASE_PATH)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialization and migration completed successfully.")
