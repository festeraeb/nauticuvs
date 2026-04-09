#!/usr/bin/env python3
"""
CESAROPS Tile Geometry Module
==============================
Fetches or computes illumination/view geometry for every satellite tile and
writes a .geometry.json sidecar alongside the .tif file.

The sidecar is the single source of truth for tile selection logic.
tile_selector.py reads these to decide which tiles to use for which modes.

Called by:
  - universal_downloader.py  after each tile download
  - standalone: python tile_geometry.py --dir downloads/hls

Sidecar schema (.geometry.json):
{
  "granule_id":       "HLS.S30.T16TFR.2024247T162839.v2.0",
  "tif_file":         "S2B_16TFR_20240903_0_L2A.blue.tif",
  "platform":         "sentinel-2b",          # sentinel-2a/b, landsat-8/9
  "sensor_type":      "optical",               # optical | sar | thermal
  "datetime_utc":     "2024-09-03T16:40:57Z",
  "cloud_cover_pct":  6,                       # 0-100, -1 = unknown
  "sun_elevation_deg": 46.7,                   # computed if not in STAC
  "sun_azimuth_deg":   162.3,
  "solar_zenith_deg":  43.3,
  "cos_solar_zenith":  0.727,
  "depth_correction":  1.375,                  # 1/cos(solar_zenith) for Stumpf
  "blue_1e_depth_m":   18.2,                   # effective penetration depth
  "view_zenith_deg":   5.0,                    # off-nadir (platform default if not in STAC)
  "usable_optical":    true,                   # cloud<20 AND sun_el>20
  "usable_bathy":      false,                  # cloud<10 AND sun_el>35
  "usable_thermal":    true,                   # cloud<20
  "source":           "stac+computed"          # stac | computed | stac+computed
}
"""

import sys, os, json, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import requests

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# ── Config ──────────────────────────────────────────────────────────────────

def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip()
    return env

_dotenv = _load_env(Path(__file__).parent / '.env')
TOKEN = os.environ.get('EARTHDATA_TOKEN', _dotenv.get('EARTHDATA_TOKEN', ''))

STAC_LPCLOUD = 'https://cmr.earthdata.nasa.gov/stac/LPCLOUD/search'

# Platform defaults for off-nadir view angle (degrees)
PLATFORM_VIEW_ZENITH = {
    'sentinel-2a': 10.3,
    'sentinel-2b': 10.3,
    'landsat-8':    7.5,
    'landsat-9':    7.5,
}

# Blue band 1/e attenuation depth in very clear (Kd=0.04/m) water, nadir
BLUE_1E_DEPTH_NADIR_M = 25.0

# Usability thresholds
THRESH_OPTICAL_CLOUD  = 20   # % — optical/thermal usable
THRESH_BATHY_CLOUD    = 10   # % — Stumpf bathy usable
THRESH_OPTICAL_SUN_EL = 20   # deg — minimum for any optical
THRESH_BATHY_SUN_EL   = 35   # deg — minimum for depth mapping


# ── Solar angle computation ─────────────────────────────────────────────────

