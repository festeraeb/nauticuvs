#!/usr/bin/env python3
"""
SMALL BATCH TEST - 5 Tiles Max

For testing on Xenon without OOM risk.

Processes first 5 tiles found, saves results, populates DB.
"""

import numpy as np
from pathlib import Path
from PIL import Image
import json
import sqlite3
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

SEARCH_DIR = Path("wreckhunter2000/data/cache/census_raw/2025_rossa")
OUTPUT_DIR = Path("outputs/small_batch_test")
DB_PATH = Path("wreckhunter2000/LAKE_MICHIGAN_CENSUS_2026.db")
MAX_TILES = 5

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ============================================================================
# PROCESSING
# ============================================================================

def process_tile(tile_path):
    """Process a single tile"""
    if not tile_path.exists():
        return {'error': 'File not found', 'tile': str(tile_path)}
    
    try:
        img = Image.open(tile_path)
        data = np.array(img, dtype=np.float32)
        
        mean_val = float(np.mean(data))
        std_val = float(np.std(data))
        zscore = (data - mean_val) / (std_val + 1e-6)
        
        anomaly_mask = np.abs(zscore) > 2.5
        anomaly_count = int(np.sum(anomaly_mask))
        
        return {
            'tile': str(tile_path),
            'filename': tile_path.name,
            'mean': mean_val,
            'std': std_val,
            'max_zscore': float(np.max(np.abs(zscore))),
            'anomaly_count': anomaly_count
        }
    except Exception as e:
        return {'error': str(e), 'tile': str(tile_path)}

def init_db(conn):
    """Initialize database"""
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='anomaly_hits'")
    if cursor.fetchone() is None:
        cursor.executescript('''
            CREATE TABLE anomaly_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tile_path TEXT,
                sensor_type TEXT,
                zscore REAL,
                anomaly_count INTEGER,
                detected_at TEXT
            );
            
            CREATE TABLE stationary_anchors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL,
                lon REAL,
                combined_score REAL,
                detection_count INTEGER,
                status TEXT
            );
            
            CREATE TABLE new_arrivals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL,
                lon REAL,
                score REAL,
                status TEXT
            );
        ''')
        conn.commit()

def populate_db(conn, results):
    """Populate database from results"""
    cursor = conn.cursor()
    
    for tile_result in results:
        if 'error' not in tile_result:
            cursor.execute('''
                INSERT INTO anomaly_hits (tile_path, sensor_type, zscore, anomaly_count, detected_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                tile_result['tile'],
                'thermal',
                tile_result.get('max_zscore', 0),
                tile_result.get('anomaly_count', 0),
                datetime.now().isoformat()
            ))
    
    conn.commit()

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*70)
    print("SMALL BATCH TEST - 5 TILES")
    print("="*70)
    print()
    
    # Find tiles
    print(f"[1/4] Finding tiles in {SEARCH_DIR}...")
    tiles = []
    for tif in SEARCH_DIR.glob("*.tif"):
        if tif.stat().st_size > 100000 and '.B04.tif' in tif.name:
            tiles.append(tif)
        if len(tiles) >= MAX_TILES:
            break
    
    print(f"  Found: {len(tiles)} tiles")
    print()
    
    if not tiles:
        print("ERROR: No tiles found!")
        return
    
    # Process tiles
    print("[2/4] Processing tiles...")
    results = []
    
    for i, tile in enumerate(tiles):
        print(f"  [{i+1}/{len(tiles)}] {tile.name}")
        result = process_tile(tile)
        results.append(result)
        
        if 'error' in result:
            print(f"    ✗ Error: {result['error']}")
        else:
            print(f"    ✓ Anomalies: {result['anomaly_count']}, Max Z: {result['max_zscore']:.2f}")
    
    print()
    
    # Save results
    print("[3/4] Saving results...")
    output_file = OUTPUT_DIR / "results.json"
    
    output_data = {
        'timestamp': datetime.now().isoformat(),
        'total_tiles': len(tiles),
        'results': results
    }
    
    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    print(f"  Saved: {output_file}")
    print()
    
    # Populate database
    print("[4/4] Populating database...")
    conn = sqlite3.connect(str(DB_PATH))
    
    try:
        init_db(conn)
        populate_db(conn, results)
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM anomaly_hits")
        count = cursor.fetchone()[0]
        
        print(f"  Database: {DB_PATH}")
        print(f"  anomaly_hits: {count}")
    finally:
        conn.close()
    
    print()
    print("="*70)
    print("SMALL BATCH TEST COMPLETE")
    print("="*70)
    print()
    print(f"Results: {output_file}")
    print(f"Database: {DB_PATH}")
    print()
    
    # Summary
    print("SUMMARY:")
    total_anomalies = sum(r.get('anomaly_count', 0) for r in results if 'error' not in r)
    print(f"  Tiles Processed: {len(tiles)}")
    print(f"  Total Anomalies: {total_anomalies}")
    print(f"  Avg Anomalies/Tile: {total_anomalies / len(tiles):.0f}")

if __name__ == "__main__":
    main()
