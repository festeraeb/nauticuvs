#!/usr/bin/env python3
"""
STEP 1: WIPE DATABASE CLEAN

Keeps schema, deletes all data.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("wreckhunter2000/LAKE_MICHIGAN_CENSUS_2026.db")

def wipe_database():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return False
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    print("Wiping database clean...")
    print()
    
    # Count before delete
    cursor.execute('SELECT COUNT(*) FROM anomaly_hits')
    anomaly_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM stationary_anchors')
    stationary_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM new_arrivals')
    arrivals_count = cursor.fetchone()[0]
    
    print(f"Before wipe:")
    print(f"  anomaly_hits: {anomaly_count}")
    print(f"  stationary_anchors: {stationary_count}")
    print(f"  new_arrivals: {arrivals_count}")
    print()
    
    # Delete all data
    cursor.execute('DELETE FROM anomaly_hits')
    cursor.execute('DELETE FROM stationary_anchors')
    cursor.execute('DELETE FROM new_arrivals')
    cursor.execute('DELETE FROM swot_passes')
    
    # Reset autoincrement counters
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='anomaly_hits'")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='stationary_anchors'")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='new_arrivals'")
    
    conn.commit()
    
    # Verify
    cursor.execute('SELECT COUNT(*) FROM anomaly_hits')
    anomaly_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM stationary_anchors')
    stationary_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM new_arrivals')
    arrivals_count = cursor.fetchone()[0]
    
    print(f"After wipe:")
    print(f"  anomaly_hits: {anomaly_count}")
    print(f"  stationary_anchors: {stationary_count}")
    print(f"  new_arrivals: {arrivals_count}")
    print()
    
    if anomaly_count == 0 and stationary_count == 0 and arrivals_count == 0:
        print("✓ Database wiped successfully")
        return True
    else:
        print("✗ Database wipe FAILED")
        return False
    
    conn.close()

if __name__ == "__main__":
    wipe_database()
