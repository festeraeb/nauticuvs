#!/usr/bin/env python3
"""
CESAROPS Universal Satellite Data Downloader

Pulls satellite imagery from all configured sources:
  - ASF HyP3 (Sentinel-1 SAR RTC/InSAR — free, Earthdata auth)
  - Copernicus Data Space (Sentinel-1 SLC, Sentinel-2 MSI — free)
  - NASA PO.DAAC (SWOT SSH, ICESat-2 ATL13 — Earthdata auth)
  - USGS Earth Explorer (Landsat 8/9 — API key)
  - NASA HLS (Harmonized Landsat Sentinel-2 — Earthdata auth)

Usage:
    python universal_downloader.py --area "straits_of_mackinac" --dates 2024-06-01 2024-09-30 --sensors sar,optical
    python universal_downloader.py --bbox 45.8,-84.8,46.1,-84.4 --dates 2013-07-01 2026-04-01 --sensors all
    python universal_downloader.py --list-sources
    python universal_downloader.py --dry-run --area "straits_of_mackinac" --dates 2025-01-01 2025-12-31
"""

import argparse
import json
import os
import sys
import time
import hashlib
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlencode, quote

import requests
from requests.auth import HTTPBasicAuth

# ── Windows console encoding ────────────────────────────────────────────────
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# ── Config loading ──────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent

def load_env(path: Path) -> Dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            env[key.strip()] = val.strip()
    return env

_dotenv = load_env(REPO / ".env")

def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, _dotenv.get(key, default))

# ── Area presets ────────────────────────────────────────────────────────────

AREAS = {
    'straits_of_mackinac': {
        'bbox': [45.80, -84.80, 46.10, -84.40],
        'label': 'Straits of Mackinac',
    },
    'fox_islands': {
        'bbox': [45.80, -84.60, 46.00, -84.40],
        'label': 'Fox Islands',
    },
    'beaver_islands': {
        'bbox': [45.60, -85.60, 45.80, -85.40],
        'label': 'Beaver Islands',
    },
    'lake_michigan_south': {
        'bbox': [42.30, -88.50, 43.20, -87.40],
        'label': 'Lake Michigan South',
    },
    'lake_michigan_north': {
        'bbox': [43.20, -87.50, 45.00, -86.00],
        'label': 'Lake Michigan North',
    },
    'lake_huron_north': {
        'bbox': [44.50, -83.50, 46.00, -81.50],
        'label': 'Lake Huron North',
    },
    'lake_superior': {
        'bbox': [46.50, -91.00, 48.00, -84.50],
        'label': 'Lake Superior',
    },
    'lake_erie': {
        'bbox': [41.30, -83.50, 42.50, -78.80],
        'label': 'Lake Erie',
    },
    'lake_ontario': {
        'bbox': [43.20, -77.50, 44.20, -76.00],
        'label': 'Lake Ontario',
    },
}


# ── Helper: Earthdata auth session ─────────────────────────────────────────

def earthdata_session() -> requests.Session:
    """Create a requests session authenticated with NASA Earthdata."""
    user = cfg('EARTHDATA_USERNAME')
    passwd = cfg('EARTHDATA_PASSWORD')
    s = requests.Session()
    if user and passwd:
        s.auth = HTTPBasicAuth(user, passwd)
    token = cfg('EARTHDATA_TOKEN')
    if token:
        s.headers.update({'Authorization': f'Bearer {token}'})
    s.headers.update({'User-Agent': 'CESAROPS-WreckHunter2000/1.0'})
    return s


# ── Source 1: ASF HyP3 (Sentinel-1 SAR) ────────────────────────────────────

class ASFDownloader:
    """Download Sentinel-1 SAR via ASF API.

    Two modes:
      - Direct SLC download (raw granules for local processing)
      - Submit HyP3 job for on-demand RTC processing (cloud, free)
    """

    ASF_SEARCH = 'https://api.daac.asf.alaska.edu/services/search/param'
    HYP3_API = 'https://hyp3-api.asf.alaska.edu'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'sar')
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CESAROPS-WreckHunter2000/1.0'})

        # Set Earthdata JWT token for HyP3 authentication
        ed_token = cfg('EARTHDATA_TOKEN', '')
        if ed_token:
            self.session.headers.update({'Authorization': f'Bearer {ed_token}'})

    def search_granules(self, bbox: List[float], start: str, end: str,
                        platform: str = 'Sentinel-1A,Sentinel-1B,Sentinel-1C',
                        max_results: int = 50) -> List[Dict]:
        """Search for Sentinel-1 SLC granules in area/time via ASF API."""
        center_lon = (bbox[1] + bbox[3]) / 2
        center_lat = (bbox[0] + bbox[2]) / 2
        params = {
            'platform': platform,
            'processingLevel': 'SLC',
            'intersectsWith': f'POINT({center_lon} {center_lat})',
            'start': f'{start}T00:00:00UTC',
            'end': f'{end}T23:59:59UTC',
            'maxResults': max_results,
            'output': 'jsonlite',  # JSON instead of Metalink XML
        }
        resp = self.session.get(self.ASF_SEARCH, params=params)
        resp.raise_for_status()

        # Parse JSONLite — list of granule dicts
        data = resp.json()
        if not isinstance(data, list):
            data = data.get('results', [])
        granules = []
        for item in data:
            name = item.get('granuleName', item.get('name', ''))
            # Strip .zip extension — HyP3 wants bare granule names
            base_name = name.replace('.zip', '') if name.endswith('.zip') else name
            granules.append({
                'id': base_name,
                'title': base_name,
                'href': item.get('url', ''),
                'start_time': item.get('startTime', ''),
                'polarization': item.get('polarization', 'VV+VH'),
            })
            if len(granules) >= max_results:
                break
        return granules

    def submit_rtc_job(self, granule_name: str, dem: str = 'GLO30',
                       scale: str = 'DECIBEL') -> str:
        """Submit a HyP3 RTC processing job. Returns job_id."""
        # HyP3 API requires a 'jobs' array
        payload = {
            'jobs': [{
                'job_type': 'RTC_GAMMA',
                'job_parameters': {
                    'granules': [granule_name],
                },
            }]
        }
        if self.dry_run:
            print(f"  [DRY RUN] Would submit RTC job for: {granule_name}")
            return 'dry-run-job-id'

        resp = self.session.post(f'{self.HYP3_API}/jobs', json=payload)
        resp.raise_for_status()
        data = resp.json()
        job = data.get('jobs', [{}])[0]
        job_id = job.get('job_id', '?')
        print(f"  HyP3 job submitted: {job_id}")
        print(f"    Granule: {granule_name}")
        print(f"    Status: {job.get('status_code', 'PENDING')}")
        print(f"    Credits: {job.get('credit_cost', '?')}")
        return job_id

    def wait_for_job(self, job_id: str, poll_interval: int = 120,
                     max_wait: int = 36000) -> Optional[str]:
        """Poll HyP3 job until SUCCEEDED/FAILED. Returns download URL or None."""
        if self.dry_run:
            return None

        deadline = time.time() + max_wait
        while time.time() < deadline:
            resp = self.session.get(f'{self.HYP3_API}/jobs/{job_id}')
            resp.raise_for_status()
            job = resp.json()
            # HyP3 returns a dict with 'jobs' key or a single job dict
            if isinstance(job, dict) and 'jobs' in job:
                job = job['jobs'][0]
            status = job.get('status_code', 'UNKNOWN')
            print(f"  Job {job_id}: {status}")

            if status == 'SUCCEEDED':
                # HyP3 returns files list, not links
                for f in job.get('files', []):
                    if isinstance(f, dict) and 'url' in f:
                        return f['url']
                # Fallback: browse_images or thumbnail_images are not what we want
                return None
            elif status in ('FAILED', 'RUNNING_EXPIRED'):
                print(f"  Job failed: {job.get('message', 'unknown error')}")
                return None

            time.sleep(poll_interval)

        print(f"  Job timed out after {max_wait}s")
        return None

    def download_file(self, url: str, dest: Path) -> bool:
        """Download a file with resume support."""
        if self.dry_run:
            print(f"  [DRY RUN] Would download: {url}")
            return True

        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self.session.get(url, stream=True, timeout=600)
        resp.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return True

    def run(self, bbox: List[float], start: str, end: str,
            max_granules: int = 20, process_rtc: bool = True) -> List[Path]:
        """Full pipeline: search → submit RTC → wait → download."""
        print(f"\n{'='*60}")
        print(f"ASF HyP3 — Sentinel-1 SAR")
        print(f"  BBOX: {bbox}")
        print(f"  Range: {start} to {end}")
        print(f"{'='*60}")

        granules = self.search_granules(bbox, start, end, max_results=max_granules)
        if not granules:
            print("  No granules found.")
            return []

        print(f"  Found {len(granules)} granules")

        downloaded = []
        for i, g in enumerate(granules):
            print(f"\n[{i+1}/{len(granules)}] {g['title']}")

            if process_rtc:
                job_id = self.submit_rtc_job(g['title'])
                url = self.wait_for_job(job_id)
                if url:
                    dest = self.output_dir / f"rtc_{g['title']}.tif"
                    if self.download_file(url, dest):
                        downloaded.append(dest)
            else:
                # Download raw SLC
                dest = self.output_dir / f"{g['title']}.zip"
                if self.download_file(g['href'], dest):
                    downloaded.append(dest)

        print(f"\n  Total downloaded: {len(downloaded)} files")
        return downloaded