def compute_solar_angles(dt_utc: datetime, lat: float, lon: float) -> Dict[str, float]:
    """
    Compute solar elevation and azimuth for a given UTC datetime and location.
    Accuracy: ±1° (sufficient for Beer-Lambert / depth corrections).

    Uses NOAA solar position algorithm (simplified Spencer + Grena).
    No external dependencies needed.

    Returns dict: sun_elevation_deg, sun_azimuth_deg, solar_zenith_deg
    """
    # Day of year
    doy = dt_utc.timetuple().tm_yday
    # Equation of time (minutes), solar declination (degrees) via Spencer 1971
    B = math.radians((360 / 365) * (doy - 1))
    eot_min = (0.000075 + 0.001868 * math.cos(B) - 0.032077 * math.sin(B)
               - 0.014615 * math.cos(2*B) - 0.04089 * math.sin(2*B)) * 229.18
    decl_deg = math.degrees(
        0.006918 - 0.399912*math.cos(B) + 0.070257*math.sin(B)
        - 0.006758*math.cos(2*B) + 0.000907*math.sin(2*B)
        - 0.002697*math.cos(3*B) + 0.00148*math.sin(3*B)
    )
    decl = math.radians(decl_deg)
    lat_r = math.radians(lat)

    # True solar time
    utc_minutes = dt_utc.hour * 60 + dt_utc.minute + dt_utc.second / 60
    solar_noon_lon_minutes = 12 * 60 - lon * 4  # lon in degrees, west = negative
    tst = utc_minutes + eot_min + (lon * 4)
    hour_angle = math.radians((tst / 4) - 180)

    # Solar elevation
    sin_el = (math.sin(lat_r) * math.sin(decl)
              + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
    sin_el = max(-1.0, min(1.0, sin_el))
    elevation = math.degrees(math.asin(sin_el))

    # Solar azimuth (degrees from North, clockwise)
    cos_az = ((math.sin(decl) - math.sin(lat_r) * sin_el)
              / (math.cos(lat_r) * math.cos(math.asin(sin_el)) + 1e-10))
    cos_az = max(-1.0, min(1.0, cos_az))
    azimuth = math.degrees(math.acos(cos_az))
    if hour_angle > 0:
        azimuth = 360 - azimuth  # afternoon: sun in west

    zenith = 90 - elevation
    return {
        'sun_elevation_deg': round(elevation, 2),
        'sun_azimuth_deg':   round(azimuth, 2),
        'solar_zenith_deg':  round(zenith, 2),
    }


# ── STAC metadata fetch ─────────────────────────────────────────────────────

def fetch_stac_geometry(granule_id: str, bbox_center: tuple) -> Optional[Dict]:
    """
    Query CMR STAC for a specific granule to get cloud_cover and any angle metadata.
    granule_id: e.g. 'HLS.S30.T16TFR.2024247T162839.v2.0'
    bbox_center: (lat, lon) for solar angle computation fallback

    Returns dict of raw STAC properties, or None on failure.
    """
    # Parse date from granule ID: ...T16TFR.2024247T162839... → DOY 247, 2024
    parts = granule_id.split('.')
    dt_utc = None
    for p in parts:
        if len(p) == 15 and 'T' in p:  # e.g. 2024247T162839
            try:
                year = int(p[:4])
                doy  = int(p[4:7])
                hh   = int(p[8:10])
                mm   = int(p[10:12])
                ss   = int(p[12:14])
                base = datetime(year, 1, 1, tzinfo=timezone.utc)
                dt_utc = base + timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss)
            except (ValueError, IndexError):
                pass

    headers = {'Authorization': f'Bearer {TOKEN}'} if TOKEN else {}
    try:
        r = requests.get(
            f'https://cmr.earthdata.nasa.gov/stac/LPCLOUD/collections/'
            f'{".".join(granule_id.split(".")[:2])}/items/{granule_id}',
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            item = r.json()
            props = item.get('properties', {})
            return {'props': props, 'dt_utc': dt_utc}
    except Exception:
        pass

    # Fallback: STAC search by date+bbox
    if dt_utc is not None:
        date_str = dt_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        lat, lon = bbox_center
        try:
            body = {
                'ids': [granule_id],
                'bbox': [lon-0.5, lat-0.5, lon+0.5, lat+0.5],
                'datetime': f'{date_str}/{date_str}',
                'limit': 1,
            }
            r = requests.post(STAC_LPCLOUD, json=body, headers=headers, timeout=10)
            if r.status_code == 200:
                feats = r.json().get('features', [])
                if feats:
                    return {'props': feats[0].get('properties', {}), 'dt_utc': dt_utc}
        except Exception:
            pass

    return {'props': {}, 'dt_utc': dt_utc}


# ── Granule ID parsing from filename ────────────────────────────────────────

def platform_from_filename(fname: str) -> tuple:
    """
    Parse platform, sensor_type, granule_id, and tile center lat/lon from filename.

    Returns (platform, sensor_type, granule_id, (lat, lon))
    Handles:
      S2B_16TFR_20240903_0_L2A.blue.tif  → sentinel-2b, optical
      LC09_L2SP_022028_20240903_...tif    → landsat-9, optical/thermal
      S1A_IW_SLC...tif                    → sentinel-1a, sar
    """
    f = fname.upper()
    # Platform
    if f.startswith('S2A'):
        platform = 'sentinel-2a'
        sensor_type = 'optical'
    elif f.startswith('S2B'):
        platform = 'sentinel-2b'
        sensor_type = 'optical'
    elif f.startswith('LC08'):
        platform = 'landsat-8'
        sensor_type = 'optical'
    elif f.startswith('LC09'):
        platform = 'landsat-9'
        sensor_type = 'optical'
    elif 'LWIR' in f or 'THERMAL' in f or 'B10' in f or 'ST_B10' in f:
        platform = 'landsat-9'
        sensor_type = 'thermal'
    elif f.startswith('S1A') or f.startswith('S1B') or f.startswith('S1C'):
        platform = 'sentinel-1'
        sensor_type = 'sar'
    else:
        platform = 'unknown'
        sensor_type = 'optical'

    # Sensor type override for thermal bands
    if any(x in f for x in ['LWIR', 'THERMAL', '.B10.', '.B11.']):
        sensor_type = 'thermal'

    # MGRS tile center (approximate) from tile ID in filename
    # e.g. 16TFR → UTM zone 16, band T, 100km grid FR
    # We use the scan area center as approximation (adequate for solar angles)
    lat, lon = 45.88, -84.55  # Straits of Mackinac center
    for part in fname.replace('.', '_').split('_'):
        if len(part) == 5 and part[:2].isdigit() and part[2].isalpha() and part[3:].isalpha():
            # Rough MGRS decode: just use Straits center
            pass

    # Parse date from filename
    import re
    date_match = re.search(r'(\d{8})', fname)
    if date_match:
        ds = date_match.group(1)
        try:
            dt = datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:8]), 16, 30, 0, tzinfo=timezone.utc)
        except ValueError:
            dt = None
    else:
        dt = None

    granule_id = fname.rsplit('.', 2)[0] if fname.count('.') >= 2 else fname
    return platform, sensor_type, granule_id, (lat, lon), dt


