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
    """Download Sentinel-1 SAR via ASF HyP3 API.

    Two modes:
      - Direct SLC download (raw granules for local processing)
      - Submit HyP3 job for on-demand RTC processing (cloud, free)
    """

    API_BASE = 'https://hyp3-api.asf.alaska.edu'
    CMR_BASE = 'https://cmr.earthdata.nasa.gov/search/granules'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'sar')
        self.session = earthdata_session()

    def search_granules(self, bbox: List[float], start: str, end: str,
                        platform: str = 'SENTINEL-1A,SENTINEL-1B',
                        max_results: int = 50) -> List[Dict]:
        """Search for Sentinel-1 SLC granules in area/time via CMR."""
        params = {
            'short_name': 'SENTINEL-1A_SLC SENTINEL-1B_SLC',
            'bounding_box': f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}',
            'temporal': f'{start}T00:00:00Z/{end}T23:59:59Z',
            'platform': platform,
            'page_size': max_results,
        }
        resp = self.session.get(self.CMR_BASE, params=params, headers={
            'Accept': 'application/json',
        })
        resp.raise_for_status()
        data = resp.json()
        granules = []
        for entry in data.get('feed', {}).get('entry', []):
            for link in entry.get('links', []):
                if link.get('href', '').endswith('.zip') or 'S1' in link.get('href', ''):
                    granules.append({
                        'id': entry.get('id', ''),
                        'title': entry.get('title', ''),
                        'href': link['href'],
                        'start_time': entry.get('time_start', ''),
                        'polarization': entry.get('additional_attributes', [{}])[0].get('value', 'VV+VH'),
                    })
                    break
        return granules

    def submit_rtc_job(self, granule_name: str, dem: str = 'GLO30',
                       scale: str = 'DECIBEL') -> str:
        """Submit a HyP3 RTC processing job. Returns job_id."""
        payload = {
            'job_type': 'RTC_GAMMA',
            'name': f'cesarops_rtc_{granule_name[:40]}',
            'granules': [granule_name],
            'job_parameters': {
                'dem_name': dem,
                'scale': scale,
                'radiometry': 'GAMMA0',
                'resolution': 30,
                'speckle_filter': True,
            },
        }
        if self.dry_run:
            print(f"  [DRY RUN] Would submit RTC job for: {granule_name}")
            return 'dry-run-job-id'

        resp = self.session.post(f'{self.API_BASE}/jobs', json=payload)
        resp.raise_for_status()
        job = resp.json()
        print(f"  HyP3 job submitted: {job.get('job_id', '?')}")
        print(f"    Granule: {granule_name}")
        print(f"    Status: {job.get('status', 'PENDING')}")
        return job.get('job_id', '')

    def wait_for_job(self, job_id: str, poll_interval: int = 120,
                     max_wait: int = 36000) -> Optional[str]:
        """Poll HyP3 job until SUCCEEDED/FAILED. Returns download URL or None."""
        if self.dry_run:
            return None

        deadline = time.time() + max_wait
        while time.time() < deadline:
            resp = self.session.get(f'{self.API_BASE}/jobs/{job_id}')
            resp.raise_for_status()
            job = resp.json()
            status = job.get('status', 'UNKNOWN')
            print(f"  Job {job_id}: {status}")

            if status == 'SUCCEEDED':
                # Find download link
                for link in job.get('job_parameters', {}).get('output_bucket', []) or job.get('links', []):
                    if isinstance(link, dict) and link.get('type') == 'application/octet-stream':
                        return link.get('href', '')
                # Fallback: first link
                for link in job.get('links', []):
                    if isinstance(link, dict) and 'href' in link:
                        return link['href']
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
                dest = self.output_dir / ds / f"{g['title']}.nc"
                if self.dry_run:
                    print(f"  [DRY RUN] Would download: {g['href']}")
                    all_downloaded.append(dest)
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                resp = self.session.get(g['href'], stream=True, timeout=600)
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
        # Return first download URL from results
        for prep in result.get('preparingDownloads', []) + result.get('availableDownloads', []):
            for url in prep.get('urls', []):
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
    """Download HLS (Harmonized Landsat Sentinel-2) from NASA."""

    CMR_URL = 'https://cmr.earthdata.nasa.gov/search/granules.json'

    def __init__(self, dry_run: bool = False, output_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.output_dir = output_dir or (REPO / 'downloads' / 'hls')
        self.session = earthdata_session()

    def search(self, bbox: List[float], start: str, end: str,
               product: str = 'HLSS30',  # HLS Sentinel-2
               max_results: int = 50) -> List[Dict]:
        """Search for HLS granules."""
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

        data = resp.json()
        granules = []
        for entry in data.get('feed', {}).get('entry', []):
            for link in entry.get('links', []):
                if link.get('href', '').endswith('.tif') or 'HLS' in link.get('href', ''):
                    granules.append({
                        'id': entry.get('id', ''),
                        'title': entry.get('title', ''),
                        'href': link['href'],
                        'time_start': entry.get('time_start', ''),
                    })
                    break
        return granules

    def run(self, bbox: List[float], start: str, end: str,
            max_results: int = 20) -> List[Path]:
        """Search and download HLS granules."""
        print(f"\n{'='*60}")
        print(f"NASA HLS — Harmonized Landsat Sentinel-2")
        print(f"  BBOX: {bbox}")
        print(f"  Range: {start} to {end}")
        print(f"{'='*60}")

        granules = self.search(bbox, start, end, max_results=max_results)
        if not granules:
            print("  No granules found.")
            return []

        print(f"  Found {len(granules)} granules")
        downloaded = []
        for i, g in enumerate(granules):
            print(f"\n[{i+1}/{len(granules)}] {g['title']}")
            dest = self.output_dir / f"{g['title']}.nc"
            if self.dry_run:
                print(f"  [DRY RUN] Would download: {g['href']}")
                downloaded.append(dest)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            resp = self.session.get(g['href'], stream=True, timeout=600)
            if resp.status_code != 200:
                print(f"  ⚠ Download failed: {resp.status_code}")
                continue
            with open(dest, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            print(f"  Downloaded: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
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


# ── Main orchestrator ─────────────────────────────────────────────────────

def list_sources():
    """Print available data sources."""
    print(f"\n{'='*70}")
    print("CESAROPS Universal Downloader — Available Sources")
    print(f"{'='*70}")
    sources = {
        'asf': 'ASF HyP3 — Sentinel-1 SAR (RTC/InSAR) — Free, Earthdata auth',
        'copernicus': 'Copernicus Data Space — Sentinel-1/2 — Free, CDSE auth',
        'podaac': 'NASA PO.DAAC — SWOT SSH, ICESat-2 ATL13 — Free, Earthdata auth',
        'usgs': 'USGS Earth Explorer — Landsat 8/9 — Free, API key required',
        'hls': 'NASA HLS — Harmonized Landsat Sentinel-2 — Free, Earthdata auth',
        'sentinel_hub': 'Sentinel Hub — On-demand processed imagery — Free tier available',
    }
    for src, desc in sources.items():
        print(f"  {src:15s}  {desc}")
    print(f"\n{'='*70}")

def main():
    parser = argparse.ArgumentParser(description='CESAROPS Universal Satellite Data Downloader')
    parser.add_argument('--area', type=str, help='Preset area name (see --list-areas)')
    parser.add_argument('--bbox', type=str, help='Custom bbox: lat_min,lon_min,lat_max,lon_max')
    parser.add_argument('--dates', type=str, nargs=2, help='Date range: START END (YYYY-MM-DD)')
    parser.add_argument('--sensors', type=str, default='all', help='Comma-separated: sar,optical,swot,lidar,hls,all')
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
        'landsat': 'usgs',
        'hls': 'hls',
    }
    if args.sensors == 'all':
        sources = ['asf', 'copernicus', 'podaac', 'usgs', 'hls']
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