# ── Source 2: Copernicus Data Space (Sentinel-1 + Sentinel-2) ─────────────

class CopernicusDownloader:
    """Download from Copernicus Data Space Ecosystem via OData API."""

    API_BASE = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
    AUTH_URL = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'copernicus')
        self.session = requests.Session()
        self._token = None
        self._token_expiry = 0

    def _get_token(self) -> Optional[str]:
        user = cfg('COPERNICUS_USERNAME')
        passwd = cfg('COPERNICUS_PASSWORD')
        if not user or not passwd:
            print("  ⚠ COPERNICUS_USERNAME/PASSWORD not set in .env")
            return None

        if self._token and time.time() < self._token_expiry:
            return self._token

        resp = requests.post(self.AUTH_URL, data={
            'client_id': 'cdse-public',
            'username': user,
            'password': passwd,
            'grant_type': 'password',
        })
        if resp.status_code != 200:
            print(f"  ⚠ Copernicus auth failed: {resp.status_code}")
            return None

        data = resp.json()
        self._token = data['access_token']
        self._token_expiry = time.time() + data.get('expires_in', 3500) - 60
        self.session.headers.update({'Authorization': f'Bearer {self._token}'})
        return self._token

    def search(self, bbox: List[float], start: str, end: str,
               product_type: str = 'S2MSI2A',  # Sentinel-2 L2A
               cloud_cover: float = 20.0,
               max_results: int = 50) -> List[Dict]:
        """Search for products in area/time."""
        token = self._get_token()
        if not token:
            return []

        # Sentinel-2 uses $search with OData
        if product_type.startswith('S2'):
            collection = 'SENTINEL-2'
            params = {
                '$filter': (
                    f"Collection/Name eq '{collection}' and "
                    f"OData.CSC.Intersects(area=geography'SRID=4326;POLYGON(("
                    f"{bbox[1]} {bbox[0]}, {bbox[3]} {bbox[0]}, "
                    f"{bbox[3]} {bbox[2]}, {bbox[1]} {bbox[2]}, "
                    f"{bbox[1]} {bbox[0]}))') and "
                    f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq '{product_type}') and "
                    f"ContentDate/Start gt {start}T00:00:00.000Z and "
                    f"ContentDate/Start lt {end}T23:59:59.999Z"
                ),
                '$top': max_results,
            }
        else:
            # Sentinel-1
            collection = 'SENTINEL-1'
            params = {
                '$filter': (
                    f"Collection/Name eq '{collection}' and "
                    f"OData.CSC.Intersects(area=geography'SRID=4326;POLYGON(("
                    f"{bbox[1]} {bbox[0]}, {bbox[3]} {bbox[0]}, "
                    f"{bbox[3]} {bbox[2]}, {bbox[1]} {bbox[2]}, "
                    f"{bbox[1]} {bbox[0]}))') and "
                    f"ContentDate/Start gt {start}T00:00:00.000Z and "
                    f"ContentDate/Start lt {end}T23:59:59.999Z"
                ),
                '$top': max_results,
            }

        resp = self.session.get(self.API_BASE, params=params)
        if resp.status_code != 200:
            print(f"  ⚠ Search failed: {resp.status_code} {resp.text[:200]}")
            return []

        data = resp.json()
        results = []
        for entry in data.get('value', []):
            results.append({
                'id': entry.get('Id', ''),
                'name': entry.get('Name', ''),
                'size': entry.get('ContentLength', 0),
                'date': entry.get('ContentDate', {}).get('Start', ''),
                'download_url': f"{self.API_BASE}({entry.get('Id', '')})/$value",
            })
        return results

    def download(self, product: Dict, dest_dir: Optional[Path] = None) -> Optional[Path]:
        """Download a product to dest_dir."""
        if not self._get_token():
            return None

        dest = (dest_dir or self.output_dir) / f"{product['name']}.zip"
        if self.dry_run:
            print(f"  [DRY RUN] Would download: {product['name']} ({product['size']/1e6:.0f} MB)")
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        url = product['download_url']
        resp = self.session.get(url, stream=True, timeout=3600)
        if resp.status_code != 200:
            print(f"  ⚠ Download failed: {resp.status_code}")
            return None

        with open(dest, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest

    def run(self, bbox: List[float], start: str, end: str,
            product_type: str = 'S2MSI2A', max_results: int = 20) -> List[Path]:
        """Search and download products."""
        print(f"\n{'='*60}")
        print(f"Copernicus Data Space — {product_type}")
        print(f"  BBOX: {bbox}")
        print(f"  Range: {start} to {end}")
        print(f"{'='*60}")

        products = self.search(bbox, start, end, product_type, max_results=max_results)
        if not products:
            print("  No products found.")
            return []

        print(f"  Found {len(products)} products")
        downloaded = []
        for i, p in enumerate(products):
            print(f"\n[{i+1}/{len(products)}] {p['name']}")
            result = self.download(p)
            if result:
                downloaded.append(result)

        print(f"\n  Total downloaded: {len(downloaded)} files")
        return downloaded


# ── Source 3: NASA PO.DAAC (SWOT / ICESat-2) ──────────────────────────────

class PODAACDownloader:
    """Download SWOT SSH and ICESat-2 ATL13 from NASA PO.DAAC."""

    CMR_URL = 'https://cmr.earthdata.nasa.gov/search/granules.json'

    COLLECTIONS = {
        'swot': {
            'short_name': 'SWOT_L2_LR_SSH_Expert',
            'version': '3.1',
        },
        'icesat2': {
            'short_name': 'ATL13',
            'version': '006',
        },
    }

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'podaac')
        self.session = earthdata_session()

    def search(self, bbox: List[float], start: str, end: str,
               dataset: str = 'swot', max_results: int = 30) -> List[Dict]:
        """Search CMR for granules."""
        coll = self.COLLECTIONS.get(dataset)
        if not coll:
            print(f"  Unknown dataset: {dataset}")
            return []

        params = {
            'short_name': coll['short_name'],
            'version': coll['version'],
            'bounding_box': f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}',
            'temporal': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'page_size': max_results,
        }
        resp = self.session.get(self.CMR_URL, params=params)
        if resp.status_code != 200:
            print(f"  ⚠ CMR search failed: {resp.status_code}")
            return []

        data = resp.json()
        granules = []
        for entry in data.get('feed', {}).get('entry', []):
            for link in entry.get('links', []):
                if link.get('href', '').endswith(('.nc', '.h5', '.he5')):
                    granules.append({
                        'id': entry.get('id', ''),
                        'title': entry.get('title', ''),
                        'href': link['href'],
                        'time_start': entry.get('time_start', ''),
                        'dataset': dataset,
                    })
                    break
        return granules

    def run(self, bbox: List[float], start: str, end: str,
            datasets: List[str] = None, max_results: int = 20) -> List[Path]:
        """Search and download SWOT/ICESat-2 granules."""
        if datasets is None:
            datasets = ['swot', 'icesat2']

        all_downloaded = []
        for ds in datasets:
            print(f"\n{'='*60}")
            print(f"NASA PO.DAAC — {ds.upper()}")
            print(f"  BBOX: {bbox}")
            print(f"  Range: {start} to {end}")
            print(f"{'='*60}")

            granules = self.search(bbox, start, end, ds, max_results)
            if not granules:
                print(f"  No {ds} granules found.")
                continue

            print(f"  Found {len(granules)} granules")
            for i, g in enumerate(granules):
                print(f"\n[{i+1}/{len(granules)}] {g['title']}")
                # Preserve extension from URL — SWOT is .nc, ICESat-2 is .h5
                href = g['href']
                ext = href.rsplit('.', 1)[-1] if '.' in href.rsplit('/', 1)[-1] else 'nc'
                dest = self.output_dir / ds / f"{g['title']}.{ext}"
                if self.dry_run:
                    print(f"  [DRY RUN] Would download: {href}")
                    all_downloaded.append(dest)
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                resp = self.session.get(href, stream=True, timeout=600)
                if resp.status_code != 200:
                    print(f"  ⚠ Download failed: {resp.status_code}")
                    continue
                with open(dest, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
                all_downloaded.append(dest)

        print(f"\n  Total downloaded: {len(all_downloaded)} files")
        return all_downloaded


# ── Source 4: USGS Earth Explorer (Landsat 8/9) ──────────────────────────

class USGSDownloader:
    """Download Landsat 8/9 from USGS Earth Explorer via M2M API."""

    API_URL = 'https://m2m.cr.usgs.gov/api/api/json/stable/'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'usgs')
        self.api_key = cfg('USGS_API_KEY')
        self.session = requests.Session()

    def _call(self, endpoint: str, data: dict = None) -> dict:
        url = f'{self.API_URL}{endpoint}'
        payload = data or {}
        payload['apiKey'] = self.api_key
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        result = resp.json()
        if 'error' in result:
            raise RuntimeError(f"USGS API error: {result['error']}")
        return result.get('data', {})

    def search(self, bbox: List[float], start: str, end: str,
               dataset: str = 'LANDSAT_LC09_C02_T1_L2',
               max_results: int = 50) -> List[Dict]:
        """Search for Landsat scenes."""
        if not self.api_key:
            print("  ⚠ USGS_API_KEY not set in .env")
            return []

        result = self._call('scene-search', {
            'datasetName': dataset,
            'sceneFilter': {
                'acquisitionFilter': {
                    'start': start,
                    'end': end,
                },
                'spatialFilter': {
                    'filterType': 'mbr',
                    'lowerLeft': {'longitude': bbox[1], 'latitude': bbox[0]},
                    'upperRight': {'longitude': bbox[3], 'latitude': bbox[2]},
                },
            },
            'maxResults': max_results,
        })

        scenes = []
        for scene in result.get('results', []):
            scenes.append({
                'id': scene.get('entityId', ''),
                'display_id': scene.get('displayId', ''),
                'browse_path': scene.get('browse', [{}])[0].get('thumbnailPath', ''),
                'acquisition_date': scene.get('acquisitionDate', ''),
                'cloud_cover': scene.get('cloudCover', 0),
                'metadata': scene.get('metadata', []),
            })
        return scenes

    def get_download_options(self, entity_id: str,
                             dataset: str = 'LANDSAT_LC09_C02_T1_L2') -> List[Dict]:
        """Get download options for a scene."""
        result = self._call('download-options', {
            'datasetName': dataset,
            'entityIds': [entity_id],
        })
        return result.get('data', [])

    def request_download(self, entity_id: str, dataset: str = 'LANDSAT_LC09_C02_T1_L2',
                         product_id: str = '5e83d0b847c0ea9c') -> str:
        """Request download URL (product_id 5e83d0b847c0ea9c = Level-2 GeoTIFF)."""
        result = self._call('download-request', {
            'downloads': [{
                'entityId': entity_id,
                'datasetName': dataset,
                'productId': product_id,
            }],
        })
        # M2M returns preparingDownloads and availableDownloads with a direct 'url' key
        for item in result.get('availableDownloads', []):
            url = item.get('url', '')
            if url:
                return url
        for item in result.get('preparingDownloads', []):
            url = item.get('url', '')
            if url:
                return url
        return ''

    def run(self, bbox: List[float], start: str, end: str,
            max_results: int = 20) -> List[Path]:
        """Search and download Landsat scenes."""
        print(f"\n{'='*60}")
        print(f"USGS Earth Explorer — Landsat 8/9")
        print(f"  BBOX: {bbox}")
        print(f"  Range: {start} to {end}")
        print(f"{'='*60}")

        scenes = self.search(bbox, start, end, max_results=max_results)
        if not scenes:
            print("  No scenes found.")
            return []

        print(f"  Found {len(scenes)} scenes")
        downloaded = []
        for i, s in enumerate(scenes):
            print(f"\n[{i+1}/{len(scenes)}] {s['display_id']} (cloud: {s['cloud_cover']}%)")
            if self.dry_run:
                downloaded.append(self.output_dir / f"{s['display_id']}.tar.gz")
                continue

            url = self.request_download(s['id'])
            if url:
                dest = self.output_dir / f"{s['display_id']}.tar.gz"
                dest.parent.mkdir(parents=True, exist_ok=True)
                resp = self.session.get(url, stream=True, timeout=600)
                resp.raise_for_status()
                with open(dest, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
                downloaded.append(dest)

        print(f"\n  Total downloaded: {len(downloaded)} files")
        return downloaded


# ── Source 5: NASA HLS (Harmonized Landsat Sentinel-2) ────────────────────

class HLSDownloader:
    """Download HLS (Harmonized Landsat Sentinel-2) from NASA LP DAAC.

    Fallback chain:
      1. NASA CMR — HLSS30 / HLSL30 .tif bands (Earthdata auth)
      2. AWS Element84 STAC — Sentinel-2 L2A COG (free, no auth)
      3. AWS Element84 STAC — Landsat C2 L2 COG (free, no auth)
    """

    CMR_URL = 'https://cmr.earthdata.nasa.gov/search/granules.json'
    STAC_URL = 'https://earth-search.aws.element84.com/v1/search'

    # Angle/QA bands to skip — download only spectral + cloud mask
    _SKIP_BANDS = {'SAA', 'SZA', 'VAA', 'VZA'}
    # Spectral bands to prefer for HLS (Sentinel-2 naming + Landsat overlap)
    _PREFER_BANDS = {'B02', 'B03', 'B04', 'B05', 'B06', 'B07', 'B8A', 'B09', 'B11', 'B12', 'Fmask'}

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'hls')
        self.session = earthdata_session()

    def search(self, bbox: List[float], start: str, end: str,
               product: str = 'HLSS30',
               max_results: int = 50) -> List[Dict]:
        """Search CMR for HLS granules, returning all spectral band links."""
        params = {
            'short_name': product,
            'bounding_box': f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}',
            'temporal': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'page_size': max_results,
        }
        resp = self.session.get(self.CMR_URL, params=params)
        if resp.status_code != 200:
            print(f"  ⚠ CMR search failed: {resp.status_code}")
            return []

        granules = []
        for entry in resp.json().get('feed', {}).get('entry', []):
            title = entry.get('title', '')
            time_start = entry.get('time_start', '')
            for link in entry.get('links', []):
                href = link.get('href', '')
                if not href.endswith('.tif'):
                    continue
                # Extract band name from filename: title.BAND.tif
                fname = href.rsplit('/', 1)[-1]
                band = fname.rsplit('.', 2)[-2] if fname.count('.') >= 2 else ''
                if band in self._SKIP_BANDS:
                    continue
                granules.append({
                    'id': entry.get('id', ''),
                    'title': title,
                    'band': band,
                    'href': href,
                    'time_start': time_start,
                    'source': 'cmr',
                })
        return granules

    def _stac_fallback(self, bbox: List[float], start: str, end: str,
                       max_results: int = 10) -> List[Dict]:
        """AWS Element84 STAC fallback — free, no auth required."""
        granules = []
        # Sentinel-2 L2A COGs
        # Key bands for wreck/HC detection:
        #   blue   = B02 (water-penetrating optical, 458-523 nm)
        #   green  = B03 (Stumpf bathymetry partner)
        #   red    = B04 (surface reference + HC cross-check)
        #   swir16 = B11 (1565 nm, HC/oil absorbs SWIR → dark anomaly)
        # Omitted to save bandwidth: nir/nir08 (surface-only), swir22, scl, qa_pixel
        for coll, bands in [
            ('sentinel-2-l2a', ['blue', 'green', 'red', 'swir16']),
            ('landsat-c2-l2',  ['blue', 'green', 'red', 'swir16']),
        ]:
            try:
                payload = {
                    'collections': [coll],
                    'bbox': [bbox[1], bbox[0], bbox[3], bbox[2]],
                    'datetime': f'{start}T00:00:00Z/{end}T23:59:59Z',
                    'limit': max_results,
                    'query': {'eo:cloud_cover': {'lt': 50}},
                    'sortby': [{'field': 'properties.eo:cloud_cover', 'direction': 'asc'}],
                }
                resp = requests.post(self.STAC_URL, json=payload, timeout=20)
                if resp.status_code != 200:
                    continue
                for feat in resp.json().get('features', []):
                    feat_id = feat['id']
                    assets = feat.get('assets', {})
                    for band_name in bands:
                        asset = assets.get(band_name)
                        if not asset:
                            continue
                        href = asset.get('href', '')
                        # Convert s3:// to HTTPS for direct download
                        if href.startswith('s3://usgs-landsat/'):
                            href = href.replace('s3://usgs-landsat/', 'https://usgs-landsat.s3.us-west-2.amazonaws.com/', 1)
                        elif href.startswith('s3://sentinel-cogs/'):
                            href = href.replace('s3://sentinel-cogs/', 'https://sentinel-cogs.s3.us-west-2.amazonaws.com/', 1)
                        granules.append({
                            'id': feat_id,
                            'title': feat_id,
                            'band': band_name,
                            'href': href,
                            'time_start': feat['properties'].get('datetime', ''),
                            'source': coll,
                        })
            except Exception as e:
                print(f"  ⚠ STAC fallback ({coll}): {e}")
        return granules

    def run(self, bbox: List[float], start: str, end: str,
            max_results: int = 20, products: List[str] = None) -> List[Path]:
        """Search and download HLS band GeoTIFFs with AWS STAC fallback."""
        if products is None:
            products = ['HLSS30', 'HLSL30']

        print(f"\n{'='*60}")
        print(f"NASA HLS — Harmonized Landsat Sentinel-2")
        print(f"  BBOX: {bbox}  |  {start} → {end}")
        print(f"{'='*60}")

        granules: List[Dict] = []
        for prod in products:
            print(f"  Searching CMR for {prod}...")
            hits = self.search(bbox, start, end, product=prod, max_results=max_results)
            print(f"  CMR {prod}: {len(hits)} band files found")
            granules.extend(hits)

        if not granules:
            print("  ⚠ No HLS granules via CMR — trying AWS STAC fallback (Sentinel-2/Landsat COG)...")
            granules = self._stac_fallback(bbox, start, end, max_results=max_results // 2)
            if granules:
                print(f"  AWS STAC: {len(granules)} band files found")
            else:
                print("  No data found from any source.")
                return []

        downloaded = []
        for i, g in enumerate(granules):
            band = g.get('band', 'band')
            fname = f"{g['title']}.{band}.tif"
            dest = self.output_dir / fname
            src_tag = g.get('source', 'cmr')

            if self.dry_run:
                print(f"  [DRY RUN] [{src_tag}] {fname}")
                downloaded.append(dest)
                continue
            if dest.exists() and dest.stat().st_size > 0:
                print(f"  [skip] {dest.name}")
                downloaded.append(dest)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            # Public S3 COGs (AWS STAC fallback) must NOT carry Earthdata Bearer auth —
            # S3 returns HTTP 400 if given an unknown Authorization header.
            if 'amazonaws.com' in g['href']:
                getter = requests.get
            else:
                getter = self.session.get
            resp = getter(g['href'], stream=True, timeout=600)
            if resp.status_code != 200:
                print(f"  ⚠ [{g['href'][:80]}]: HTTP {resp.status_code}")
                continue
            with open(dest, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            print(f"  [{i+1}/{len(granules)}] {dest.name} ({dest.stat().st_size/1e6:.1f} MB) [{src_tag}]")
            downloaded.append(dest)

        print(f"\n  Total downloaded: {len(downloaded)} files")
        return downloaded


# ── Source 6: Sentinel Hub (Process API) ─────────────────────────────────

class SentinelHubDownloader:
    """Download processed imagery from Sentinel Hub via Process API."""

    PROCESS_URL = 'https://services.sentinel-hub.com/api/v1/process'
    STAT_URL = 'https://services.sentinel-hub.com/api/v1/statistics'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'sentinel_hub')
        self.client_id = cfg('SENTINEL_HUB_CLIENT_ID')
        self.client_secret = cfg('SENTINEL_HUB_CLIENT_SECRET')
        self.session = requests.Session()
        self._token = None
        self._token_expiry = 0

    def _get_token(self) -> Optional[str]:
        if not self.client_id or not self.client_secret:
            print("  ⚠ SENTINEL_HUB_CLIENT_ID/SECRET not set in .env")
            return None
        if self._token and time.time() < self._token_expiry:
            return self._token

        resp = requests.post('https://services.sentinel-hub.com/oauth/token', data={
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        })
        if resp.status_code != 200:
            print(f"  ⚠ Sentinel Hub auth failed: {resp.status_code}")
            return None

        data = resp.json()
        self._token = data['access_token']
        self._token_expiry = time.time() + data.get('expires_in', 3600) - 60
        self.session.headers.update({'Authorization': f'Bearer {self._token}'})
        return self._token

    def download_image(self, bbox: List[float], start: str, end: str,
                       bands: str = 'B04,B08', resolution: float = 10.0,
                       maxcc: float = 0.2, img_format: str = 'image/tiff') -> Optional[Path]:
        """Request processed image from Sentinel Hub."""
        if not self._get_token():
            return None

        evalscript = f"""
        //VERSION=3
        function setup() {{
            return {{
                input: ["B04", "B08", "dataMask"],
                output: {{ bands: 4, sampleType: SampleType.FLOAT32 }}
            }};
        }}
        function evaluatePixel(samples) {{
            let s = samples[0];
            return [s.B04, s.B08, s.B04 / s.B08, s.dataMask];
        }}
        """

        payload = {
            'input': {
                'bounds': {
                    'bbox': [bbox[1], bbox[0], bbox[3], bbox[2]],
                    'properties': {'crs': 'http://www.opengis.net/def/crs/EPSG/0/4326'},
                },
                'data': [{
                    'type': 'sentinel-2-l2a',
                    'dataFilter': {
                        'timeRange': {'from': f'{start}T00:00:00Z', 'to': f'{end}T23:59:59Z'},
                        'maxCloudCoverage': maxcc,
                    },
                }],
            },
            'output': {
                'width': int((bbox[3] - bbox[1]) * 111320 / resolution),
                'height': int((bbox[2] - bbox[0]) * 111320 / resolution),
                'responses': [{'identifier': 'default', 'format': {'type': img_format}}],
            },
            'evalscript': evalscript,
        }

        if self.dry_run:
            dest = self.output_dir / f"sh_{start}_{end}.tif"
            print(f"  [DRY RUN] Would request {payload['output']['width']}x{payload['output']['height']} image")
            return dest

        resp = self.session.post(self.PROCESS_URL, json=payload, timeout=300)
        if resp.status_code != 200:
            print(f"  ⚠ Process failed: {resp.status_code} {resp.text[:200]}")
            return None

        dest = self.output_dir / f"sh_{start}_{end}.tif"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(resp.content)
        print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest


# ── Source 7: NOAA CoastWatch ERDDAP (Great Lakes SST, free, no auth) ────────

class NOAACoastwatchDownloader:
    """Download Great Lakes Surface Environmental Analysis (GLSEA) SST from NOAA CoastWatch.

    Free — no authentication. ~1.8 km daily thermal composites of all five Great Lakes.
    AVHRR-based, GOES-supplemented. Best proxy for thermal anomaly before/after Landsat passes.

    Dataset IDs:
      GLSEA_GCS   — Daily SST composite, full Great Lakes (through ~Jan 2024)
      GLSEA2_GCS  — 7-day max cloud-free composite

    ERDDAP variable: sst  (sea_water_temperature, degrees_C)
    Coverage: 1995-01-01 → 2024-01-01 (AVHRR archive)
    """

    ERDDAP_BASE = 'https://apps.glerl.noaa.gov/erddap/griddap'
    DATASET = 'GLSEA_GCS'
    # Dataset ends ~2024-01-01; cap requests to avoid 404
    MAX_DATE = '2024-01-01'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'noaa_coastwatch')
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CESAROPS-WreckHunter2000/1.0'})

    def search(self, bbox: List[float], start: str, end: str) -> List[Dict]:
        """Return a list of date strings that should have SST coverage."""
        from datetime import date as _date
        d0 = datetime.strptime(start, '%Y-%m-%d').date()
        d1 = datetime.strptime(min(end, self.MAX_DATE), '%Y-%m-%d').date()
        if d0 > d1:
            print(f"  ⚠ GLSEA dataset ends {self.MAX_DATE} — no coverage for {start}→{end}")
            return []
        dates = []
        d = d0
        while d <= d1:
            dates.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=7)  # Weekly samples to avoid huge volumes
        return [{'date': s} for s in dates]

    def download_sst(self, date_str: str, bbox: List[float]) -> Optional[Path]:
        """Download a single day's SST netCDF for the given bbox."""
        lat_min, lon_min, lat_max, lon_max = bbox[0], bbox[1], bbox[2], bbox[3]
        # ERDDAP griddap URL — variable is 'sst', not 'surface_temp'
        url = (
            f"{self.ERDDAP_BASE}/{self.DATASET}.nc"
            f"?sst"
            f"[({date_str}T12:00:00Z):1:({date_str}T12:00:00Z)]"
            f"[({lat_min}):1:({lat_max})]"
            f"[({lon_min}):1:({lon_max})]"
        )
        dest = self.output_dir / f"glsea_sst_{date_str}.nc"
        if self.dry_run:
            print(f"  [DRY RUN] Would download: {dest.name}")
            return dest
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  [skip] {dest.name}")
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self.session.get(url, timeout=120)
        if resp.status_code != 200:
            print(f"  ⚠ GLSEA SST {date_str}: HTTP {resp.status_code}")
            return None
        with open(dest, 'wb') as f:
            f.write(resp.content)
        print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.2f} MB)")
        return dest

    def run(self, bbox: List[float], start: str, end: str, **_) -> List[Path]:
        print(f"\n{'='*60}")
        print(f"NOAA CoastWatch ERDDAP — Great Lakes SST (GLSEA, free)")
        print(f"  BBOX: {bbox}  |  {start} → {end}")
        print(f"{'='*60}")
        dates = self.search(bbox, start, end)
        print(f"  {len(dates)} sample dates")
        downloaded = []
        for entry in dates:
            result = self.download_sst(entry['date'], bbox)
            if result:
                downloaded.append(result)
        print(f"\n  Total: {len(downloaded)} SST files")
        return downloaded


