#!/usr/bin/env python3
"""
CESAROPS Database Initialization
Creates SQLite database with comprehensive schema for logging all scans and detections
"""

import sqlite3
from pathlib import Path
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

DB_PATH = Path(__file__).parent / "outputs" / "cesarops.db"
SCHEMA_PATH = Path(__file__).parent / "cesarops_comprehensive_schema.sql"

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_database():
    """Initialize database with comprehensive schema"""
    
    # Create outputs directory
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Remove existing database for clean start
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing database: {DB_PATH}")
    
    print(f"Creating database: {DB_PATH}")
    
    # Connect and create schema
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Read and execute schema
    if SCHEMA_PATH.exists():
        print(f"Loading schema from: {SCHEMA_PATH}")
        with open(SCHEMA_PATH, 'r') as f:
            schema_sql = f.read()
        cursor.executescript(schema_sql)
    else:
        print("Schema file not found, creating minimal schema...")
        create_minimal_schema(cursor)
    
    # Insert initial metadata
    cursor.execute('''
        INSERT INTO metadata (key, value, updated_at)
        VALUES ('database_version', '1.0', ?)
    ''', (datetime.now().isoformat(),))
    
    cursor.execute('''
        INSERT INTO metadata (key, value, updated_at)
        VALUES ('created_at', ?, ?)
    ''', (datetime.now().isoformat(), datetime.now().isoformat()))
    
    conn.commit()
    
    # Verify tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    print(f"\n✓ Database created successfully: {DB_PATH}")
    print(f"✓ Tables created: {len(tables)}")
    for table in tables:
        print(f"    - {table}")
    
    return DB_PATH

def create_minimal_schema(cursor):
    """Create minimal schema if comprehensive schema not found"""
    
    cursor.executescript('''
        -- Scan runs metadata
        CREATE TABLE IF NOT EXISTS scan_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_name TEXT,
            run_type TEXT,
            input_directory TEXT,
            output_directory TEXT,
            tile_count INTEGER,
            min_confidence REAL,
            gpu_name TEXT,
            total_detections INTEGER DEFAULT 0,
            start_time DATETIME,
            end_time DATETIME,
            duration_seconds REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Tiles processed
        CREATE TABLE IF NOT EXISTS tiles_processed (
            tile_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            tile_path TEXT,
            tile_prefix TEXT,
            satellite_type TEXT,
            acquisition_date TEXT,
            bands_processed TEXT,
            width_pixels INTEGER,
            height_pixels INTEGER,
            gpu_compute_time_seconds REAL,
            thermal_mean REAL,
            thermal_stddev REAL,
            raw_anomaly_count INTEGER,
            FOREIGN KEY (run_id) REFERENCES scan_runs(run_id)
        );
        
        -- Raw detections
        CREATE TABLE IF NOT EXISTS raw_detections (
            detection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tile_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            pixel_row INTEGER,
            pixel_col INTEGER,
            wgs84_lat REAL,
            wgs84_lon REAL,
            z_score REAL,
            confidence_score REAL,
            FOREIGN KEY (tile_id) REFERENCES tiles_processed(tile_id),
            FOREIGN KEY (run_id) REFERENCES scan_runs(run_id)
        );
        
        -- Metadata
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    ''')

def test_database():
    """Test database with sample data"""
    
    print("\n" + "="*80)
    print("DATABASE TEST")
    print("="*80)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Insert test scan run
    cursor.execute('''
        INSERT INTO scan_runs (
            run_name, run_type, tile_count, gpu_name, 
            total_detections, start_time, end_time, duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        'test_run_001',
        'cuda_test',
        20,
        'Quadro M2200',
        11447267,
        datetime.now().isoformat(),
        datetime.now().isoformat(),
        45.5
    ))
    
    run_id = cursor.lastrowid
    
    # Insert test tiles
    test_tiles = [
        ('S2C_16TDN_20250916_0_L2A.B01.tif', 'S30', '2025-09-16', 83157),
        ('S2C_16TDN_20250916_0_L2A.B02.tif', 'S30', '2025-09-16', 3169954),
        ('HLS.S30.T16TDN.2025244T165711.B11.tif', 'S30', '2025-09-01', 1204182),
    ]
    
    for tile_path, sat_type, acq_date, anomaly_count in test_tiles:
        cursor.execute('''
            INSERT INTO tiles_processed (
                run_id, tile_path, tile_prefix, satellite_type,
                acquisition_date, bands_processed, raw_anomaly_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            run_id,
            tile_path,
            tile_path.split('.')[0],
            sat_type,
            acq_date,
            '["B01", "B11"]',
            anomaly_count
        ))
    
    # Query and display results
    print("\n[1] Scan Runs:")
    cursor.execute("SELECT run_id, run_name, run_type, total_detections, duration_seconds FROM scan_runs")
    for row in cursor.fetchall():
        print(f"    Run {row[0]}: {row[1]} ({row[2]}) - {row[3]:,} detections in {row[4]:.1f}s")
    
    print("\n[2] Tiles Processed:")
    cursor.execute('''
        SELECT tile_id, tile_path, satellite_type, acquisition_date, raw_anomaly_count 
        FROM tiles_processed
    ''')
    for row in cursor.fetchall():
        print(f"    Tile {row[0]}: {row[1]} - {row[4]:,} anomalies")
    
    print("\n[3] Database Statistics:")
    cursor.execute("SELECT COUNT(*) FROM scan_runs")
    print(f"    Total scan runs: {cursor.fetchone()[0]}")
    
    cursor.execute("SELECT COUNT(*) FROM tiles_processed")
    print(f"    Total tiles: {cursor.fetchone()[0]}")
    
    cursor.execute("SELECT SUM(raw_anomaly_count) FROM tiles_processed")
    total = cursor.fetchone()[0]
    print(f"    Total anomalies: {total:,}" if total else "    Total anomalies: 0")
    
    conn.commit()
    conn.close()
    
    print("\n✓ Database test complete")

def main():
    print("="*80)
    print("CESAROPS DATABASE INITIALIZATION")
    print("="*80)
    print()
    
    # Initialize
    db_path = init_database()
    
    # Test
    test_database()
    
    print("\n" + "="*80)
    print("DATABASE READY")
    print("="*80)
    print(f"\nDatabase location: {DB_PATH.absolute()}")
    print("\nNext steps:")
    print("  1. Run: python cuda_test_kmz.py --with-db  (to log CUDA tests)")
    print("  2. Run: python cesarops_cli.py  (to process and log scans)")
    print("  3. Query: sqlite3 outputs/cesarops.db 'SELECT * FROM scan_runs'")
    print("="*80)

if __name__ == "__main__":
    main()
