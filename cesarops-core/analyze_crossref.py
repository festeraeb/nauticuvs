#!/usr/bin/env python3
"""Analyze crossref results against known wrecks and Stumpf depth data."""
import json, math, sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2-lat1); dlon = math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

with open('known_wrecks.json') as f:
    raw = json.load(f)
_w = raw if isinstance(raw, list) else raw.get('wrecks', raw)
WRECKS = list(_w.values()) if isinstance(_w, dict) else _w

HIGH = [
    (46.0187, -84.3330, 'optical_blue+green+red+stumpf_shallow'),
    (45.9751, -84.3327, 'optical_green+optical_red'),
    (46.0237, -84.5843, 'optical_blue+green+red'),
    (45.9393, -84.3813, 'optical_blue+optical_red'),
    (45.9394, -84.3705, 'optical_blue+optical_green'),
]
MED = [
    (45.7005, -84.7696, 'optical+thermal  [MULTI-BAND]'),
    (45.7009, -84.5558, 'optical+thermal  [MULTI-BAND]'),
    (45.7000, -84.4440, 'optical+thermal'),
    (45.8439, -84.7024, 'optical_blue+green+red'),
    (46.0084, -84.7132, 'optical_red'),
    (45.8503, -84.7131, 'optical_red'),
    (45.9422, -84.6203, 'optical_blue'),
    (45.9392, -84.3628, 'optical_blue'),
    (45.9433, -84.5651, 'optical_blue'),
    (45.9447, -84.6009, 'optical_blue'),
]

def wreck_coords(w):
    """Return (lat, lon) — exact if present, else bbox centre."""
    if 'lat' in w and 'lon' in w:
        return w['lat'], w['lon']
    return (w['lat_min'] + w['lat_max']) / 2, (w['lon_min'] + w['lon_max']) / 2

def nearest_wrecks(lat, lon, n=3):
    ranked = sorted(WRECKS, key=lambda w: haversine_km(lat, lon, *wreck_coords(w)))[:n]
    return [(w, haversine_km(lat, lon, *wreck_coords(w))) for w in ranked]

print('=' * 80)
print('HIGH CONFIDENCE ANOMALIES — 2015 + 2024 persistent features')
print('=' * 80)
for i, (lat, lon, sensors) in enumerate(HIGH, 1):
    print(f'\n  H{i}.  ({lat:.4f}, {lon:.4f})  [{sensors}]')
    for w, d in nearest_wrecks(lat, lon):
        flag = '  <-- WITHIN 1.5km' if d < 1.5 else ('  <-- within 3km' if d < 3.0 else '')
        depth_ft = w.get('depth_ft', '?')
        print(f'       {w["name"]:<48s} {d:5.2f} km  {depth_ft}ft{flag}')

print()
print('=' * 80)
print('MEDIUM CONFIDENCE ANOMALIES — notable subsets')
print('=' * 80)
for i, (lat, lon, sensors) in enumerate(MED, 1):
    print(f'\n  M{i}.  ({lat:.4f}, {lon:.4f})  [{sensors}]')
    for w, d in nearest_wrecks(lat, lon, n=2):
        flag = '  <-- WITHIN 1.5km' if d < 1.5 else ('  <-- within 3km' if d < 3.0 else '')
        depth_ft = w.get('depth_ft', '?')
        print(f'       {w["name"]:<48s} {d:5.2f} km  {depth_ft}ft{flag}')

# --- Stumpf depth analysis from v3 scan ---
print()
print('=' * 80)
print('STUMPF DEPTH ESTIMATES — Persistent HIGH confidence sites')
print('(depth_m_corrected from v3 scan, nearest stumpf_shallow detections)')
print('=' * 80)

v3_file = sorted(Path('outputs').glob('straits_mackinac_2024_v3_scan_*.json'))[-1]
with open(v3_file) as f:
    v3 = json.load(f)

stumpf_dets = [d for d in v3.get('detections', []) if d.get('type') == 'stumpf_shallow']
print(f'\n  {len(stumpf_dets)} stumpf_shallow detections in v3 scan')

for i, (lat, lon, sensors) in enumerate(HIGH, 1):
    if 'stumpf' not in sensors:
        continue
    nearby = [d for d in stumpf_dets
              if haversine_km(lat, lon, d['lat'], d['lon']) < 0.5]
    if nearby:
        depths = [d.get('depth_m_corrected', d.get('depth_m_raw', None)) for d in nearby if d.get('depth_m_corrected')]
        depths = [d for d in depths if d is not None]
        if depths:
            print(f'\n  H{i}. ({lat:.4f}, {lon:.4f}) — {len(nearby)} nearby Stumpf hits')
            print(f'       depth_m_corrected range: {min(depths):.1f} – {max(depths):.1f} m')
            print(f'       median: {sorted(depths)[len(depths)//2]:.1f} m')
            print(f'       correction factor applied: {nearby[0].get("solar_zenith_correction", "?")}x')
    else:
        # Check wider radius
        nearby_wide = [d for d in stumpf_dets
                       if haversine_km(lat, lon, d['lat'], d['lon']) < 2.0]
        if nearby_wide:
            depths = [d.get('depth_m_corrected') for d in nearby_wide if d.get('depth_m_corrected')]
            print(f'\n  H{i}. ({lat:.4f}, {lon:.4f}) — {len(nearby_wide)} Stumpf hits within 2km')
            if depths:
                print(f'       depth_m_corrected range: {min(depths):.1f} – {max(depths):.1f} m (zenith-corrected)')

# --- Z-score comparison ---
print()
print('=' * 80)
print('Z-SCORE ANALYSIS: are high-conf sites detectably different from cluster?')
print('=' * 80)
all_dets = v3.get('detections', [])
STRAITS = (45.70, -84.80, 46.05, -84.10)
in_straits = [d for d in all_dets
              if STRAITS[0] <= d['lat'] <= STRAITS[2] and STRAITS[1] <= d['lon'] <= STRAITS[3]]
zscores = [abs(d['zscore']) for d in in_straits if abs(d.get('zscore', 0)) <= 8]
if zscores:
    zscores_s = sorted(zscores, reverse=True)
    print(f'  In-Straits detections: {len(zscores)}')
    print(f'  |z| median : {zscores_s[len(zscores_s)//2]:.2f}')
    print(f'  |z| p90    : {zscores_s[int(len(zscores_s)*0.10)]:.2f}')
    print(f'  |z| p99    : {zscores_s[int(len(zscores_s)*0.01)]:.2f}')
    print(f'  |z| max    : {zscores_s[0]:.2f}')
    hi_conf_z = [abs(d.get('zscore',0)) for site_lat, site_lon, _ in HIGH
                 for d in all_dets
                 if haversine_km(site_lat, site_lon, d['lat'], d['lon']) < 0.3]
    if hi_conf_z:
        print(f'\n  HIGH-conf site |z| mean: {sum(hi_conf_z)/len(hi_conf_z):.2f}  '
              f'(vs scene median {zscores_s[len(zscores_s)//2]:.2f})')
