#!/usr/bin/env python3
import requests, json
r = requests.post('https://planetarycomputer.microsoft.com/api/stac/v1/search', json={
    'collections': ['landsat-c2-l2'],
    'bbox': [-85.0, 45.6, -83.9, 46.2],
    'datetime': '2024-09-01/2024-09-05',
    'limit': 5
}, timeout=30)
data = r.json()
print('Total features:', len(data.get('features', [])))
for f in data.get('features', []):
    print('\nID:', f['id'])
    print('Assets:', list(f['assets'].keys()))
    for k in ['lwir11', 'swir16', 'blue']:
        if k in f['assets']:
            print(f'  {k}: {f["assets"][k]["href"]}')