# ── Source 8: MODIS Daily Thermal (MOD11A1 / MYD11A1) via NASA CMR ───────────

class MODISDownloader:
    """Download MODIS land surface temperature (LST) from NASA LP DAAC.

    Products:
      MOD11A1  — Terra daily 1-km LST (Day/Night) — Earthdata auth
      MYD11A1  — Aqua daily 1-km LST (Day/Night)  — Earthdata auth

    LST_Day_1km and LST_Night_1km bands cover Great Lakes thermocline
    and shallow nearshore thermal anomalies at 1 km resolution.
    """

    CMR_URL = 'https://cmr.earthdata.nasa.gov/search/granules.json'

    COLLECTIONS = {
        'mod11a1': {'short_name': 'MOD11A1', 'version': '061'},
        'myd11a1': {'short_name': 'MYD11A1', 'version': '061'},
        'viirs_lst': {'short_name': 'VNP21A1D', 'version': '002'},  # VIIRS day LST
    }

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'modis')
        self.session = earthdata_session()

    def search(self, bbox: List[float], start: str, end: str,
               product: str = 'mod11a1', max_results: int = 30) -> List[Dict]:
        coll = self.COLLECTIONS.get(product.lower())
        if not coll:
            print(f"  Unknown MODIS product: {product}")
            return []
        params = {
            'short_name': coll['short_name'],
            'version': coll['version'],
            'bounding_box': f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}',
            'temporal': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'page_size': max_results,
        }
        resp = self.session.get(self.CMR_URL, params=params)
        if resp.status_code != 200:
            print(f"  ⚠ CMR search failed: {resp.status_code}")
            return []
        granules = []
        for entry in resp.json().get('feed', {}).get('entry', []):
            for link in entry.get('links', []):
                href = link.get('href', '')
                if href.endswith('.hdf') or href.endswith('.nc'):
                    granules.append({
                        'title': entry.get('title', ''),
                        'href': href,
                        'time_start': entry.get('time_start', ''),
                        'product': product,
                    })
                    break
        return granules

    def run(self, bbox: List[float], start: str, end: str,
            products: List[str] = None, max_results: int = 20) -> List[Path]:
        if products is None:
            products = ['mod11a1', 'myd11a1']
        all_downloaded = []
        for prod in products:
            print(f"\n{'='*60}")
            print(f"MODIS LST — {prod.upper()} (1km thermal, daily)")
            print(f"  BBOX: {bbox}  |  {start} → {end}")
            print(f"{'='*60}")
            granules = self.search(bbox, start, end, prod, max_results)
            if not granules:
                print(f"  No granules found (check EARTHDATA_TOKEN in .env)")
                continue
            print(f"  Found {len(granules)} granules")
            for i, g in enumerate(granules):
                suffix = g['href'].rsplit('.', 1)[-1]
                dest = self.output_dir / prod / f"{g['title']}.{suffix}"
                if self.dry_run:
                    print(f"  [DRY RUN] {g['title']}")
                    all_downloaded.append(dest)
                    continue
                if dest.exists() and dest.stat().st_size > 0:
                    print(f"  [skip] {dest.name}")
                    all_downloaded.append(dest)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                resp = self.session.get(g['href'], stream=True, timeout=600)
                if resp.status_code != 200:
                    print(f"  ⚠ {resp.status_code}")
                    continue
                with open(dest, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                print(f"  [{i+1}/{len(granules)}] {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
                all_downloaded.append(dest)
        print(f"\n  Total: {len(all_downloaded)} MODIS files")
        return all_downloaded


# ── Source 9: ICESat-2 ATL03 (photon-level water, Earthdata) ─────────────────

class ICESat2Downloader:
    """Download ICESat-2 ATL03 (geolocated photons) for water-column penetration.

    ATL03 provides individual photon returns — useful for shallow-water bathymetry
    in the Great Lakes where water clarity allows ~15-25m penetration.
    ATL13 (inland water surface height) is already in PODAACDownloader.

    Uses existing EARTHDATA_TOKEN.
    """

    CMR_URL = 'https://cmr.earthdata.nasa.gov/search/granules.json'

    COLLECTIONS = {
        'atl03': {'short_name': 'ATL03', 'version': '006'},  # Geolocated photons
        'atl08': {'short_name': 'ATL08', 'version': '006'},  # Land/canopy height
        'atl13': {'short_name': 'ATL13', 'version': '006'},  # Inland water height
    }

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'icesat2')
        self.session = earthdata_session()

    def search(self, bbox: List[float], start: str, end: str,
               product: str = 'atl13', max_results: int = 20) -> List[Dict]:
        coll = self.COLLECTIONS.get(product.lower())
        if not coll:
            print(f"  Unknown ICESat-2 product: {product}")
            return []
        params = {
            'short_name': coll['short_name'],
            'version': coll['version'],
            'bounding_box': f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}',
            'temporal': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'page_size': max_results,
        }
        resp = self.session.get(self.CMR_URL, params=params)
        if resp.status_code != 200:
            print(f"  ⚠ CMR search failed: {resp.status_code}")
            return []
        granules = []
        for entry in resp.json().get('feed', {}).get('entry', []):
            for link in entry.get('links', []):
                href = link.get('href', '')
                if href.endswith('.h5') or href.endswith('.nc'):
                    granules.append({
                        'title': entry.get('title', ''),
                        'href': href,
                        'time_start': entry.get('time_start', ''),
                        'product': product,
                    })
                    break
        return granules

    def run(self, bbox: List[float], start: str, end: str,
            products: List[str] = None, max_results: int = 20) -> List[Path]:
        if products is None:
            products = ['atl13', 'atl03']
        all_downloaded = []
        for prod in products:
            print(f"\n{'='*60}")
            print(f"ICESat-2 — {prod.upper()}")
            print(f"  BBOX: {bbox}  |  {start} → {end}")
            print(f"{'='*60}")
            granules = self.search(bbox, start, end, prod, max_results)
            if not granules:
                print(f"  No granules (check EARTHDATA_TOKEN in .env)")
                continue
            print(f"  Found {len(granules)} granules")
            for i, g in enumerate(granules):
                dest = self.output_dir / prod / f"{g['title']}.h5"
                if self.dry_run:
                    print(f"  [DRY RUN] {g['title']}")
                    all_downloaded.append(dest)
                    continue
                if dest.exists() and dest.stat().st_size > 0:
                    print(f"  [skip] {dest.name}")
                    all_downloaded.append(dest)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                resp = self.session.get(g['href'], stream=True, timeout=600)
                if resp.status_code != 200:
                    print(f"  ⚠ {resp.status_code}")
                    continue
                with open(dest, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                print(f"  [{i+1}/{len(granules)}] {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
                all_downloaded.append(dest)
        print(f"\n  Total: {len(all_downloaded)} ICESat-2 files")
        return all_downloaded


# ── Source 10: USGS 3DEP LiDAR via The National Map (free, no auth) ──────────

class USGSLiDARDownloader:
    """Download LiDAR point clouds and DEMs from USGS 3DEP via the TNM API.

    Free — no authentication required.
    Covers all Great Lakes shorelines with 1m resolution LiDAR returns.

    Products available:
      - Lidar Point Cloud (LPC) — raw .laz point clouds
      - Digital Elevation Model (DEM 1m) — processed bare-earth and first-return
    """

    TNM_URL = 'https://tnmapi.cr.usgs.gov/api/products'

    # Great Lakes shoreline focus (slightly inland + nearshore)
    DATASETS = {
        'lpc': 'Lidar Point Cloud (LPC)',
        'dem_1m': 'Digital Elevation Model (DEM) 1 meter',
        'dem_3dep': '1/3 Arc Second DEM (10m)',
    }

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'lidar')
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CESAROPS-WreckHunter2000/1.0'})

    def search(self, bbox: List[float], dataset: str = 'lpc',
               max_results: int = 20) -> List[Dict]:
        """Search TNM for LiDAR products in bbox."""
        params = {
            'datasets': self.DATASETS.get(dataset, self.DATASETS['lpc']),
            'bbox': f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}',
            'max': max_results,
            'offset': 0,
            'outputFormat': 'JSON',
        }
        resp = self.session.get(self.TNM_URL, params=params, timeout=60)
        if resp.status_code != 200:
            print(f"  ⚠ TNM search failed: {resp.status_code}")
            return []
        data = resp.json()
        items = []
        for item in data.get('items', []):
            dl_url = item.get('downloadURL', '')
            if not dl_url:
                continue
            items.append({
                'title': item.get('title', ''),
                'href': dl_url,
                'size': item.get('sizeInBytes', 0),
                'pub_date': item.get('publicationDate', ''),
                'dataset': dataset,
            })
        return items

    def run(self, bbox: List[float], start: str = '', end: str = '',
            datasets: List[str] = None, max_results: int = 10, **_) -> List[Path]:
        if datasets is None:
            datasets = ['lpc']
        all_downloaded = []
        for ds in datasets:
            print(f"\n{'='*60}")
            print(f"USGS 3DEP LiDAR — {self.DATASETS.get(ds, ds)} (free)")
            print(f"  BBOX: {bbox}")
            print(f"{'='*60}")
            items = self.search(bbox, ds, max_results)
            if not items:
                print(f"  No LiDAR tiles found for this area")
                continue
            print(f"  Found {len(items)} tiles")
            for i, item in enumerate(items):
                fname = item['href'].rsplit('/', 1)[-1] or f"{ds}_{i}.laz"
                dest = self.output_dir / ds / fname
                if self.dry_run:
                    size_mb = item['size'] / 1e6 if item['size'] else 0
                    print(f"  [DRY RUN] {fname} ({size_mb:.0f} MB)")
                    all_downloaded.append(dest)
                    continue
                if dest.exists() and dest.stat().st_size > 0:
                    print(f"  [skip] {dest.name}")
                    all_downloaded.append(dest)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                resp = self.session.get(item['href'], stream=True, timeout=3600)
                if resp.status_code != 200:
                    print(f"  ⚠ {resp.status_code}")
                    continue
                with open(dest, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                print(f"  [{i+1}/{len(items)}] {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
                all_downloaded.append(dest)
        print(f"\n  Total: {len(all_downloaded)} LiDAR files")
        return all_downloaded


# ── Source 11: AWS Element84 STAC — Sentinel-2 L2A COG (free, no auth) ───────

class AWSSentinel2Downloader:
    """Download Sentinel-2 L2A GeoTIFFs from AWS Open Data via Element84 STAC.

    Free — no authentication. Cloud Optimized GeoTIFFs (COG) from the
    sentinel-cogs S3 bucket (us-west-2, public). 10m–60m resolution.

    Uses Element84 Earth Search STAC at https://earth-search.aws.element84.com/v1
    Same data as Copernicus Open Access Hub but zero-auth.

    Example bands: blue, green, red, nir, nir08, swir16, swir22, scl (cloud)
    """

    STAC_URL = 'https://earth-search.aws.element84.com/v1/search'
    COLLECTION = 'sentinel-2-l2a'
    DEFAULT_BANDS = ['blue', 'green', 'red', 'nir', 'nir08', 'swir16', 'swir22', 'scl']

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None,
                 bands: List[str] = None, max_cloud: float = 30.0):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'sentinel2_aws')
        self.bands = bands or self.DEFAULT_BANDS
        self.max_cloud = max_cloud
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CESAROPS-WreckHunter2000/1.0'})

    def search(self, bbox: List[float], start: str, end: str,
               max_results: int = 20) -> List[Dict]:
        """Search Element84 STAC for Sentinel-2 L2A items."""
        payload = {
            'collections': [self.COLLECTION],
            'bbox': [bbox[1], bbox[0], bbox[3], bbox[2]],
            'datetime': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'limit': max_results,
            'query': {'eo:cloud_cover': {'lt': self.max_cloud}},
            'sortby': [{'field': 'properties.eo:cloud_cover', 'direction': 'asc'}],
        }
        resp = self.session.post(self.STAC_URL, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"  ⚠ Element84 STAC search failed: {resp.status_code}")
            return []

        items = []
        for feat in resp.json().get('features', []):
            feat_id = feat['id']
            cloud = feat['properties'].get('eo:cloud_cover', -1)
            assets = feat.get('assets', {})
            item_bands = []
            for band_name in self.bands:
                asset = assets.get(band_name)
                if not asset:
                    continue
                href = asset.get('href', '')
                if href.startswith('s3://sentinel-cogs/'):
                    href = href.replace('s3://sentinel-cogs/',
                                        'https://sentinel-cogs.s3.us-west-2.amazonaws.com/', 1)
                item_bands.append({
                    'id': feat_id,
                    'title': feat_id,
                    'band': band_name,
                    'href': href,
                    'cloud_cover': cloud,
                    'time_start': feat['properties'].get('datetime', ''),
                })
            items.extend(item_bands)
        return items

    def run(self, bbox: List[float], start: str, end: str,
            max_results: int = 10, **_) -> List[Path]:
        print(f"\n{'='*60}")
        print(f"AWS Sentinel-2 L2A COG — Element84 STAC (free, no auth)")
        print(f"  BBOX: {bbox}  |  {start} → {end}")
        print(f"  Bands: {self.bands}")
        print(f"{'='*60}")

        band_files = self.search(bbox, start, end, max_results=max_results)
        if not band_files:
            print("  No Sentinel-2 scenes found.")
            return []

        unique_scenes = len({b['id'] for b in band_files})
        print(f"  Found {unique_scenes} scenes × {len(self.bands)} bands = {len(band_files)} files")

        downloaded = []
        for bf in band_files:
            fname = f"{bf['title']}.{bf['band']}.tif"
            dest = self.output_dir / fname
            if self.dry_run:
                print(f"  [DRY RUN] {fname}  (cloud {bf['cloud_cover']:.1f}%)")
                downloaded.append(dest)
                continue
            if dest.exists() and dest.stat().st_size > 0:
                print(f"  [skip] {dest.name}")
                downloaded.append(dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            resp = self.session.get(bf['href'], stream=True, timeout=600)
            if resp.status_code != 200:
                print(f"  ⚠ {bf['band']} HTTP {resp.status_code}")
                continue
            with open(dest, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            print(f"  {dest.name} ({dest.stat().st_size/1e6:.1f} MB, cloud {bf['cloud_cover']:.1f}%)")
            downloaded.append(dest)

        print(f"\n  Total: {len(downloaded)} Sentinel-2 COG files")
        return downloaded


# ── Source 12: AWS Element84 STAC — Landsat C2 L2 COG (free, no auth) ────────

class AWSLandsatDownloader:
    """Download Landsat Collection 2 Level-2 GeoTIFFs from USGS on AWS Open Data.

    Free — no authentication required. Cloud Optimized GeoTIFFs (COG) from
    usgs-landsat S3 bucket (us-west-2, public). 30m SR + 100m thermal.

    Uses Element84 Earth Search STAC at https://earth-search.aws.element84.com/v1
    Covers Landsat 4–9 C2 L2A SR+ST.

    Bands: coastal, blue, green, red, nir08, swir16, swir22, st_b10 (thermal),
           qa_pixel (cloud/shadow mask)
    """

    STAC_URL = 'https://earth-search.aws.element84.com/v1/search'
    COLLECTION = 'landsat-c2-l2'
    DEFAULT_BANDS = ['blue', 'green', 'red', 'nir08', 'swir16', 'swir22', 'st_b10', 'qa_pixel']

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None,
                 bands: List[str] = None, max_cloud: float = 30.0):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'landsat_aws')
        self.bands = bands or self.DEFAULT_BANDS
        self.max_cloud = max_cloud
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CESAROPS-WreckHunter2000/1.0'})

    def search(self, bbox: List[float], start: str, end: str,
               max_results: int = 10) -> List[Dict]:
        """Search Element84 STAC for Landsat C2 L2 items."""
        payload = {
            'collections': [self.COLLECTION],
            'bbox': [bbox[1], bbox[0], bbox[3], bbox[2]],
            'datetime': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'limit': max_results,
            'query': {'eo:cloud_cover': {'lt': self.max_cloud}},
            'sortby': [{'field': 'properties.eo:cloud_cover', 'direction': 'asc'}],
        }
        resp = self.session.post(self.STAC_URL, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"  ⚠ Element84 STAC search failed: {resp.status_code}")
            return []

        items = []
        for feat in resp.json().get('features', []):
            feat_id = feat['id']
            cloud = feat['properties'].get('eo:cloud_cover', -1)
            assets = feat.get('assets', {})
            for band_name in self.bands:
                asset = assets.get(band_name)
                if not asset:
                    continue
                href = asset.get('href', '')
                if href.startswith('s3://usgs-landsat/'):
                    href = href.replace('s3://usgs-landsat/',
                                        'https://usgs-landsat.s3.us-west-2.amazonaws.com/', 1)
                items.append({
                    'id': feat_id,
                    'title': feat_id,
                    'band': band_name,
                    'href': href,
                    'cloud_cover': cloud,
                    'time_start': feat['properties'].get('datetime', ''),
                })
        return items

    def run(self, bbox: List[float], start: str, end: str,
            max_results: int = 10, **_) -> List[Path]:
        print(f"\n{'='*60}")
        print(f"AWS Landsat C2 L2 COG — Element84 STAC (free, no auth)")
        print(f"  BBOX: {bbox}  |  {start} → {end}")
        print(f"  Bands: {self.bands}")
        print(f"{'='*60}")

        band_files = self.search(bbox, start, end, max_results=max_results)
        if not band_files:
            print("  No Landsat scenes found.")
            return []

        unique_scenes = len({b['id'] for b in band_files})
        print(f"  Found {unique_scenes} scenes × bands = {len(band_files)} files")

        downloaded = []
        for bf in band_files:
            fname = f"{bf['title']}.{bf['band']}.tif"
            dest = self.output_dir / fname
            if self.dry_run:
                print(f"  [DRY RUN] {fname}  (cloud {bf['cloud_cover']:.1f}%)")
                downloaded.append(dest)
                continue
            if dest.exists() and dest.stat().st_size > 0:
                print(f"  [skip] {dest.name}")
                downloaded.append(dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            resp = self.session.get(bf['href'], stream=True, timeout=600)
            if resp.status_code != 200:
                print(f"  ⚠ {bf['band']} HTTP {resp.status_code}")
                continue
            with open(dest, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            print(f"  {dest.name} ({dest.stat().st_size/1e6:.1f} MB, cloud {bf['cloud_cover']:.1f}%)")
            downloaded.append(dest)

        print(f"\n  Total: {len(downloaded)} Landsat COG files")
        return downloaded


# ── Main orchestrator ─────────────────────────────────────────────────────

def list_sources():
    """Print available data sources."""
    print(f"\n{'='*70}")
    print("CESAROPS Universal Downloader — Available Sources")
    print(f"{'='*70}")
    sources = {
        'asf':            'ASF HyP3 — Sentinel-1 SAR (RTC/InSAR) — Free, Earthdata auth',
        'copernicus':     'Copernicus Data Space — Sentinel-1/2 — Free, CDSE auth',
        'podaac':         'NASA PO.DAAC — SWOT SSH, ICESat-2 ATL13 — Free, Earthdata auth',
        'icesat2':        'ICESat-2 ATL03/ATL13 — Photon LiDAR bathymetry — Free, Earthdata auth',
        'usgs':           'USGS Earth Explorer — Landsat 8/9 — Free, API key required',
        'hls':            'NASA HLS — Harmonized Landsat Sentinel-2 — Free, Earthdata auth (AWS fallback)',
        'sentinel2_aws':  'AWS Sentinel-2 L2A COG — Element84 STAC — Free, no auth (10–60m)',
        'landsat_aws':    'AWS Landsat C2 L2 COG — Element84 STAC — Free, no auth (30m+thermal)',
        'modis':          'MODIS MOD11A1/MYD11A1 — 1km thermal daily — Free, Earthdata auth',
        'noaa_coastwatch':'NOAA CoastWatch ERDDAP — Great Lakes GLSEA SST (~2024) — Free, no auth',
        'lidar':          'USGS 3DEP LiDAR — 1m point clouds, DEMs — Free, no auth',
        'sentinel_hub':   'Sentinel Hub — On-demand processed imagery — Free tier available',
    }
    for src, desc in sources.items():
        print(f"  {src:18s}  {desc}")
    print(f"\nSensor aliases: sar, optical, swot, icesat, landsat, hls, thermal, lidar, sst, sentinel2, landsat_aws")
    print(f"{'='*70}")

def main():
    parser = argparse.ArgumentParser(description='CESAROPS Universal Satellite Data Downloader')
    parser.add_argument('--area', type=str, help='Preset area name (see --list-areas)')
    parser.add_argument('--bbox', type=str, help='Custom bbox: lat_min,lon_min,lat_max,lon_max')
    parser.add_argument('--dates', type=str, nargs=2, help='Date range: START END (YYYY-MM-DD)')
    parser.add_argument('--sensors', type=str, default='all',
                        help='Comma-separated: sar,optical,swot,icesat,icesat2,landsat,hls,thermal,modis,lidar,sst,all')
    parser.add_argument('--max-results', type=int, default=20, help='Max results per source')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be downloaded')
    parser.add_argument('--list-sources', action='store_true', help='List available data sources')
    parser.add_argument('--list-areas', action='store_true', help='List preset areas')
    parser.add_argument('--output', type=str, help='Override output directory')
    parser.add_argument('--no-rtc', action='store_true', help='Download raw SLC instead of RTC for SAR')
    args = parser.parse_args()

    if args.list_sources:
        list_sources()
        return

    if args.list_areas:
        print("\nPreset areas:")
        for name, info in AREAS.items():
            print(f"  {name:25s}  {info['label']}  [{info['bbox']}]")
        return

    # Resolve bbox
    if args.area:
        if args.area not in AREAS:
            print(f"Unknown area: {args.area}")
            print("Available areas:")
            for name in AREAS:
                print(f"  {name}")
            return
        bbox = AREAS[args.area]['bbox']
        print(f"Area: {AREAS[args.area]['label']}")
    elif args.bbox:
        parts = [float(x) for x in args.bbox.split(',')]
        bbox = parts
    else:
        bbox = AREAS['straits_of_mackinac']['bbox']
        print(f"Default area: Straits of Mackinac")

    # Resolve dates
    if args.dates:
        start, end = args.dates
    else:
        # Default: last 6 months of available data
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')

    # Resolve sensors
    sensor_map = {
        'sar': 'asf',
        'optical': 'copernicus',
        'swot': 'podaac',
        'icesat': 'podaac',
        'icesat2': 'podaac',
        'landsat': 'landsat_aws',
        'landsat_aws': 'landsat_aws',
        'sentinel2': 'sentinel2_aws',
        'sentinel2_aws': 'sentinel2_aws',
        'hls': 'hls',
        'thermal': 'modis',
        'modis': 'modis',
        'lidar': 'lidar',
        'sst': 'noaa_coastwatch',
        'coastwatch': 'noaa_coastwatch',
    }
    if args.sensors == 'all':
        sources = ['asf', 'copernicus', 'podaac', 'usgs', 'hls',
                   'sentinel2_aws', 'landsat_aws', 'modis', 'noaa_coastwatch', 'lidar']
    else:
        sources = [sensor_map.get(s.strip(), s.strip()) for s in args.sensors.split(',')]

    output_base = Path(args.output) if args.output else (REPO / 'downloads')

    all_downloaded = []
    for src in sources:
        try:
            if src == 'asf':
                dl = ASFDownloader(dry_run=args.dry_run, output_dir=output_base / 'sar')
                result = dl.run(bbox, start, end, max_granules=args.max_results, process_rtc=not args.no_rtc)
                all_downloaded.extend(result)
            elif src == 'copernicus':
                dl = CopernicusDownloader(dry_run=args.dry_run, output_dir=output_base / 'copernicus')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'podaac':
                dl = PODAACDownloader(dry_run=args.dry_run, output_dir=output_base / 'podaac')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'usgs':
                dl = USGSDownloader(dry_run=args.dry_run, output_dir=output_base / 'usgs')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'hls':
                dl = HLSDownloader(dry_run=args.dry_run, output_dir=output_base / 'hls')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'modis':
                dl = MODISDownloader(dry_run=args.dry_run, output_dir=output_base / 'modis')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'noaa_coastwatch':
                dl = NOAACoastwatchDownloader(dry_run=args.dry_run, output_dir=output_base / 'noaa_coastwatch')
                result = dl.run(bbox, start, end)
                all_downloaded.extend(result)
            elif src == 'lidar':
                dl = USGSLiDARDownloader(dry_run=args.dry_run, output_dir=output_base / 'lidar')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'sentinel2_aws':
                dl = AWSSentinel2Downloader(dry_run=args.dry_run, output_dir=output_base / 'sentinel2_aws')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'landsat_aws':
                dl = AWSLandsatDownloader(dry_run=args.dry_run, output_dir=output_base / 'landsat_aws')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            elif src == 'icesat2':
                dl = ICESat2Downloader(dry_run=args.dry_run, output_dir=output_base / 'icesat2')
                result = dl.run(bbox, start, end, max_results=args.max_results)
                all_downloaded.extend(result)
            else:
                print(f"  Unknown source: {src}")
        except Exception as e:
            print(f"  ⚠ {src} failed: {e}")

    print(f"\n{'='*70}")
    print(f"DOWNLOAD SUMMARY")
    print(f"{'='*70}")
    print(f"  Total files: {len(all_downloaded)}")
    total_size = sum(f.stat().st_size for f in all_downloaded if f.exists())
    print(f"  Total size: {total_size / 1e6:.1f} MB")
    print(f"  Output: {output_base}")
    print(f"{'='*70}")

if __name__ == '__main__':
    main()
