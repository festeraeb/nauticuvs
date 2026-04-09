#!/usr/bin/env python3
"""Show Line 5 corridor flags from most recent 2024 scan."""
import json, glob
from pathlib import Path

files = sorted(glob.glob('outputs/straits_mackinac_2024_scan_*.json'))
if not files:
    print('No 2024 scan files found')
    exit()
data = json.loads(Path(files[-1]).read_text(encoding='utf-8'))
line5 = [d for d in data['detections'] if d.get('line5_candidate')]
print(f'File: {files[-1]}')
print(f'Total detections: {len(data["detections"])}')
print(f'Line 5 corridor flags: {len(line5)}')
print()
for d in sorted(line5, key=lambda x: abs(x['zscore']), reverse=True)[:20]:
    det_type = d.get('type', 'unknown')
    z = d.get('zscore', 0)
    lat = d.get('lat', 0)
    lon = d.get('lon', 0)
    src = d.get('source', '')[:50]
    print(f'  {det_type:15s}  z={z:+.2f}  {lat:.5f}, {lon:.5f}  {src}')
