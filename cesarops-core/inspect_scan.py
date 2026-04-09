#!/usr/bin/env python3
"""Quick inspector for latest scan JSON output."""
import json, glob, sys

pattern = sys.argv[1] if len(sys.argv) > 1 else 'outputs/straits_mackinac_2015_2016_scan_*.json'
files = sorted(glob.glob(pattern))
if not files:
    print("No files found:", pattern)
    sys.exit(1)
f = files[-1]
print(f"Loading: {f}\n")
data = json.load(open(f, encoding='utf-8'))
dets = data['detections']

from collections import Counter
types = Counter(d['type'] for d in dets)
print(f"Total detections: {len(dets)}")
print(f"By type: {dict(types)}")
print()

# Positive z-score optical_blue = bright reflector (shallow submerged object)
pos_blue = sorted([d for d in dets if d['type'] == 'optical_blue' and d['zscore'] > 0],
                  key=lambda x: x['zscore'], reverse=True)
total_blue = len([d for d in dets if d['type'] == 'optical_blue'])
print(f"BRIGHT optical_blue (z>0, positive = more scatter upward — shallow submerged):")
print(f"  Total positive: {len(pos_blue)} / {total_blue}")
for d in pos_blue[:20]:
    l5 = ' LINE5' if d.get('line5_candidate') else ''
    kw = f' KW:{d["known_wreck_name"]}' if d.get('known_wreck_name') else ''
    print(f"  lat={d['lat']:9.4f} lon={d['lon']:9.4f} z={d['zscore']:7.2f}{l5}{kw}")
print()

# Cold-sink thermal (-2 to -8 range is most interesting, not the -14 floor)
therm_cold = sorted([d for d in dets if d['type'] == 'thermal' and -10 < d['zscore'] < -1.5],
                    key=lambda x: x['zscore'])
print(f"THERMAL cold-sink (-10 < z < -1.5, submerged steel thermal mass):")
print(f"  Count: {len(therm_cold)}")
for d in therm_cold[:20]:
    l5 = ' LINE5' if d.get('line5_candidate') else ''
    kw = f' KW:{d["known_wreck_name"]}' if d.get('known_wreck_name') else ''
    print(f"  lat={d['lat']:9.4f} lon={d['lon']:9.4f} z={d['zscore']:7.2f}{l5}{kw}")
print()

# Positive thermal (warm spot — unusual in cold lake — could be Line 5 geologic heat)
therm_warm = sorted([d for d in dets if d['type'] == 'thermal' and d['zscore'] > 0.5],
                    key=lambda x: x['zscore'], reverse=True)
print(f"THERMAL warm anomaly (z>0.5 — unusual warm spot, potential pipeline/vent):")
for d in therm_warm[:10]:
    l5 = ' LINE5' if d.get('line5_candidate') else ''
    print(f"  lat={d['lat']:9.4f} lon={d['lon']:9.4f} z={d['zscore']:7.2f}{l5}")
