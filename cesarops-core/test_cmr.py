import requests
from requests.auth import HTTPBasicAuth
import json
import os
from pathlib import Path

# Load credentials from .env — never hardcode in source
def _load_env():
    env_path = Path(__file__).resolve().parent / '.env'
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip()
    return env

_env = _load_env()
_user = os.environ.get('EARTHDATA_USERNAME', _env.get('EARTHDATA_USERNAME', ''))
_pass = os.environ.get('EARTHDATA_PASSWORD', _env.get('EARTHDATA_PASSWORD', ''))

url = 'https://cmr.earthdata.nasa.gov/search/granules.json'
params = {
    'short_name': 'SENTINEL-1A_SLC',
    'bounding_box': '-92.0,44.5,-80.0,47.0',
    'temporal': '2025-01-01T00:00:00Z/2025-12-31T23:59:59Z',
    'page_size': 5,
}
auth = HTTPBasicAuth(_user, _pass) if _user and _pass else None
resp = requests.get(url, params=params, headers={'Accept': 'application/json'}, auth=auth)
print('Status:', resp.status_code)
data = resp.json()
print('Total hits:', data.get('feed', {}).get('hits', 0))
entries = data.get('feed', {}).get('entry', [])
print(f'Entries returned: {len(entries)}')
for e in entries[:3]:
    title = e.get('title', '?')
    print(f'  - {title}')
