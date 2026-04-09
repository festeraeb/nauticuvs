#!/usr/bin/env python3
"""
Fetch and display all STAC/CMR geometry metadata for the HLS granules we downloaded.
Read-only diagnostic.

Variables of interest for 3D bathymetric mapping:
  view:sun_azimuth    - shadow direction vector
  view:sun_elevation  - Beer-Lambert depth penetration multiplier
  view:off_nadir      - sensor look angle (BRDF path length)
  eo:cloud_cover      - quality
  platform / datetime - which sensor, when
"""

import json, os, sys, math
from pathlib import Path
import requests

sys.stdout.reconfigure(encoding='utf-8')

_dotenv = {}
env_path = Path('.env')
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            _dotenv[k.strip()] = v.strip()

TOKEN = os.environ.get('EARTHDATA_TOKEN', _dotenv.get('EARTHDATA_TOKEN', ''))

# Granule IDs derived from downloaded filenames
GRANULES = [
    # (collection, granule_ur, short_name)
    ('HLSL30.v2.0', 'HLS.L30.T16TFR.2024247T163117.v2.0', 'Landsat HLS 16TFR Sep3'),
    ('HLSS30.v2.0', 'HLS.S30.T16TFR.2024247T163117.v2.0', 'Sentinel HLS 16TFR Sep3'),
    ('HLSS30.v2.0', 'HLS.S30.T16TGR.2024247T163117.v2.0', 'Sentinel HLS 16TGR Sep3'),
    ('HLSS30.v2.0', 'HLS.S30.T16TGS.2024247T163117.v2.0', 'Sentinel HLS 16TGS Sep3'),
]

# Also search CMR STAC directly for the exact granules by bounding box + date
STAC_SEARCH = "https://cmr.earthdata.nasa.gov/stac/LPCLOUD/search"
STAC_COLLECTIONS = ["HLSL30.v2.0", "HLSS30.v2.0"]

headers = {'Authorization': f'Bearer {TOKEN}'} if TOKEN else {}

print("=" * 72)
print("STAC GEOMETRY METADATA — HLS Sep 3 2024 Granules")
print("=" * 72)
if not TOKEN:
    print("  [!] No EARTHDATA_TOKEN — will try public endpoint (may get less metadata)")

# Search by date + bbox covering Straits of Mackinac
search_body = {
    "collections": STAC_COLLECTIONS,
    "datetime": "2024-09-03T00:00:00Z/2024-09-03T23:59:59Z",
    "bbox": [-84.85, 45.70, -84.10, 46.10],
    "limit": 20
}

try:
    r = requests.post(STAC_SEARCH, json=search_body, headers=headers, timeout=30)
    r.raise_for_status()
    results = r.json()
except Exception as e:
    print(f"  STAC search failed: {e}")
    results = {"features": []}

features = results.get('features', [])
print(f"\nFound {len(features)} granules via STAC search\n")

# Geometry variables useful for bathymetry
BATHY_KEYS = [
    'view:sun_azimuth', 'view:sun_elevation', 'view:off_nadir',
    'eo:cloud_cover', 'platform', 'datetime',
    'MEAN_SUN_AZIMUTH_ANGLE', 'MEAN_SUN_ZENITH_ANGLE',
    'MEAN_VIEW_AZIMUTH_ANGLE', 'MEAN_VIEW_ZENITH_ANGLE',
    'SPATIAL_COVERAGE', 'CLOUD_COVERAGE',
    'SUN_AZIMUTH', 'SUN_ELEVATION', 'EARTH_SUN_DISTANCE',
]

for feat in features:
    props = feat.get('properties', {})
    feat_id = feat.get('id', '?')
    print(f"{'─'*60}")
    print(f"GRANULE: {feat_id}")
    print(f"  Collection: {props.get('collection', '?')}")
    print(f"  Platform:   {props.get('platform', '?')}")
    print(f"  Datetime:   {props.get('datetime', '?')}")

    # Print all geometry/science properties
    print("  GEOMETRY PROPERTIES:")
    found = False
    for k in sorted(props.keys()):
        kl = k.lower()
        if any(x in kl for x in ['sun', 'solar', 'azimuth', 'zenith', 'elevation',
                                   'view', 'nadir', 'cloud', 'angle', 'platform',
                                   'spacecraft', 'coverage', 'earth_sun', 'incidence']):
            print(f"    {k:45s} = {props[k]}")
            found = True
    if not found:
        print("    [none found — dumping all properties]")
        for k, v in sorted(props.items()):
            print(f"    {k:45s} = {v}")

    # Also show asset keys (band files available)
    assets = feat.get('assets', {})
    print(f"  ASSETS ({len(assets)} bands/files):")
    for k in sorted(assets.keys())[:8]:
        print(f"    {k}")

