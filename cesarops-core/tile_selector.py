#!/usr/bin/env python3
"""
CESAROPS Tile Selector
=======================
Reads .geometry.json sidecars from downloaded tiles and selects/ranks
tiles for a given scan mode.

MODES
-----
HISTORIC_WRECK  — optical only, any season, rank by depth_correction_factor
BATHY_3D        — strict optical, summer only, apply Stumpf zenith correction
SAR_SEARCH      — SAR always; add optical if cloud<15
SAR_AFTER_EVENT — SAR tiles in event_date±3d range only; NO optical needed

DESIGN PRINCIPLE FOR AGENT USE
-------------------------------
Every decision is a binary test against a numeric threshold.
The output is a structured JSON with exact tile lists and flags.
No judgment is left open for the calling agent.

Usage:
  python tile_selector.py --mode historic_wreck --dir downloads/hls
  python tile_selector.py --mode bathy_3d --dir downloads/hls
  python tile_selector.py --mode sar_search --dir downloads/hls
  python tile_selector.py --mode sar_after_event --event-date 2024-09-03 --dir downloads/hls

Output (stdout JSON):
{
  "mode": "historic_wreck",
  "selected_tiles": [
    {"tif": "S2B_16TFR_20240903.blue.tif", "rank": 1, "reason": "cloud=6% depth_corr=1.375"},
    ...
  ],
  "excluded_tiles": [
    {"tif": "S2B_16TGS_20240903.blue.tif", "reason": "cloud=26% > 20% threshold"},
    ...
  ],
  "no_sar_tiles": false,
  "no_optical_tiles": false,
  "summary": "4 optical tiles selected, 2 excluded"
}
"""

import sys, json, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# ── Mode thresholds (all numeric — no judgment calls) ─────────────────────

MODES = {
    'historic_wreck': {
        'cloud_max':   20,   # %
        'sun_el_min':  20,   # deg
        'sensor':      'optical',
        'rank_by':     'depth_correction',   # lower is better (nearer nadir)
        'description': 'Wreck detection: any optical tile with low cloud',
    },
    'bathy_3d': {
        'cloud_max':   10,   # %
        'sun_el_min':  35,   # deg
        'sensor':      'optical',
        'rank_by':     'cloud_cover_pct',
        'description': 'Stumpf depth mapping: strict optical quality required',
    },
    'sar_search': {
        'cloud_max':   15,   # % for optional optical overlay
        'sun_el_min':  20,   # deg for optional optical overlay
        'sensor':      'both',
        'rank_by':     'cloud_cover_pct',
        'description': 'SAR always; optical added if available and cloud<15%',
    },
    'sar_after_event': {
        'cloud_max':   100,  # not relevant — SAR only
        'sun_el_min':  0,
        'sensor':      'sar',
        'rank_by':     'datetime_utc',
        'description': 'Delta-detect: SAR tiles within event_date ± 3 days only',
        'event_window_days': 3,
    },
}


def load_sidecars(tile_dir: Path) -> list:
    """Load all .geometry.json sidecars in tile_dir."""
    sidecars = sorted(tile_dir.glob('*.geometry.json'))
    result = []
    for sc in sidecars:
        with open(sc) as f:
            try:
                result.append(json.load(f))
            except json.JSONDecodeError:
                pass
    return result


