#!/usr/bin/env python3
"""
Mackinac Bridge coordinate calibration diagnostic.
No changes to scan logic — read-only analysis only.

Reference points (surveyed/GPS-verified):
  North tower anchor  : 45.81656 N, -84.72769 W  [user-provided UTM 16T 676529.17E 5076177.10N]
  South tower center  : 45.78633 N, -84.72705 W  [USGS topo / Google Maps cross-check]
  Bridge midpoint     : 45.80140 N, -84.72740 W  [geometric midpoint north/south towers]

Tests performed per tile:
  1. Report CRS, EPSG, pixel resolution, geotransform
  2. Forward:  bridge lat/lon -> file CRS (easting/northing) -> row/col
  3. Backward: row/col -> file CRS -> lat/lon  (exactly what scan does)
  4. Round-trip error reported in METRES
  5. Report what 3 km error in lat/lon looks like in pixel space (sanity check)
"""

import sys, math
from pathlib import Path
import rasterio
from rasterio.warp import transform as warp_transform

# ── Ground-truth reference points ────────────────────────────────────────────
REFS = [
    ("North tower anchor", 45.81656, -84.72769),
    ("South tower center", 45.78633, -84.72705),
    ("Bridge midpoint",    45.80140, -84.72740),
]

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

TILE_DIR = Path("downloads/hls")
tiles = sorted(TILE_DIR.glob("*.tif"))

print("=" * 72)
print("CESAROPS — MACKINAC BRIDGE COORDINATE CALIBRATION DIAGNOSTIC")
print(f"Tile dir: {TILE_DIR}  ({len(tiles)} tif files found)")
print("=" * 72)

for tiff in tiles:
    try:
        with rasterio.open(tiff) as src:
            t = src.transform
            epsg = src.crs.to_epsg() if src.crs else "??"
            res_x = abs(t.a)
            res_y = abs(t.e)
            print(f"\n{'─'*60}")
            print(f"FILE:    {tiff.name}")
            print(f"  CRS:   {src.crs}  (EPSG:{epsg})")
            print(f"  Shape: {src.width} cols x {src.height} rows  |  pixel {res_x:.2f}m x {res_y:.2f}m")
            print(f"  Geotransform: {t}")

            for label, ref_lat, ref_lon in REFS:
                # Step 1: lat/lon -> file CRS native coords
                xs_native, ys_native = warp_transform(
                    'EPSG:4326', src.crs, [ref_lon], [ref_lat]
                )
                x_nat, y_nat = xs_native[0], ys_native[0]

                # Step 2: native coords -> pixel row/col
                try:
                    row, col = src.index(x_nat, y_nat)
                except Exception as e:
                    print(f"  [{label}] Outside tile bounds ({e})")
                    continue

                in_bounds = (0 <= row < src.height) and (0 <= col < src.width)
                if not in_bounds:
                    print(f"  [{label}] row={row} col={col} — outside raster extent")
                    continue

                # Step 3: pixel -> native coords (EXACTLY what scan pipeline does)
                px_x, px_y = src.xy(row, col)

                # Step 4: native -> WGS84  (EXACTLY what scan pipeline does)
                lons_out, lats_out = warp_transform(src.crs, 'EPSG:4326', [px_x], [px_y])
                lat_out = lats_out[0]
                lon_out = lons_out[0]

                err_m = haversine_m(ref_lat, ref_lon, lat_out, lon_out)
                dlat  = lat_out - ref_lat
                dlon  = lon_out - ref_lon

                print(f"\n  [{label}]  ref=({ref_lat:.5f}, {ref_lon:.5f})")
                print(f"    → native CRS:       x={x_nat:.3f}  y={y_nat:.3f}")
                print(f"    → pixel:            row={row}  col={col}")
                print(f"    → px center native: x={px_x:.3f}  y={px_y:.3f}")
                print(f"    → round-trip WGS84: lat={lat_out:.6f}  lon={lon_out:.6f}")
                print(f"    → ERROR:            Δlat={dlat*111000:.1f}m  Δlon={dlon*77600:.1f}m  "
                      f"total={err_m:.1f}m  ({err_m/1000:.3f} km)")

                # How many pixels is 3 km?
                px_3km = 3000 / res_x
                print(f"    [ref] 3 km = {px_3km:.1f} pixels ({res_x:.0f}m pixel)")

    except Exception as e:
        print(f"\nERROR opening {tiff.name}: {e}")

print("\n" + "=" * 72)
print("NOTES:")
print("  Round-trip error > 1 pixel = geotransform or CRS mismatch")
print("  Round-trip error = 0m     = conversion is internally consistent")
print("  (Even 0m round-trip error can still mean the FILE has a geolocation")
print("   offset baked in — to catch that we need the optical bridge check below)")
print()
print("OPTICAL BRIDGE CHECK:")
print("  Open NIR/Red band, find the brightest straight-line feature near the bridge,")
print("  compare pixel location to ground-truth. That catches baked-in tile offsets.")
print("=" * 72)
