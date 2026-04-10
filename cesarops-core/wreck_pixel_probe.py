#!/usr/bin/env python3
"""
Probe pixel values at known wreck coordinates from all Sep 3 2024 tiles.
Reports raw DN, scene stats (mean/std), and z-score for each wreck site.
"""
import sys, math
from pathlib import Path
import rasterio
from rasterio.warp import transform as warp_transform
import numpy as np

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

TILE_DIR = Path("downloads/hls")

# Target wrecks (name, lat, lon, depth_ft)
TARGETS = [
    ("Minneapolis",    45.80852, -84.73173, 124),
    ("William Young",  45.81295, -84.69872, 120),
    ("M. Stalker",     45.79367, -84.68437,  85),
    ("Cedarville",     45.78725, -84.67080,  40),
    ("Eber Ward",      45.81272, -84.81888, 128),
    ("Sandusky",       45.79932, -84.83748,  77),
]

# Bands of interest in priority order
BAND_PRIORITY = ['blue', 'green', 'red', 'nir', 'lwir11', 'swir16']

def get_pixel(src, lat, lon):
    """Return (row, col, value) at lat/lon, or None if outside raster."""
    xs, ys = warp_transform('EPSG:4326', src.crs, [lon], [lat])
    try:
        row, col = src.index(xs[0], ys[0])
    except Exception:
        return None
    if not (0 <= row < src.height and 0 <= col < src.width):
        return None
    val = src.read(1)[row, col]
    return row, col, float(val)

def scene_stats(src, mask_nodata=True):
    data = src.read(1).astype(float)
    nd = src.nodata
    if nd is not None:
        data = data[data != nd]
    # mask zeros (water nodata often 0)
    data = data[data > 0]
    if len(data) == 0:
        return None, None
    return float(np.mean(data)), float(np.std(data))

# Group tiles by sensor prefix
tiles = sorted(TILE_DIR.glob("*.tif"))
sensors = {}
for t in tiles:
    parts = t.stem.split('.')
    if len(parts) < 2:
        continue
    sensor = parts[0]
    band = parts[1]
    sensors.setdefault(sensor, {})[band] = t

print("=" * 80)
print("WRECK PIXEL PROBE — Sep 3 2024 — all available bands")
print("=" * 80)

for sensor_id, band_map in sorted(sensors.items()):
    print(f"\nSENSOR: {sensor_id}")
    print(f"  Bands: {sorted(band_map.keys())}")

    for band in BAND_PRIORITY:
        if band not in band_map:
            continue
        tif_path = band_map[band]
        with rasterio.open(tif_path) as src:
            mean, std = scene_stats(src)
            if mean is None or std is None or std == 0:
                continue

            print(f"\n  BAND: {band}  (scene mean={mean:.1f}  std={std:.1f})")
            any_in_tile = False
            for name, lat, lon, depth in TARGETS:
                result = get_pixel(src, lat, lon)
                if result is None:
                    continue
                any_in_tile = True
                row, col, val = result
                z = (val - mean) / std
                # SCL class meanings for S2: 6=water, 8=cloud_med, 9=cloud_high
                flag = ""
                if band == 'scl':
                    scl_labels = {0:'nodata',1:'sat',2:'dark',3:'shadow',4:'veg',
                                  5:'bare',6:'WATER',7:'unclass',8:'cld_med',
                                  9:'cld_hi',10:'cirrus',11:'snow'}
                    flag = f"  [{scl_labels.get(int(val), '?')}]"
                elif abs(z) >= 3.0:
                    flag = "  *** HIGH z-score"
                elif abs(z) >= 2.0:
                    flag = "  ** elevated"
                elif abs(z) >= 1.5:
                    flag = "  * slightly elevated"
                print(f"    {name:<20} ({lat:.5f},{lon:.7f})  "
                      f"depth={depth:>3}ft  "
                      f"px=({row},{col})  val={val:>8.1f}  z={z:>+6.2f}{flag}")
            if not any_in_tile:
                print(f"    (no target coords inside this tile)")

print()
print("=" * 80)
print("SCL SCENE CLASSIFICATION CHECK (S2 only)")
print("=" * 80)
for sensor_id, band_map in sorted(sensors.items()):
    if 'scl' not in band_map:
        continue
    with rasterio.open(band_map['scl']) as src:
        print(f"\n  {sensor_id}")
        for name, lat, lon, depth in TARGETS:
            result = get_pixel(src, lat, lon)
            if result is None:
                continue
            row, col, val = result
            scl_labels = {0:'nodata',1:'saturated',2:'dark_pixel',3:'cloud_shadow',
                          4:'vegetation',5:'bare_soil',6:'WATER',7:'unclassified',
                          8:'cloud_medium',9:'cloud_high',10:'cirrus',11:'snow'}
            label = scl_labels.get(int(val), f'unknown({int(val)})')
            print(f"    {name:<20}  SCL={int(val):>2} ({label})")
