#!/usr/bin/env python3
"""Quick proximity table: known wrecks vs Mackinac Bridge + scan coverage check."""
import sys, math, json
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def dms(d, m): return d + m / 60.0

def hkm(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

BRIDGE = (45.80140, -84.72740)  # midpoint

# Load all wrecks from known_wrecks.json
raw = json.load(open('known_wrecks.json'))
wrecks_dict = raw['wrecks']

rows = []
for key, w in wrecks_dict.items():
    if 'lat' in w and 'lon' in w:
        lat, lon = w['lat'], w['lon']
    else:
        lat = (w['lat_min'] + w['lat_max']) / 2
        lon = (w['lon_min'] + w['lon_max']) / 2
    d_km = hkm(*BRIDGE, lat, lon)
    d_mi = d_km * 0.621371
    rows.append((d_km, w.get('name', key), lat, lon, w.get('depth_ft', '?'), d_mi))

rows.sort()

print("=" * 75)
print("KNOWN WRECKS SORTED BY DISTANCE FROM MACKINAC BRIDGE MIDPOINT")
print(f"Bridge: {BRIDGE[0]}N, {BRIDGE[1]}W")
print("=" * 75)
print(f"  {'Wreck':<26} {'lat':>9} {'lon':>10} {'depth':>6}  {'km':>6}  {'miles':>6}")
print("  " + "-"*65)
for d_km, name, lat, lon, depth, d_mi in rows:
    flag = "  <-- WITHIN 2mi" if d_mi < 2.0 else ("  <- within 5mi" if d_mi < 5.0 else "")
    print(f"  {name:<26} {lat:>9.5f} {lon:>10.5f} {str(depth):>6}ft  {d_km:>6.2f}  {d_mi:>6.2f}{flag}")

print()

# Check scan bbox
print("=" * 75)
print("SCAN BBOX CHECK (from lake_michigan_scan.py)")
print("=" * 75)
try:
    src = open('lake_michigan_scan.py').read()
    import re
    bbox_hits = re.findall(r'(?:BBOX|bbox|straits[_\w]*bbox)[^\n]*=.*?[\[\(]([\d., \-]+)[\]\)]', src, re.IGNORECASE)
    if not bbox_hits:
        # look for lat_min/lat_max/lon_min/lon_max near straits
        lats = re.findall(r'lat_min\s*=\s*([\d.]+)', src)
        lons = re.findall(r'lon_min\s*=\s*([\-\d.]+)', src)
        print(f"  lat_min hits: {lats[:4]}")
        print(f"  lon_min hits: {lons[:4]}")
    else:
        for h in bbox_hits[:4]:
            print(f"  bbox: {h}")
    # find SCAN_BBOX or similar
    for line in src.splitlines():
        if any(k in line for k in ['SCAN_BBOX', 'STRAITS_BBOX', 'lat_min', 'lat_max', 'lon_min', 'lon_max', 'bbox']):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and len(stripped) < 100:
                print(f"  {stripped}")
except Exception as e:
    print(f"  Could not parse scan file: {e}")
