"""
swot_ssh_extractor.py

Downloads SWOT_L2_LR_SSH_Expert granules from PO.DAAC and extracts
Sea Surface Height (SSH) anomaly values at each census coordinate.

- Downloads Expert-only granules (full geophysical corrections applied)
- Extracts ssha (SSH anomaly) variable at nearest nadir point to each coordinate
- Writes outputs/calibration/swot_ssh_anomalies.json
- Updates acquisition_queue status in DB
- Then calls swot_displacement_layer.apply_ssh_anomaly_from_file() to push
  values into stationary_anchors / new_arrivals and re-run triple-lock.

SWOT LR SSH NetCDF variables used:
  longitude, latitude  — nadir track coordinates (1D along-track)
  ssha                 — sea surface height anomaly (metres, relative to mean)
  quality_flag         — 0 = good
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO    = Path(__file__).resolve().parent
DB_PATH = REPO / 'LAKE_MICHIGAN_CENSUS_2026.db'
OUT_DIR = REPO / 'outputs' / 'calibration'
SSH_OUT = OUT_DIR / 'swot_ssh_anomalies.json'

# Earthdata token paths — configurable via .env or environment variables
def _get_token_paths() -> list:
    """Get possible Earthdata token locations from .env or defaults."""
    env_token = os.environ.get('EARTHDATA_TOKEN', '')
    if env_token:
        return []  # Token provided directly via env var

    # Default fallback paths
    return [
        Path('c:/Users/thomf/programming/Bagrecovery/erie_remote/erie_remote_data/.earthdata_token'),
        Path('c:/Users/thomf/programming/Bagrecovery/sentinel_hunt/earthdata_token.json'),
    ]

_TOKEN_PATHS = _get_token_paths()

# Only download Expert product — has all geophysical corrections
EXPERT_TAG = 'SSH_Expert'

# Max search radius to snap a census coordinate to a SWOT nadir point (metres)
SNAP_RADIUS_M = 12_000.0   # SWOT LR swath half-width ~10 km

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_token() -> str:
    # Check environment variable first
    env_token = os.environ.get('EARTHDATA_TOKEN', '')
    if env_token:
        return env_token

    # Check .env file
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith('EARTHDATA_TOKEN='):
                _, _, val = line.partition('=')
                token = val.strip()
                if token:
                    return token

    # Fall back to token files
    for tp in _TOKEN_PATHS:
        if tp.exists():
            try:
                txt = tp.read_text(encoding='utf-8').strip()
                if tp.suffix == '.json':
                    return json.loads(txt).get('earthdata_token', '')
                return txt
            except Exception:
                continue
    return ''


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _auth_session(token: str) -> requests.Session:
    s = requests.Session()
    if token:
        s.headers.update({'Authorization': f'Bearer {token}'})
    s.headers.update({'User-Agent': 'WreckHunter2000/1.0'})
    return s

# ── Load census coordinates ───────────────────────────────────────────────────

def load_census_coords(conn) -> list[dict]:
    """Return all anchor + arrival coordinates with table/id for update."""
    cur = conn.cursor()
    coords = []
    cur.execute('SELECT id, lat, lon FROM stationary_anchors')
    for row in cur.fetchall():
        coords.append({'table': 'stationary_anchors', 'id': row[0],
                       'lat': row[1], 'lon': row[2]})
    cur.execute('SELECT id, lat, lon FROM new_arrivals')
    for row in cur.fetchall():
        coords.append({'table': 'new_arrivals', 'id': row[0],
                       'lat': row[1], 'lon': row[2]})
    return coords

# ── Fetch Expert granule URLs from DB ─────────────────────────────────────────

def get_expert_urls(conn) -> list[tuple[str, str]]:
    """Return (tile, url) for Expert granules only."""
    cur = conn.cursor()
    cur.execute("""
        SELECT tile, reason FROM acquisition_queue
        WHERE product LIKE 'SWOT%' AND status='PENDING'
    """)
    rows = cur.fetchall()
    expert = []
    for tile, reason in rows:
        if EXPERT_TAG not in tile and EXPERT_TAG not in (reason or ''):
            continue
        # URL is embedded in reason field
        url = None
        for part in (reason or '').split('URL: '):
            if len(part) > 10 and part.startswith('https://'):
                url = part.strip()
                break
        if url:
            expert.append((tile, url))
    return expert

# ── Download + extract SSH ────────────────────────────────────────────────────

def extract_ssh_from_granule(nc_path: Path, coords: list[dict]) -> list[dict]:
    """
    Open a SWOT LR SSH NetCDF, find nearest nadir point to each coordinate,
    return SSH anomaly values for points within SNAP_RADIUS_M.
    """
    try:
        import netCDF4 as nc
    except ImportError:
        try:
            import h5py
            return _extract_ssh_h5py(nc_path, coords)
        except ImportError:
            print('[!] Neither netCDF4 nor h5py available — cannot read NetCDF')
            return []

    results = []
    try:
        with nc.Dataset(str(nc_path), 'r') as ds:
            # SWOT LR SSH variable names
            lons = np.array(ds.variables.get('longitude',
                            ds.variables.get('lon', None))[:]).flatten()
            lats = np.array(ds.variables.get('latitude',
                            ds.variables.get('lat', None))[:]).flatten()

            # ssha preferred; fall back to ssh
            if 'ssha' in ds.variables:
                ssha = np.ma.filled(ds.variables['ssha'][:].flatten(), np.nan)
            elif 'ssh_karin_2' in ds.variables:
                ssha = np.ma.filled(ds.variables['ssh_karin_2'][:].flatten(), np.nan)
            else:
                print(f'[!] No ssha/ssh_karin_2 in {nc_path.name}')
                return []

            # quality flag — 0 = good
            if 'quality_flag' in ds.variables:
                qf = np.array(ds.variables['quality_flag'][:]).flatten()
                good = (qf == 0)
            else:
                good = np.ones(len(lons), dtype=bool)

            for coord in coords:
                clat, clon = coord['lat'], coord['lon']
                dists = np.array([_haversine_m(clat, clon, float(lats[i]), float(lons[i]))
                                  for i in range(len(lats))])
                idx = int(np.argmin(dists))
                if dists[idx] > SNAP_RADIUS_M:
                    continue
                if not good[idx] or np.isnan(ssha[idx]):
                    continue
                results.append({
                    'table':          coord['table'],
                    'id':             coord['id'],
                    'lat':            coord['lat'],
                    'lon':            coord['lon'],
                    'ssh_anomaly_m':  float(ssha[idx]),
                    'snap_dist_m':    round(float(dists[idx]), 1),
                    'granule':        nc_path.name,
                })
    except Exception as e:
        print(f'[!] NetCDF read error {nc_path.name}: {e}')
    return results


def _extract_ssh_h5py(nc_path: Path, coords: list[dict]) -> list[dict]:
    """h5py fallback for NetCDF4/HDF5 files."""
    import h5py
    results = []
    try:
        with h5py.File(str(nc_path), 'r') as f:
            def _get(keys):
                for k in keys:
                    if k in f:
                        return np.array(f[k]).flatten()
                return None

            lons = _get(['longitude', 'lon'])
            lats = _get(['latitude', 'lat'])
            ssha = _get(['ssha', 'ssh_karin_2'])
            if lons is None or lats is None or ssha is None:
                return []

            for coord in coords:
                clat, clon = coord['lat'], coord['lon']
                dists = np.array([_haversine_m(clat, clon, float(lats[i]), float(lons[i]))
                                  for i in range(len(lats))])
                idx = int(np.argmin(dists))
                if dists[idx] > SNAP_RADIUS_M or np.isnan(float(ssha[idx])):
                    continue
                results.append({
                    'table': coord['table'], 'id': coord['id'],
                    'lat': coord['lat'], 'lon': coord['lon'],
                    'ssh_anomaly_m': float(ssha[idx]),
                    'snap_dist_m': round(float(dists[idx]), 1),
                    'granule': nc_path.name,
                })
    except Exception as e:
        print(f'[!] h5py read error {nc_path.name}: {e}')
    return results


def download_and_extract(session: requests.Session, tile: str, url: str,
                         coords: list[dict], conn) -> list[dict]:
    """Download one Expert granule to a temp file, extract SSH, update queue status."""
    cur = conn.cursor()
    print(f'[SWOT] Downloading {tile[:50]}...')
    try:
        resp = session.get(url, timeout=120, stream=True)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            for chunk in resp.iter_content(chunk_size=1 << 20):
                tmp.write(chunk)

        size_mb = tmp_path.stat().st_size / 1e6
        print(f'[SWOT]   Downloaded {size_mb:.1f} MB — extracting SSH...')

        hits = extract_ssh_from_granule(tmp_path, coords)
        tmp_path.unlink(missing_ok=True)

        status = f'DONE: {len(hits)} coord hits'
        cur.execute("UPDATE acquisition_queue SET status=? WHERE tile=?", (status, tile))
        conn.commit()
        print(f'[SWOT]   {tile[:40]}: {len(hits)} coordinate hits')
        return hits

    except requests.HTTPError as e:
        status = f'HTTP_ERROR: {e.response.status_code}'
        cur.execute("UPDATE acquisition_queue SET status=? WHERE tile=?", (status, tile))
        conn.commit()
        print(f'[SWOT]   HTTP {e.response.status_code} — {tile[:40]}')
        return []
    except Exception as e:
        status = f'ERROR: {str(e)[:80]}'
        cur.execute("UPDATE acquisition_queue SET status=? WHERE tile=?", (status, tile))
        conn.commit()
        print(f'[SWOT]   Error: {e}')
        return []

# ── Aggregate SSH anomalies per coordinate ────────────────────────────────────

def aggregate_anomalies(all_hits: list[dict]) -> list[dict]:
    """
    Collapse per-granule hits into per-coordinate records.
    surface_height_anomaly_m = median of all valid passes (robust to outliers).
    pass_count = number of passes with a valid reading.
    """
    from collections import defaultdict
    by_coord = defaultdict(list)
    for h in all_hits:
        key = (h['table'], h['id'])
        by_coord[key].append(h['ssh_anomaly_m'])

    aggregated = []
    for (table, row_id), values in by_coord.items():
        median_ssh = float(np.median(values))
        aggregated.append({
            'table':           table,
            'id':              row_id,
            'lat':             next(h['lat'] for h in all_hits
                                   if h['table'] == table and h['id'] == row_id),
            'lon':             next(h['lon'] for h in all_hits
                                   if h['table'] == table and h['id'] == row_id),
            'ssh_anomaly_m':   round(median_ssh, 4),
            'pass_count':      len(values),
            'all_passes_m':    [round(v, 4) for v in values],
        })
    return aggregated

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'[+] SWOT SSH Extractor — {datetime.now(timezone.utc).isoformat()}')

    if not DB_PATH.exists():
        print(f'[!] DB not found. Run lake_census_engine.py first.')
        sys.exit(1)

    # check netCDF4 / h5py availability
    has_nc4, has_h5 = False, False
    try:
        import netCDF4; has_nc4 = True
    except ImportError:
        pass
    try:
        import h5py; has_h5 = True
    except ImportError:
        pass

    if not has_nc4 and not has_h5:
        print('[!] Neither netCDF4 nor h5py is installed.')
        print('    Install with: pip install netCDF4')
        print('    or:           pip install h5py')
        sys.exit(1)

    print(f'[+] NetCDF reader: {"netCDF4" if has_nc4 else "h5py fallback"}')

    conn    = sqlite3.connect(str(DB_PATH))
    token   = _load_token()
    session = _auth_session(token)
    print(f'[+] Auth token: {"loaded" if token else "MISSING — downloads will likely 401"}')

    coords      = load_census_coords(conn)
    expert_urls = get_expert_urls(conn)
    print(f'[+] Census coordinates: {len(coords)}  (anchors + arrivals)')
    print(f'[+] Expert granules queued: {len(expert_urls)}')

    if not expert_urls:
        print('[!] No Expert granule URLs found in acquisition_queue.')
        print('    Re-run swot_displacement_layer.py to re-populate.')
        conn.close()
        sys.exit(1)

    all_hits = []
    for i, (tile, url) in enumerate(expert_urls, 1):
        print(f'[{i}/{len(expert_urls)}] ', end='', flush=True)
        hits = download_and_extract(session, tile, url, coords, conn)
        all_hits.extend(hits)

    print(f'\n[+] Total coordinate hits across all passes: {len(all_hits)}')

    if not all_hits:
        print('[!] Zero SSH hits extracted.')
        print('    Possible causes:')
        print('    - All passes outside SNAP_RADIUS_M (12 km) of corridor coordinates')
        print('    - Auth failure (401) — check earthdata token')
        print('    - SWOT nadir track does not cross corridor on these cycle dates')
        conn.close()
        sys.exit(0)

    aggregated = aggregate_anomalies(all_hits)
    print(f'[+] Unique coordinates with SSH data: {len(aggregated)}')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SSH_OUT, 'w', encoding='utf-8') as f:
        json.dump(aggregated, f, indent=2)
    print(f'[+] Written {SSH_OUT}')

    # Push values into DB and re-run triple-lock
    print('[+] Applying SSH anomalies to DB and re-running triple-lock...')
    from swot_displacement_layer import (
        apply_ssh_anomaly_from_file, run_triple_lock_validation, print_report
    )
    apply_ssh_anomaly_from_file(conn, SSH_OUT)
    triple_stats = run_triple_lock_validation(conn)
    print_report(conn, [], triple_stats)

    conn.close()
    print('[+] Done.')


if __name__ == '__main__':
    main()
