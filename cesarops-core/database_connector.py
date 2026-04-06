#!/usr/bin/env python3
"""
CESAROPS Database Connector
Plugs cesarops_cli.py into existing LAKE_MICHIGAN_CENSUS_2026.db
"""

import sqlite3
from pathlib import Path
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

# Main census database (existing)
CENSUS_DB = Path(__file__).parent / "wreckhunter2000" / "LAKE_MICHIGAN_CENSUS_2026.db"

# Alternative: cesarops_runs.db for detailed run logging
RUNS_DB = Path(__file__).parent / "outputs" / "run_zero" / "cesarops_runs.db"

# ============================================================================
# DATABASE CONNECTION
# ============================================================================

def get_census_db():
    """Get connection to main census database"""
    if not CENSUS_DB.exists():
        raise FileNotFoundError(f"Census database not found: {CENSUS_DB}")
    
    conn = sqlite3.connect(str(CENSUS_DB))
    conn.row_factory = sqlite3.Row
    return conn

def get_runs_db():
    """Get connection to detailed runs database"""
    if not RUNS_DB.exists():
        print(f"Note: Runs database not found: {RUNS_DB}")
        print("  Run init_database.py first or use census_db")
        return None
    
    conn = sqlite3.connect(str(RUNS_DB))
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================================
# CENSUS DB OPERATIONS
# ============================================================================

def log_scan_run_to_census(run_name, tile_count, detection_count, notes=""):
    """Log a scan run to anomaly_hits table in census DB"""
    conn = get_census_db()
    cursor = conn.cursor()
    
    # Insert summary record into anomaly_hits
    # Using lat/lon for Lake Michigan center corridor
    cursor.execute('''
        INSERT INTO anomaly_hits (
            epoch_date, lat, lon, concept, score, classification,
            scene_id, thermal_zscore, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().strftime('%Y-%m-%d'),  # epoch_date required
        42.5,  # Lake Michigan corridor
        -87.0,
        f'cuda_batch_{tile_count}_tiles',
        0.8,
        'cesarops_cuda_test',
        run_name,
        float(detection_count),  # Store total detections as thermal_zscore metric
        datetime.now().isoformat()
    ))
    
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    
    print(f"✓ Logged to census DB: {run_name} (ID={run_id}, {tile_count} tiles, {detection_count} detections)")
    return run_id

def get_stationary_anchors():
    """Get all stationary anchors from census DB"""
    conn = get_census_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, lat, lon, triple_lock_status, swot_persistent_anomaly,
               combined_score, thermal_sink_l8, sar_stability_s1
        FROM stationary_anchors
        ORDER BY id
    ''')
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

def get_new_arrivals(limit=None):
    """Get new arrivals from census DB"""
    conn = get_census_db()
    cursor = conn.cursor()
    
    query = '''
        SELECT id, lat, lon, triple_lock_status, flagged_at,
               score, priority, thermal_sink_l8, sar_stability_s1
        FROM new_arrivals
        ORDER BY id DESC
    '''
    
    if limit:
        query += f' LIMIT {limit}'
    
    cursor.execute(query)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

