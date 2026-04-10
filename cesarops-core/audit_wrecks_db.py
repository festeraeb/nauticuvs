"""Audit wrecks.db for coordinate quality - identify genuine vs centroid GPS entries."""
import sqlite3
import json
from collections import Counter

DB_PATH = r"C:\Users\thomf\programming\Bagrecovery\db\wrecks.db"

def run_audit():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # List all tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print("Tables:", tables)

    for tbl in tables:
        cur.execute(f"PRAGMA table_info({tbl})")
        cols = [r[1] for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur.fetchone()[0]
        print(f"\n  {tbl}: {cnt} rows, cols: {cols}")

    # Focus on features table
    if "features" not in tables:
        print("No features table!")
        return

    cur.execute("PRAGMA table_info(features)")
    all_cols = [(r[0], r[1]) for r in cur.fetchall()]
    print("\nfeatures full schema:")
    for idx, name in all_cols:
        print(f"  [{idx}] {name}")

    # Find lat/lon columns
    col_names = [c[1].lower() for c in all_cols]
    lat_col = next((c[1] for c in all_cols if "lat" in c[1].lower()), None)
    lon_col = next((c[1] for c in all_cols if "lon" in c[1].lower() or "lng" in c[1].lower()), None)
    name_col = next((c[1] for c in all_cols if "name" in c[1].lower()), None)
    print(f"\nLat col: {lat_col}, Lon col: {lon_col}, Name col: {name_col}")

    if not lat_col or not lon_col:
        print("No lat/lon columns!")
        return

    # Count rows with non-null GPS
    cur.execute(f"SELECT COUNT(*) FROM features WHERE {lat_col} IS NOT NULL AND {lon_col} IS NOT NULL")
    gps_count = cur.fetchone()[0]
    print(f"\nRows with GPS: {gps_count}")

    # Find centroid clusters - coordinates shared by many wrecks
    cur.execute(f"SELECT ROUND({lat_col}, 2), ROUND({lon_col}, 2), COUNT(*) as cnt FROM features WHERE {lat_col} IS NOT NULL GROUP BY ROUND({lat_col}, 2), ROUND({lon_col}, 2) ORDER BY cnt DESC LIMIT 30")
    cluster_rows = cur.fetchall()
    print("\nTop 30 coordinate clusters (lat, lon, count):")
    total_clustered = 0
    high_count_clusters = []
    for lat, lon, cnt in cluster_rows:
        print(f"  {lat:8.4f}, {lon:9.4f} → {cnt} wrecks")
        if cnt > 5:
            total_clustered += cnt
            high_count_clusters.append((lat, lon))
    print(f"\nWrecks in high-density clusters (>5 per coord pair): {total_clustered}")

    # Find singletons - unique coordinate pairs
    cur.execute(f"""
        SELECT {name_col}, {lat_col}, {lon_col}
        FROM features f
        WHERE {lat_col} IS NOT NULL
        AND (
            SELECT COUNT(*) FROM features f2
            WHERE ROUND(f2.{lat_col}, 3) = ROUND(f.{lat_col}, 3)
            AND ROUND(f2.{lon_col}, 3) = ROUND(f.{lon_col}, 3)
        ) = 1
        ORDER BY {lat_col}
        LIMIT 100
    """)
    singletons = cur.fetchall()
    print(f"\nUnique GPS entries (no other wreck at same ~0.001deg): {len(singletons)}")
    for name, lat, lon in singletons[:50]:
        print(f"  {name:<45} lat={lat:.4f}  lon={lon:.4f}")

    # Check if there's a coordinate_source or confidence column
    coord_source_col = next((c[1] for c in all_cols if "source" in c[1].lower() or "conf" in c[1].lower()), None)
    print(f"\nCoord source/confidence column: {coord_source_col}")
    if coord_source_col:
        cur.execute(f"SELECT DISTINCT {coord_source_col}, COUNT(*) FROM features GROUP BY {coord_source_col}")
        for row in cur.fetchall():
            print(f"  {row}")

    # Export singletons
    if singletons:
        results = [{"name": r[0], "lat": r[1], "lon": r[2]} for r in singletons]
        with open("outputs/wrecks_db_singletons.json", "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSingletons saved to outputs/wrecks_db_singletons.json")

    con.close()

run_audit()
