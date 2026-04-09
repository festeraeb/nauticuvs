#!/usr/bin/env python3
"""Direct targeted download of T16TFR Sep 3 2024 key bands and Landsat-9 thermal/SWIR.
Sentinel-2: public S3 COGs (no auth needed)
Landsat-9:  Microsoft Planetary Computer Azure Blob (requires SAS token sign)
"""
import requests
from pathlib import Path

BASE_S2  = 'https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/16/T/FR/2024/9/S2B_16TFR_20240903_0_L2A'
SCENE_L9 = 'LC09_L2SP_022028_20240903_20240904_02_T1'
BASE_L9  = (f'https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/'
            f'standard/oli-tirs/2024/022/028/{SCENE_L9}')

PC_SIGN_API = 'https://planetarycomputer.microsoft.com/api/sas/v1/sign'

OUT = Path('downloads/hls')
OUT.mkdir(parents=True, exist_ok=True)


def pc_sign(url: str) -> str:
    """Sign an Azure Blob URL via Planetary Computer's public SAS token API."""
    r = requests.get(PC_SIGN_API, params={'href': url}, timeout=20)
    if r.status_code == 200:
        return r.json().get('href', url)
    print(f'  [warn] PC sign failed ({r.status_code}) — trying unsigned')
    return url


downloads = [
    # ── Sentinel-2 T16TFR Sep 3 2024 (4% cloud — primary Straits tile) ──────
    (f'{BASE_S2}/B02.tif',  'S2B_16TFR_20240903_0_L2A.blue.tif',   False),
    (f'{BASE_S2}/B03.tif',  'S2B_16TFR_20240903_0_L2A.green.tif',  False),
    (f'{BASE_S2}/B04.tif',  'S2B_16TFR_20240903_0_L2A.red.tif',    False),
    (f'{BASE_S2}/B11.tif',  'S2B_16TFR_20240903_0_L2A.swir16.tif', False),
    # ── Landsat-9 Sep 3 2024 — thermal + SWIR from Planetary Computer ────────
    (f'{BASE_L9}/{SCENE_L9}_ST_B10.TIF', f'{SCENE_L9}.lwir11.tif',  True),   # thermal
    (f'{BASE_L9}/{SCENE_L9}_SR_B2.TIF',  f'{SCENE_L9}.blue.tif',    True),   # blue
    (f'{BASE_L9}/{SCENE_L9}_SR_B6.TIF',  f'{SCENE_L9}.swir16.tif',  True),   # SWIR HC
]

for url, fname, need_sign in downloads:
    dest = OUT / fname
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f'[skip] {fname} ({dest.stat().st_size/1e6:.1f} MB)')
        continue

    # Sign URL if hosted on Planetary Computer (Azure Blob)
    dl_url = pc_sign(url) if need_sign else url

    try:
        h = requests.head(dl_url, timeout=15)
        if h.status_code != 200:
            print(f'[skip] {fname}: HEAD {h.status_code} — trying next')
            continue
        sz = int(h.headers.get('Content-Length', 0))
        print(f'Downloading {fname} ({sz/1e6:.0f} MB)...')
    except Exception as e:
        print(f'[error] {fname}: {e}')
        continue
    try:
        with requests.get(dl_url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(dest, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        print(f'  -> {dest.name} ({dest.stat().st_size/1e6:.1f} MB) OK')
    except Exception as e:
        print(f'  [error] {e}')

print('\nDone.')