def select_tiles(geo_list: list, mode: str, event_date_str: str = None) -> dict:
    """
    Apply mode-specific selection rules to a list of geometry dicts.
    Returns structured output dict (the agent acts on this directly).
    """
    if mode not in MODES:
        return {'error': f'Unknown mode: {mode}. Valid modes: {list(MODES.keys())}'}

    cfg = MODES[mode]
    selected = []
    excluded = []

    event_dt = None
    event_window = timedelta(days=cfg.get('event_window_days', 3))
    if event_date_str:
        try:
            event_dt = datetime.fromisoformat(event_date_str).replace(tzinfo=timezone.utc)
        except ValueError:
            return {'error': f'Invalid event_date format: {event_date_str}. Use YYYY-MM-DD.'}

    for geo in geo_list:
        tif   = geo.get('tif_file', '?')
        cloud = geo.get('cloud_cover_pct', -1)
        sun   = geo.get('sun_elevation_deg', 0)
        stype = geo.get('sensor_type', 'optical')
        dt_s  = geo.get('datetime_utc')
        depth = geo.get('depth_correction', 1.0)

        # ── SAR_AFTER_EVENT: SAR + date window only ────────────────────────
        if mode == 'sar_after_event':
            if stype != 'sar':
                excluded.append({'tif': tif, 'reason': 'sar_after_event mode requires SAR tiles only'})
                continue
            if event_dt is None:
                # No date filter — include all SAR
                selected.append({'tif': tif, 'rank': 0,
                                  'reason': 'SAR tile (no event date filter)',
                                  'datetime_utc': dt_s})
                continue
            # Check date window
            if dt_s is None:
                excluded.append({'tif': tif, 'reason': 'no acquisition datetime — cannot verify event window'})
                continue
            try:
                tile_dt = datetime.fromisoformat(dt_s.replace('Z', '+00:00'))
            except ValueError:
                excluded.append({'tif': tif, 'reason': f'datetime parse error: {dt_s}'})
                continue
            delta = abs((tile_dt - event_dt).total_seconds()) / 86400
            if delta <= event_window.days:
                selected.append({'tif': tif, 'rank': delta,
                                  'reason': f'SAR tile {delta:.1f}d from event',
                                  'datetime_utc': dt_s})
            else:
                excluded.append({'tif': tif,
                                  'reason': f'{delta:.1f}d from event > {event_window.days}d window'})
            continue

        # ── SAR_SEARCH: SAR always, optical conditional ────────────────────
        if mode == 'sar_search':
            if stype == 'sar':
                selected.append({'tif': tif, 'rank': 0,
                                  'reason': 'SAR tile always included for sar_search'})
            elif cloud < 0:
                excluded.append({'tif': tif, 'reason': 'cloud cover unknown — cannot assess optical usability'})
            elif cloud <= cfg['cloud_max'] and sun >= cfg['sun_el_min']:
                selected.append({'tif': tif, 'rank': cloud,
                                  'reason': f'optical overlay: cloud={cloud}% sun={sun:.1f}°'})
            else:
                reason_parts = []
                if cloud > cfg['cloud_max']:
                    reason_parts.append(f'cloud={cloud}% > {cfg["cloud_max"]}% threshold')
                if sun < cfg['sun_el_min']:
                    reason_parts.append(f'sun={sun:.1f}° < {cfg["sun_el_min"]}° threshold')
                excluded.append({'tif': tif, 'reason': '; '.join(reason_parts)})
            continue

        # ── HISTORIC_WRECK / BATHY_3D: optical only ───────────────────────
        if stype == 'sar':
            excluded.append({'tif': tif, 'reason': f'mode={mode} does not use SAR tiles'})
            continue
        if stype == 'thermal':
            excluded.append({'tif': tif, 'reason': f'mode={mode} does not use thermal tiles'})
            continue
        if cloud < 0:
            excluded.append({'tif': tif, 'reason': 'cloud cover unknown — skipping'})
            continue
        if cloud > cfg['cloud_max']:
            excluded.append({'tif': tif, 'reason': f'cloud={cloud}% > {cfg["cloud_max"]}% threshold'})
            continue
        if sun < cfg['sun_el_min']:
            excluded.append({'tif': tif, 'reason': f'sun={sun:.1f}° < {cfg["sun_el_min"]}° minimum'})
            continue

        rank = depth if cfg['rank_by'] == 'depth_correction' else cloud
        selected.append({
            'tif':             tif,
            'rank':            rank,
            'cloud_cover_pct': cloud,
            'sun_elevation':   sun,
            'depth_correction': depth,
            'reason':          f'cloud={cloud}% sun={sun:.1f}° depth_corr={depth:.3f}',
        })

    # Sort selected by rank ascending
    selected.sort(key=lambda x: x.get('rank', 0))
    for i, s in enumerate(selected):
        s['rank'] = i + 1

    sar_count     = sum(1 for s in selected if 'SAR' in s.get('reason', '').upper()
                        or 'sar' in s.get('tif', '').lower())
    optical_count = len(selected) - sar_count

    no_sar     = (mode in ('sar_search', 'sar_after_event') and sar_count == 0)
    no_optical = (mode in ('historic_wreck', 'bathy_3d') and len(selected) == 0)

    summary_parts = []
    if selected:
        summary_parts.append(f'{len(selected)} tile(s) selected')
    if excluded:
        summary_parts.append(f'{len(excluded)} excluded')
    if no_sar:
        summary_parts.append('WARNING: no SAR tiles available')
    if no_optical:
        summary_parts.append('WARNING: no usable optical tiles')

    return {
        'mode':            mode,
        'mode_description': cfg['description'],
        'selected_tiles':  selected,
        'excluded_tiles':  excluded,
        'no_sar_tiles':    no_sar,
        'no_optical_tiles': no_optical,
        'summary':         '; '.join(summary_parts) if summary_parts else 'no tiles processed',
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Select tiles for a CESAROPS scan mode')
    ap.add_argument('--mode', required=True,
                    choices=list(MODES.keys()),
                    help='Scan mode: historic_wreck | bathy_3d | sar_search | sar_after_event')
    ap.add_argument('--dir',  default='downloads/hls',
                    help='Directory containing .geometry.json sidecar files')
    ap.add_argument('--event-date', default=None,
                    help='Event date for sar_after_event mode (YYYY-MM-DD)')
    ap.add_argument('--pretty', action='store_true', default=True,
                    help='Pretty-print JSON output (default: True)')
    args = ap.parse_args()

    tile_dir = Path(args.dir)
    geo_list = load_sidecars(tile_dir)
    if not geo_list:
        print(json.dumps({'error': f'No .geometry.json sidecars found in {tile_dir}. '
                                    f'Run: python tile_geometry.py --dir {tile_dir}'}))
        sys.exit(1)

    result = select_tiles(geo_list, args.mode, event_date_str=args.event_date)
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent))

    # Exit 1 if agent must stop (no required tile type available)
    if result.get('no_sar_tiles') or result.get('no_optical_tiles'):
        sys.exit(1)