# ── Physics summary ───────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("BATHYMETRIC PHYSICS VARIABLES — Sep 3, 2024, ~45.8°N")
print("=" * 72)

# If STAC returned sun angles, compute physics params
sun_el = None
sun_az = None
for feat in features:
    props = feat.get('properties', {})
    for k, v in props.items():
        kl = k.lower()
        if 'sun_elevation' in kl or 'sun_el' in kl:
            try: sun_el = float(v)
            except: pass
        if 'sun_az' in kl or 'azimuth' in kl and 'sun' in kl:
            try: sun_az = float(v)
            except: pass
    if sun_el is not None:
        break

# Fallback: compute approximate sun angles from date/time/location
# LC09 passes at ~10:30 AM local solar time (descending)
# Sep 3 at 45.8°N: sun elevation at ~10:30 local solar time
from datetime import datetime, timezone
import math

lat_rad = math.radians(45.8)
# Sep 3 = day 246. Declination:
doy = 246
decl = math.radians(-23.45 * math.cos(math.radians(360/365 * (doy + 10))))
# Hour angle at ~10:30 local solar time = -22.5° (before noon)
# Landsat descending node ~10:00-10:30 LT at mid-lat
ha = math.radians(-22.5)
sin_el = (math.sin(lat_rad) * math.sin(decl) +
          math.cos(lat_rad) * math.cos(decl) * math.cos(ha))
approx_el = math.degrees(math.asin(sin_el))
approx_zen = 90 - approx_el

# Sentinel-2 passes ~10:30 similarly (same equatorial crossing)
print(f"\n  Sep 3, 2024 at 45.8°N — approximate illumination geometry:")
if sun_el is not None:
    print(f"  Sun elevation (from STAC):   {sun_el:.2f}°")
    print(f"  Solar zenith (from STAC):    {90-sun_el:.2f}°")
    if sun_az:
        print(f"  Sun azimuth (from STAC):     {sun_az:.2f}°")
else:
    print(f"  Sun elevation (computed):    ~{approx_el:.1f}°  (STAC unavailable)")
    print(f"  Solar zenith (computed):     ~{approx_zen:.1f}°")

el = sun_el if sun_el else approx_el
zen = 90 - el

print(f"\n  BEER-LAMBERT PENETRATION FACTOR:")
print(f"    cos(solar_zenith) = cos({zen:.1f}°) = {math.cos(math.radians(zen)):.4f}")
print(f"    Blue band (B02) 1/e depth in clear water ≈ 25m at nadir")
print(f"    Effective 1/e depth at this geometry ≈ {25*math.cos(math.radians(zen)):.1f}m")
print(f"    → Stumpf ratio depth estimates need ×{1/math.cos(math.radians(zen)):.3f} correction")

print(f"\n  SHADOW GEOMETRY (for structure height from shadow length):")
print(f"    Solar elevation = {el:.1f}°")
print(f"    tan(elevation)  = {math.tan(math.radians(el)):.4f}")
print(f"    Bridge tower (550ft = 167m) shadow length ≈ {167/math.tan(math.radians(el)):.0f}m")
print(f"    = {167/math.tan(math.radians(el))/30:.1f} Landsat pixels / "
      f"{167/math.tan(math.radians(el))/10:.1f} S2 pixels")

print(f"\n  KEY INSIGHT:")
print(f"    Without solar_zenith correction, Stumpf depths are ~{(1/math.cos(math.radians(zen))-1)*100:.0f}% too shallow.")
print(f"    Multi-pass stereo: Landsat ±7.5° off-nadir, Sentinel-2 ±10.3° → baseline for parallax depth.")
print(f"    ICESat-2 ATL13 (lidar) can provide absolute water depth ground truth if passes exist.")
