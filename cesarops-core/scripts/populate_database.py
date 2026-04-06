#!/usr/bin/env python3
"""
POPULATE DATABASE FROM PROCESSING RESULTS

Takes JSON results from full_lake_michigan_run.py and loads into SQLite DB.

Usage:
    python populate_database.py outputs/full_lake_run/20260402_081510/full_results.json
"""

import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

DB_PATH = Path("wreckhunter2000/LAKE_MICHIGAN_CENSUS_2026.db")

# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def init_db(conn):
    """Initialize database schema if needed"""
    cursor = conn.cursor()
    
    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='anomaly_hits'")
    if cursor.fetchone() is None:
        print("  Creating database schema...")
        
        cursor.executescript('''
            -- Anomaly hits (all detections)
            CREATE TABLE anomaly_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tile_path TEXT,
                sensor_type TEXT,
                zscore REAL,
                anomaly_count INTEGER,
                lat REAL,
                lon REAL,
                detected_at TEXT
            );
            
            -- Stationary anchors (repeatable detections)
            CREATE TABLE stationary_anchors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL,
                lon REAL,
                combined_score REAL,
                detection_count INTEGER,
                status TEXT
            );
            
            -- New arrivals (single detections)
            CREATE TABLE new_arrivals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL,
                lon REAL,
                score REAL,
                status TEXT
            );
            
            -- SWOT passes
            CREATE TABLE swot_passes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pass_date TEXT,
                granule_id TEXT,
                coverage_area TEXT
            );
        ''')
        
        conn.commit()
        print("  Schema created")
    else:
        print("  Schema exists")

def populate_from_results(results_file, conn):
    """Populate database from processing results JSON"""
    
    cursor = conn.cursor()
    
    print(f"Loading results from: {results_file}")
    with open(results_file) as f:
        results = json.load(f)
    
    run_timestamp = results.get('run_timestamp', 'unknown')
    tiles_processed = results.get('processed', 0)
    
    print(f"  Run: {run_timestamp}")
    print(f"  Tiles: {tiles_processed}")
    print()
    
    # Insert anomaly hits
    print("Inserting anomaly hits...")
    anomaly_count = 0
    
    for tile_result in results.get('results', []):
        tile_path = tile_result.get('tile', 'unknown')
        
        for sensor_name, sensor_data in tile_result.get('sensors', {}).items():
            if 'anomaly_count' in sensor_data:
                cursor.execute('''
                    INSERT INTO anomaly_hits (tile_path, sensor_type, zscore, anomaly_count, detected_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    tile_path,
                    sensor_name,
                    sensor_data.get('max_zscore', 0),
                    sensor_data.get('anomaly_count', 0),
                    datetime.now().isoformat()
                ))
                anomaly_count += 1
    
    conn.commit()
    print(f"  Inserted {anomaly_count} anomaly hits")
    print()
    
    # Summary
    print("Database Summary:")
    cursor.execute("SELECT COUNT(*) FROM anomaly_hits")
    print(f"  anomaly_hits: {cursor.fetchone()[0]}")
    
    cursor.execute("SELECT COUNT(*) FROM stationary_anchors")
    print(f"  stationary_anchors: {cursor.fetchone()[0]}")
    
    cursor.execute("SELECT COUNT(*) FROM new_arrivals")
    print(f"  new_arrivals: {cursor.fetchone()[0]}")
    
    cursor.execute("SELECT COUNT(*) FROM swot_passes")
    print(f"  swot_passes: {cursor.fetchone()[0]}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*70)
    print("DATABASE POPULATOR")
    print("="*70)
    print()
    
    if len(sys.argv) < 2:
        print("Usage: python populate_database.py <results.json>")
        print()
        print("Example:")
        print("  python populate_database.py outputs/full_lake_run/20260402_081510/full_results.json")
        return
    
    results_file = Path(sys.argv[1])
    
    if not results_file.exists():
        print(f"ERROR: Results file not found: {results_file}")
        return
    
    # Ensure DB directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Connect to DB
    print(f"Database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    
    try:
        # Initialize schema
        init_db(conn)
        print()
        
        # Populate from results
        populate_from_results(results_file, conn)
        print()
        
        print("="*70)
        print("DATABASE POPULATED SUCCESSFULLY")
        print("="*70)
        
    finally:
        conn.close()

if __name__ == "__main__":
    main()