def update_triple_lock_status(anchor_id, status):
    """Update triple_lock_status for an anchor"""
    conn = get_census_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE stationary_anchors
        SET triple_lock_status = ?, updated_at = ?
        WHERE id = ?
    ''', (status, datetime.now().isoformat(), anchor_id))
    
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    
    return affected > 0

# ============================================================================
# RUNS DB OPERATIONS (detailed logging)
# ============================================================================

def log_detailed_run(run_data, tiles, detections):
    """Log detailed run data to runs DB"""
    conn = get_runs_db()
    if not conn:
        return False
    
    cursor = conn.cursor()
    
    # Insert run
    cursor.execute('''
        INSERT INTO scan_runs (
            run_name, run_type, tile_count, gpu_name,
            total_detections, start_time, end_time, duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        run_data.get('run_name', 'unknown'),
        run_data.get('run_type', 'production'),
        run_data.get('tile_count', 0),
        run_data.get('gpu_name', 'Quadro M2200'),
        run_data.get('total_detections', 0),
        run_data.get('start_time'),
        run_data.get('end_time'),
        run_data.get('duration_seconds')
    ))
    
    run_id = cursor.lastrowid
    
    # Insert tiles
    for tile in tiles:
        cursor.execute('''
            INSERT INTO tiles_processed (
                run_id, tile_path, tile_prefix, satellite_type,
                acquisition_date, bands_processed, raw_anomaly_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            run_id,
            tile.get('tile_path', ''),
            tile.get('tile_prefix', ''),
            tile.get('satellite_type', ''),
            tile.get('acquisition_date', ''),
            tile.get('bands_processed', '[]'),
            tile.get('raw_anomaly_count', 0)
        ))
    
    # Insert detections
    for det in detections:
        cursor.execute('''
            INSERT INTO raw_detections (
                tile_id, run_id, pixel_row, pixel_col,
                wgs84_lat, wgs84_lon, z_score, confidence_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            det.get('tile_id', 0),
            run_id,
            det.get('pixel_row', 0),
            det.get('pixel_col', 0),
            det.get('wgs84_lat', 0),
            det.get('wgs84_lon', 0),
            det.get('z_score', 0),
            det.get('confidence_score', 0)
        ))
    
    conn.commit()
    conn.close()
    
    print(f"✓ Logged detailed run: {run_data.get('run_name')} ({len(detections)} detections)")
    return True

# ============================================================================
# QUERY HELPERS
# ============================================================================

def query_census(sql, params=None):
    """Execute query on census DB and return results"""
    conn = get_census_db()
    cursor = conn.cursor()
    
    if params:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

def print_db_status():
    """Print database status summary"""
    print("="*80)
    print("DATABASE STATUS")
    print("="*80)
    
    # Census DB
    if CENSUS_DB.exists():
        conn = get_census_db()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM stationary_anchors')
        stationary = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM new_arrivals')
        arrivals = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM anomaly_hits')
        anomalies = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM swot_passes')
        swot = cursor.fetchone()[0]
        
        conn.close()
        
        print(f"\nCensus DB: {CENSUS_DB.name}")
        print(f"  ✓ stationary_anchors: {stationary}")
        print(f"  ✓ new_arrivals: {arrivals}")
        print(f"  ✓ anomaly_hits: {anomalies}")
        print(f"  ✓ swot_passes: {swot}")
    else:
        print(f"\nCensus DB: MISSING ({CENSUS_DB})")
    
    # Runs DB
    if RUNS_DB.exists():
        try:
            conn = get_runs_db()
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM scan_runs')
            runs = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM tiles_processed')
            tiles = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM raw_detections')
            dets = cursor.fetchone()[0]
            
            conn.close()
            
            print(f"\nRuns DB: {RUNS_DB.name}")
            print(f"  ✓ scan_runs: {runs}")
            print(f"  ✓ tiles_processed: {tiles}")
            print(f"  ✓ raw_detections: {dets}")
        except sqlite3.OperationalError as e:
            print(f"\nRuns DB: EXISTS but schema needs initialization ({RUNS_DB})")
    else:
        print(f"\nRuns DB: NOT FOUND ({RUNS_DB})")
    
    print("\n" + "="*80)

# ============================================================================
# MAIN
# ============================================================================

def main():
    print_db_status()
    
    print("\n[Sample Queries]")
    
    # Get stationary anchors
    anchors = get_stationary_anchors()
    print(f"\nStationary Anchors ({len(anchors)}):")
    for a in anchors[:5]:
        print(f"  ID {a['id']}: {a['triple_lock_status']} @ ({a['lat']:.4f}, {a['lon']:.4f}) - score: {a['combined_score']}")
    
    # Get new arrivals
    arrivals = get_new_arrivals(limit=5)
    print(f"\nNew Arrivals (latest {len(arrivals)}):")
    for a in arrivals:
        print(f"  ID {a['id']}: {a['triple_lock_status']} @ ({a['lat']:.4f}, {a['lon']:.4f}) - score: {a['score']}")

if __name__ == "__main__":
    main()