# ── Main: build sidecar for a single tif ────────────────────────────────────

def build_geometry_sidecar(tif_path: Path,
                            stac_props: Optional[Dict] = None,
                            dt_utc: Optional[datetime] = None,
                            bbox_center: tuple = (45.88, -84.55),
                            force: bool = False) -> Dict:
    """
    Build and write a .geometry.json sidecar for a .tif file.
    Returns the geometry dict.
    """
    sidecar_path = tif_path.with_suffix('').with_suffix('') # strip .tif → .geometry.json
    # Handle double extension like S2B_16TFR.blue.tif → strip both
    stem = tif_path.stem  # e.g. S2B_16TFR_20240903_0_L2A.blue
    sidecar_path = tif_path.parent / (stem + '.geometry.json')

    if sidecar_path.exists() and not force:
        with open(sidecar_path) as f:
            return json.load(f)

    fname = tif_path.name
    platform, sensor_type, granule_id, (lat, lon), file_dt = platform_from_filename(fname)

    # Use provided dt or file-parsed dt
    acq_dt = dt_utc or file_dt

    # STAC cloud cover
    cloud_pct = -1
    stac_source = 'computed'
    if stac_props:
        for k, v in stac_props.items():
            if 'cloud' in k.lower():
                try:
                    cloud_pct = int(float(v))
                    stac_source = 'stac+computed'
                except (ValueError, TypeError):
                    pass

    # SAR never has cloud cover issue
    if sensor_type == 'sar':
        cloud_pct = 0

    # Solar angles
    if acq_dt is not None:
        angles = compute_solar_angles(acq_dt, lat, lon)
    else:
        # Cannot compute — use conservative defaults
        angles = {
            'sun_elevation_deg': 30.0,
            'sun_azimuth_deg':   150.0,
            'solar_zenith_deg':  60.0,
        }

    sun_el  = angles['sun_elevation_deg']
    sun_az  = angles['sun_azimuth_deg']
    zen     = angles['solar_zenith_deg']
    cos_zen = math.cos(math.radians(zen))
    depth_correction = 1.0 / cos_zen if cos_zen > 0.01 else 99.0
    blue_depth = round(BLUE_1E_DEPTH_NADIR_M * cos_zen, 1)

    view_zenith = PLATFORM_VIEW_ZENITH.get(platform, 7.5)

    # Usability flags — binary, no judgment required downstream
    if sensor_type == 'sar':
        usable_optical  = False
        usable_bathy    = False
        usable_thermal  = False
        usable_sar      = True
    elif sensor_type == 'thermal':
        usable_optical  = False
        usable_bathy    = False
        usable_thermal  = (cloud_pct >= 0 and cloud_pct < THRESH_OPTICAL_CLOUD and
                           sun_el > THRESH_OPTICAL_SUN_EL)
        usable_sar      = False
    else:
        usable_optical  = (cloud_pct < THRESH_OPTICAL_CLOUD and
                           sun_el > THRESH_OPTICAL_SUN_EL) if cloud_pct >= 0 else False
        usable_bathy    = (cloud_pct < THRESH_BATHY_CLOUD and
                           sun_el > THRESH_BATHY_SUN_EL) if cloud_pct >= 0 else False
        usable_thermal  = False
        usable_sar      = False

    geo = {
        'granule_id':        granule_id,
        'tif_file':          fname,
        'platform':          platform,
        'sensor_type':       sensor_type,
        'datetime_utc':      acq_dt.isoformat() if acq_dt else None,
        'cloud_cover_pct':   cloud_pct,
        'sun_elevation_deg': sun_el,
        'sun_azimuth_deg':   sun_az,
        'solar_zenith_deg':  zen,
        'cos_solar_zenith':  round(cos_zen, 4),
        'depth_correction':  round(depth_correction, 3),
        'blue_1e_depth_m':   blue_depth,
        'view_zenith_deg':   view_zenith,
        'usable_optical':    usable_optical,
        'usable_bathy':      usable_bathy,
        'usable_thermal':    usable_thermal,
        'usable_sar':        usable_sar,
        'source':            stac_source,
    }

    with open(sidecar_path, 'w') as f:
        json.dump(geo, f, indent=2)

    return geo


