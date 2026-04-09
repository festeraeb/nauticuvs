import requests

# CMR basic health check - try the collection endpoint first
url = 'https://cmr.earthdata.nasa.gov/search/collections.json'
params = {'short_name': 'SENTINEL-1A_SLC'}
r = requests.get(url, params=params, headers={'Accept': 'application/json'})
print('Collections:', r.status_code, r.json().get('feed', {}).get('hits', 0))

# Try granules with known parameters (global, recent)
url2 = 'https://cmr.earthdata.nasa.gov/search/granules.json'
params2 = {
    'short_name': 'SENTINEL-1A_SLC',
    'temporal': '2025-01-01T00:00:00Z/2025-01-02T00:00:00Z',
    'page_size': 2,
}
r2 = requests.get(url2, params=params2, headers={'Accept': 'application/json'})
d2 = r2.json()
hits = d2.get('feed', {}).get('hits', 0)
print(f'Granules (global, 1 day): {hits} hits')
if hits > 0:
    e = d2.get('feed', {}).get('entry', [{}])[0]
    boxes = e.get('boxes', [])
    if boxes:
        print(f'  First granule bbox: {boxes[0]}')
        print(f'  Title: {e.get("title", "?")}')
