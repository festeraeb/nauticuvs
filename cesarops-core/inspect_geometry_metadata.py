#!/usr/bin/env python3
"""
Inspect all available geometry/illumination metadata in HLS tiles.
Read-only diagnostic — no changes to scan pipeline.

Variables we're hunting:
  SUN_AZIMUTH       deg from North, clockwise — shadow direction
  SUN_ELEVATION     deg above horizon — penetration depth multiplier
  SOLAR_ZENITH      = 90 - SUN_ELEVATION — Beer-Lambert path length
  VIEW_ZENITH       sensor look angle — BRDF + atmospheric path
  VIEW_AZIMUTH      sensor look direction
  EARTH_SUN_DIST    AU — radiometric correction factor
  PROCESSING_LEVEL  confirms surface reflectance (L2A/L2SP = atmospherically corrected)

Physics notes:
  Effective water penetration depth  ∝ cos(solar_zenith)
  B02 (blue) 1/e attenuation depth in clear water ≈ 20m × cos(solar_zenith)
  At solar_zenith=60°  → effective depth halved vs nadir (cos60=0.5)
  At solar_zenith=30°  → 87% of nadir depth (cos30=0.866)

  Stumpf ratio bathymetry:
    depth = m1 × [log(Rrs_blue) / log(Rrs_green)] − m0
  With geometry correction:
    effective_depth = raw_depth × cos(solar_zenith) × (1 / cos(view_zenith))

  Shadow stereo (for 3D structure height from shadows):
    object_height = shadow_length × tan(solar_elevation)
    shadow_direction = (sun_azimuth + 180°) mod 360°
"""

import json
from pathlib import Path
import rasterio

TILE_DIR = Path("downloads/hls")

print("=" * 72)
print("GEOMETRY METADATA SCAN — HLS TILES")
print("=" * 72)

all_meta = {}

for tiff in sorted(TILE_DIR.glob("*.tif")):
    with rasterio.open(tiff) as src:
        tags_all = src.tags()           # top-level GDAL tags
        tags_img = src.tags(1)          # band-level tags
        profile   = src.profile
        epsg      = src.crs.to_epsg() if src.crs else None

    # Gather all tags across both levels
    combined = {**tags_all, **tags_img}

    # Pull every geometry-relevant key (case-insensitive scan)
    geo_keys = {}
    for k, v in combined.items():
        kl = k.lower()
        if any(x in kl for x in [
            'sun', 'solar', 'azimuth', 'zenith', 'elevation', 'view',
            'incidence', 'illumin', 'angle', 'satellite', 'sensor',
            'earth_sun', 'distance', 'mean_angle', 'off_nadir',
            'cloud', 'scene_center', 'acquisition', 'date', 'time',
            'spacecraft', 'platform', 'processing_level', 'product_id',
            'station_id', 'wrs', 'mgrs', 'orbit'
        ]):
            geo_keys[k] = v

    stem = tiff.name
    # Deduplicate across bands of same tile
    tile_id = '_'.join(stem.split('.')[:1])
    if tile_id not in all_meta:
        all_meta[tile_id] = {"file": stem, "geo_keys": geo_keys, "all_tags": combined}
        print(f"\n{'─'*60}")
        print(f"FILE: {stem}")
        print(f"  CRS: EPSG:{epsg}  |  shape: {src.width}x{src.height}  |  bands: {src.count}")
        if geo_keys:
            print("  GEOMETRY / ILLUMINATION TAGS:")
            for k, v in sorted(geo_keys.items()):
                print(f"    {k:40s} = {v}")
        else:
            print("  [no geometry tags found in GeoTIFF metadata]")
        if combined and not geo_keys:
            print("  ALL TAGS (for inspection):")
            for k, v in sorted(combined.items())[:30]:
                print(f"    {k:40s} = {v}")

# Check for JSON sidecar files (HLS .json, Landsat MTL, S2 *MTD*.xml)
print("\n" + "=" * 72)
print("SIDECAR / MTL / XML FILES")
print("=" * 72)
for ext in ['*.json', '*.xml', '*MTL*.txt', '*MTL*.xml', '*.MTL']:
    for f in sorted(TILE_DIR.glob(ext)):
        size = f.stat().st_size
        print(f"\n  {f.name}  ({size} bytes)")
        if size < 32768:  # read small files in full
            try:
                txt = f.read_text(errors='replace')
                # Hunt for geometry fields
                lines = txt.splitlines()
                for i, line in enumerate(lines):
                    ll = line.lower()
                    if any(x in ll for x in ['sun', 'solar', 'azimuth', 'zenith',
                                              'elevation', 'view', 'angle', 'incidence',
                                              'cloud', 'date', 'time', 'spacecraft']):
                        print(f"    L{i+1:04d}: {line.strip()}")
            except Exception as e:
                print(f"    read error: {e}")
        else:
            print(f"    (large file — skipped full read)")

print("\n" + "=" * 72)
print("SUMMARY OF GEOMETRY VARIABLES FOUND")
print("=" * 72)
found_any = False
for tile_id, info in all_meta.items():
    if info['geo_keys']:
        found_any = True
        print(f"\n  {info['file']}")
        for k, v in sorted(info['geo_keys'].items()):
            print(f"    {k} = {v}")
if not found_any:
    print("\n  No geometry tags found in GeoTIFF headers.")
    print("  Geometry metadata likely in sidecar .json/.xml files above.")
    print("  OR: metadata is in the HLS CMR JSON — check downloads/ for .json files.")

print()
