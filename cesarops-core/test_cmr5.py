import requests

# Correct collection short_name, minimal bbox, recent date
url = 'https://cmr.earthdata.nasa.gov/search/granules.json'

# Test with exact short_name from collection search
params = {
    'short_name': 'SENTINEL-1A_SLC',
    'temporal': '2025-01-01T00:00:00Z/2025-01-03T00:00:00Z',
    'page_size': 2,
}
r = requests.get(url, params=params, headers={'Accept': 'application/json'})
d = r.json()
hits = d.get('feed', {}).get('hits', 0)
print(f'SENTINEL-1A_SLC global 2 days: {hits} hits')

# Try HLS
params2 = {
    'short_name': 'HLSS30',
    'temporal': '2025-06-01T00:00:00Z/2025-06-03T00:00:00Z',
    'page_size': 2,
}
r2 = requests.get(url, params=params2, headers={'Accept': 'application/json'})
d2 = r2.json()
hits2 = d2.get('feed', {}).get('hits', 0)
print(f'HLSS30 global 2 days: {hits2} hits')

# Try with no temporal at all (just bbox)
params3 = {
    'short_name': 'HLSS30',
    'bounding_box': '-87.0,45.0,-86.0,46.0',
    'page_size': 2,
}
r3 = requests.get(url, params=params3, headers={'Accept': 'application/json'})
d3 = r3.json()
hits3 = d3.get('feed', {}).get('hits', 0)
print(f'HLSS30 bbox only: {hits3} hits')
if hits3 > 0:
    for e in d3.get('feed', {}).get('entry', [])[:2]:
        title = e.get('title', '?')
        boxes = e.get('boxes', [])
        print(f'  {title}')
        if boxes:
            print(f'    bbox: {boxes[0]}')
