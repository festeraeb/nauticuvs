#!/usr/bin/env python3
"""
DB INGESTOR (LAPTOP)
Watches for new probe JSON files, pushes to SQLite, keeps Tauri live.
"""
import sqlite3
import json
import os
import time
import glob
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = "cesarops_master.db"
WATCH_DIR = "outputs/probes"
PROCESSED_MARKER = "outputs/probes/.processed"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS anomaly_hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lat REAL, lon REAL, 
        sensor TEXT, 
        confidence REAL, 
        concept TEXT, 
        tile_id TEXT,
        ingested_at TEXT
    )""")
    conn.commit()
    conn.close()

def ingest_json(json_path):
    with open(json_path) as f:
        data = json.load(f)
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    inserted = 0
    
    for feat in data.get("features", []):
        geom = feat.get("geometry", {})
        props = feat.get("properties", {})
        coords = geom.get("coordinates", [0, 0])
        if len(coords) >= 2:
            c.execute("""
                INSERT INTO anomaly_hits (lat, lon, sensor, confidence, concept, tile_id, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (coords[1], coords[0], props.get("sensor"), props.get("confidence", 0.0), 
                  props.get("concept", "Unknown"), data.get("tile_id", "auto"), datetime.now(timezone.utc).isoformat()))
            inserted += 1
            
    conn.commit()
    conn.close()
    return inserted

def watch_loop():
    print(f"👀 Watching {WATCH_DIR} for new probes...")
    os.makedirs(WATCH_DIR, exist_ok=True)
    os.makedirs(PROCESSED_MARKER, exist_ok=True)
    
    while True:
        new_files = glob.glob(os.path.join(WATCH_DIR, "*.json"))
        for f in new_files:
            marker = os.path.join(PROCESSED_MARKER, os.path.basename(f))
            if not os.path.exists(marker):
                try:
                    count = ingest_json(f)
                    Path(marker).touch()
                    print(f"✅ Ingested {count} hits from {os.path.basename(f)}")
                except Exception as e:
                    print(f"⚠️ Failed to ingest {f}: {e}")
        time.sleep(5)

if __name__ == "__main__":
    init_db()
    watch_loop()