# ── Batch: build sidecars for all tifs in a directory ───────────────────────

def build_all_sidecars(tile_dir: Path, force: bool = False, verbose: bool = True) -> list:
    """
    Build geometry sidecars for all .tif files in tile_dir.
    Fetches STAC cloud cover per unique granule (one request per granule, not per band).
    Returns list of geometry dicts.
    """
    tifs = sorted(tile_dir.glob('*.tif'))
    if not tifs:
        print(f'  No .tif files found in {tile_dir}')
        return []

    # Batch STAC fetch: one query per date for all granules in the directory
    # Group by date string in filename to avoid N requests for N bands of same granule
    from collections import defaultdict
    import re

    date_groups: dict = defaultdict(list)
    for t in tifs:
        m = re.search(r'(\d{8})', t.name)
        date_key = m.group(1) if m else 'unknown'
        date_groups[date_key].append(t)

    results = []
    headers = {'Authorization': f'Bearer {TOKEN}'} if TOKEN else {}

    for date_key, files in sorted(date_groups.items()):
        # Fetch STAC once for this date batch
        stac_cache: dict = {}
        if date_key != 'unknown' and len(date_key) == 8:
            try:
                ds = f'{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}'
                body = {
                    'collections': ['HLSL30.v2.0', 'HLSS30.v2.0'],
                    'datetime': f'{ds}T00:00:00Z/{ds}T23:59:59Z',
                    'bbox': [-85.5, 45.0, -83.5, 46.5],
                    'limit': 50,
                }
                r = requests.post(STAC_LPCLOUD, json=body, headers=headers, timeout=15)
                if r.status_code == 200:
                    for feat in r.json().get('features', []):
                        fid = feat.get('id', '')
                        stac_cache[fid] = feat.get('properties', {})
            except Exception as e:
                if verbose:
                    print(f'  STAC fetch for {date_key}: {e}')

        # Build sidecar for each file in this date group
        for tif in files:
            # Skip files that already have sidecars unless force
            sidecar = tif.parent / (tif.stem + '.geometry.json')
            if sidecar.exists() and not force:
                with open(sidecar) as f:
                    results.append(json.load(f))
                continue

            # Find matching STAC entry
            stac_props = None
            fname_upper = tif.name.upper()
            for fid, props in stac_cache.items():
                # Match by platform prefix and MGRS tile
                if any(part in fid.upper() for part in fname_upper.split('_')[:3]):
                    stac_props = props
                    break
            # Best-effort: if only one STAC entry for this date, use it
            if stac_props is None and len(stac_cache) == 1:
                stac_props = list(stac_cache.values())[0]

            geo = build_geometry_sidecar(tif, stac_props=stac_props, force=force)
            results.append(geo)
            if verbose:
                flag = []
                if geo['usable_optical']:  flag.append('OPT')
                if geo['usable_bathy']:    flag.append('BATHY')
                if geo['usable_thermal']:  flag.append('THERM')
                if geo['usable_sar']:      flag.append('SAR')
                flags = '+'.join(flag) if flag else 'SKIP'
                print(f'  {tif.name:<50s}  cloud={geo["cloud_cover_pct"]:>3}%  '
                      f'sun={geo["sun_elevation_deg"]:>5.1f}°  [{flags}]')

    return results


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Build geometry sidecars for all .tif files')
    ap.add_argument('--dir',   default='downloads/hls', help='Directory containing .tif files')
    ap.add_argument('--force', action='store_true',     help='Rebuild even if sidecar exists')
    args = ap.parse_args()

    tile_dir = Path(args.dir)
    print(f'Building geometry sidecars in: {tile_dir}')
    print(f'Usability thresholds: optical<{THRESH_OPTICAL_CLOUD}% cloud, sun>{THRESH_OPTICAL_SUN_EL}°')
    print(f'                       bathy<{THRESH_BATHY_CLOUD}% cloud, sun>{THRESH_BATHY_SUN_EL}°')
    print()
    results = build_all_sidecars(tile_dir, force=args.force, verbose=True)
    opt   = sum(1 for r in results if r['usable_optical'])
    bathy = sum(1 for r in results if r['usable_bathy'])
    therm = sum(1 for r in results if r['usable_thermal'])
    sar   = sum(1 for r in results if r['usable_sar'])
    print(f'\nTotal: {len(results)} files  |  optical={opt}  bathy={bathy}  thermal={therm}  sar={sar}')
