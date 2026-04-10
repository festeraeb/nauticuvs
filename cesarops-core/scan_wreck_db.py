#!/usr/bin/env python3
"""Scan Bagrecovery for SQLite databases with verified wreck GPS coordinates."""
import sqlite3
import pathlib

bagrecovery = pathlib.Path(__file__).parent.parent / "Bagrecovery"

print(f"Scanning {bagrecovery} for .db files...")
for db_path in sorted(bagrecovery.rglob("*.db")):
    sz = db_path.stat().st_size
    if sz < 10000:
        continue
    print(f"\n{'='*70}")
    print(f"{db_path.name}  ({sz//1024}KB)  {db_path.parent.name}/")
    try:
        con = sqlite3.connect(str(db_path))
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for tn in tables[:6]:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({tn})").fetchall()]
            cnt = con.execute(f"SELECT COUNT(*) FROM [{tn}]").fetchone()[0]
            print(f"  {tn}: {cnt} rows  cols: {cols[:10]}")

            # Look for coordinate columns
            lat_col = next((c for c in cols if "lat" in c.lower()), None)
            lon_col = next((c for c in cols if "lon" in c.lower()), None)
            name_col = next((c for c in cols if any(x in c.lower()
                             for x in ["name", "vessel", "ship", "title"])), None)

            if lat_col and lon_col:
                has_real = con.execute(
                    f"SELECT COUNT(*) FROM [{tn}] WHERE [{lat_col}] IS NOT NULL "
                    f"AND [{lat_col}] != 0 AND [{lat_col}] != 45.0"
                ).fetchone()[0]
                print(f"    -> {has_real} rows with non-placeholder {lat_col}/{lon_col}")

                if has_real > 0:
                    cols_sel = (
                        [f"[{name_col}]"] if name_col else []
                    ) + [f"[{lat_col}]", f"[{lon_col}]"]
                    sample = con.execute(
                        f"SELECT {','.join(cols_sel)} FROM [{tn}] "
                        f"WHERE [{lat_col}] IS NOT NULL AND [{lat_col}] != 0 "
                        f"AND [{lat_col}] != 45.0 LIMIT 15"
                    ).fetchall()
                    for row in sample:
                        print(f"      {row}")
        con.close()
    except Exception as e:
        print(f"  ERROR: {e}")
