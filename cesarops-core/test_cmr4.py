import requests

# Search for actual Sentinel-1 collection names
url = 'https://cmr.earthdata.nasa.gov/search/collections.json'
params = {'keyword': 'Sentinel-1 SLC', 'page_size': 10}
r = requests.get(url, params=params, headers={'Accept': 'application/json'})
d = r.json()
hits = d.get('feed', {}).get('hits', 0)
print(f'Collections matching "Sentinel-1 SLC": {hits}')
entries = d.get('feed', {}).get('entry', [])
for e in entries[:5]:
    print(f'  short_name: {e.get("short_name", "?")}')
    print(f'    title: {e.get("title", "?")}')
    print()

# Also try HLS collection
params2 = {'keyword': 'HLS Landsat Sentinel-2', 'page_size': 5}
r2 = requests.get(url, params=params2, headers={'Accept': 'application/json'})
d2 = r2.json()
print(f'\nCollections matching "HLS": {d2.get("feed", {}).get("hits", 0)}')
for e in d2.get('feed', {}).get('entry', [])[:3]:
    print(f'  short_name: {e.get("short_name", "?")}')
    print(f'    title: {e.get("title", "?")}')
