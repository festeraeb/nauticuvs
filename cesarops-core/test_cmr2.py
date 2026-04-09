import requests
import json

# Try with different short_name format
url = 'https://cmr.earthdata.nasa.gov/search/granules.json'

# Test 1: Single short_name
params1 = {
    'short_name': 'SENTINEL-1A_SLC',
    'bounding_box': '-87.0,45.0,-86.0,46.0',  # Smaller box, Lake Michigan
    'temporal': '2025-06-01T00:00:00Z/2025-06-30T23:59:59Z',
    'page_size': 3,
}
r1 = requests.get(url, params=params1, headers={'Accept': 'application/json'})
d1 = r1.json()
hits1 = d1.get('feed', {}).get('hits', 0)
print(f'Test 1 - SENTINEL-1A_SLC (small box, June): {hits1} hits')

# Test 2: SENTINEL-1B_SLC
params2 = dict(params1)
params2['short_name'] = 'SENTINEL-1B_SLC'
r2 = requests.get(url, params=params2, headers={'Accept': 'application/json'})
d2 = r2.json()
hits2 = d2.get('feed', {}).get('hits', 0)
print(f'Test 2 - SENTINEL-1B_SLC (small box, June): {hits2} hits')

# Test 3: Different bbox order (lat,lon,lat,lon)
params3 = {
    'short_name': 'SENTINEL-1A_SLC',
    'bounding_box': '45.0,-87.0,46.0,-86.0',
    'temporal': '2025-06-01T00:00:00Z/2025-06-30T23:59:59Z',
    'page_size': 3,
}
r3 = requests.get(url, params=params3, headers={'Accept': 'application/json'})
d3 = r3.json()
hits3 = d3.get('feed', {}).get('hits', 0)
print(f'Test 3 - SENTINEL-1A_SLC (lat,lon order): {hits3} hits')
if hits3 > 0:
    for e in d3.get('feed', {}).get('entry', [])[:2]:
        print(f'  - {e.get("title", "?")}')

# Test 4: Try HLS instead (Sentinel-2)
params4 = {
    'short_name': 'HLSS30.v2.0',
    'bounding_box': '45.0,-87.0,46.0,-86.0',
    'temporal': '2025-06-01T00:00:00Z/2025-06-30T23:59:59Z',
    'page_size': 3,
}
r4 = requests.get(url, params=params4, headers={'Accept': 'application/json'})
d4 = r4.json()
hits4 = d4.get('feed', {}).get('hits', 0)
print(f'Test 4 - HLSS30 (small box, June): {hits4} hits')
if hits4 > 0:
    for e in d4.get('feed', {}).get('entry', [])[:2]:
        print(f'  - {e.get("title", "?")}')
