#!/usr/bin/env python3
"""
CESAROPS Mission Report — Straits of Mackinac Shipwreck Survey
Generates a prioritized analysis from crossref_report.json + 2024 scan JSON.
"""
import json
import glob
from pathlib import Path

REPO = Path(__file__).parent

def load_crossref():
    path = REPO / 'outputs' / 'crossref_report.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    # Support both flat list (old) and dict (new) format
    if isinstance(data, list):
        return data
    return data.get('all_results', [])

def load_latest_2024_scan():
    files = sorted(glob.glob(str(REPO / 'outputs' / 'straits_mackinac_2024_scan_*.json')))
    if not files:
        return None
    return json.loads(Path(files[-1]).read_text(encoding='utf-8')), files[-1]

def geo_context(lat, lon):
    """Return geographic context based on coordinates."""
    if lat < 45.77 and lon < -84.65:
        return 'Open Straits south, west approach — pre-bridge zone'
    if lat < 45.77:
        return 'Open Straits south — Michigan shoreline approach'
    if 45.77 <= lat <= 45.87 and -84.77 <= lon <= -84.68:
        return 'Mackinac Bridge corridor — primary wreck zone'
    if lat > 45.95 and lon > -84.45:
        return 'Eastern Straits outlet — Bois Blanc / Lake Huron approach'
    if lat > 45.95:
        return 'Northern Straits / Lake Michigan-Huron transition'
    if 45.87 <= lat <= 45.97 and lon < -84.55:
        return 'Mid-Straits north channel — St. Ignace side'
    return 'Mid-Straits'

report_lines = [
    '=' * 80,
    'CESAROPS MISSION REPORT — STRAITS OF MACKINAC SHIPWRECK SURVEY',
    'Multi-epoch satellite analysis: HLS L30 (2015) + Sentinel-2 + Landsat-9 (2024)',
    '=' * 80,
    '',
]

# ── Cross-reference results ──────────────────────────────────────────────────
xr = load_crossref()
hi  = [r for r in xr if r.get('confidence') == 'HIGH']
med = [r for r in xr if r.get('confidence') == 'MEDIUM']

report_lines += [
    f'CROSS-EPOCH SUMMARY',
    f'  Data eras compared : 2015-2016 (winter HLS) vs 2024 (summer S2+L9)',
    f'  HIGH confidence    : {len(hi)} persistent multi-epoch anomalies',
    f'  MEDIUM confidence  : {len(med)} strong single-epoch anomalies',
    '',
    'HIGH CONFIDENCE ANOMALIES (appear in BOTH 2015 and 2024 independently)',
    '-' * 70,
]

for i, r in enumerate(sorted(hi, key=lambda x: -abs(x.get('max_abs_z', 0))), 1):
    sensors = '+'.join(sorted(r.get('types', ['?'])))
    z = r.get('zscore', 0)
    geo = geo_context(r['lat'], r['lon'])
    kw = f'  *** KNOWN WRECK: {r["known_wreck_name"]}' if r.get('known_wreck_name') else ''
    l5 = '  *** LINE 5 CORRIDOR' if r.get('line5_candidate') else ''
    report_lines += [
        f'  [{i:02d}] lat={r["lat"]:.5f}  lon={r["lon"]:.5f}  z={z:.2f}',
        f'        sensors: {sensors}',
        f'        context: {geo}{kw}{l5}',
        '',
    ]

report_lines += [
    'MEDIUM CONFIDENCE ANOMALIES (strong single-epoch, not yet cross-validated)',
    '-' * 70,
]
for r in med:
    sensors = '+'.join(sorted(r.get('types', ['?'])))
    geo = geo_context(r['lat'], r['lon'])
    report_lines.append(
        f'  lat={r["lat"]:.5f}  lon={r["lon"]:.5f}  z={r["zscore"]:.2f}  sensors={sensors}  [{geo}]')

# ── Line 5 thermal findings ──────────────────────────────────────────────────
result = load_latest_2024_scan()
if result:
    scan_data, scan_file = result
    line5 = [d for d in scan_data.get('detections', []) if d.get('line5_candidate')]
    thermal_line5 = [d for d in line5 if d.get('type') == 'thermal']
    report_lines += [
        '',
        'LINE 5 PIPELINE CORRIDOR — THERMAL ANALYSIS',
        '-' * 70,
        f'  Total Line 5 corridor flags  : {len(line5)}',
        f'  Thermal warm anomalies (LWIR): {len(thermal_line5)}',
        f'  Signal character             : POSITIVE z=+3.18 to +3.27 (WARMER than surroundings)',
        f'  Cluster center               : lat~45.778N, lon~84.733W',
        f'  Pipeline section             : South Straits crossing entry, Mackinaw City side',
        f'  Sensor                       : Landsat-9 LWIR (Sep 3 2024 14:30 UTC)',
        '',
        '  INTERPRETATION:',
        '    Warm thermal anomaly at the Line 5 south touchdown. Possible causes:',
        '    1. Cathodic protection anode discharge creating electrochemical heat',
        '    2. Shallow-water turbulence over buried pipeline warming near-surface layer',  
        '    3. Near-shore solar/thermal amplification at pipeline anchor point',
        '    4. Low-probability: hydrocarbon seep (would also need SWIR dark confirmation)',
        '    RECOMMENDATION: Cross-validate with SWIR HC scan (re-scan in progress).',
        '    Coordinate with Enbridge for pipeline inspection log for this section.',
    ]

# ── Physical assessment of top candidates ───────────────────────────────────
report_lines += [
    '',
    'PRIORITY TARGET ASSESSMENT',
    '-' * 70,
    '',
    '  TARGET ALPHA — lat=45.7005, lon=-84.7696  z=-5.86',
    '    Sensors  : optical_blue + optical_green + optical_red + thermal COLD-SINK',
    '    Location : Open Straits south, ~7km SW of Mackinac Bridge, Michigan shore approach',
    '    Depth est: 15-40m (optical bands can penetrate to ~25m)' ,
    '    Character: DARK in all visible bands + COLD thermal — STRONGEST multi-sensor signature',
    '    Assessment: HIGH PRIORITY. Dark optical + thermal cold = dense submerged mass at',
    '               water-penetrating depth. Consistent with iron/steel wreck reflecting',
    '               differently from sandy/clay bottom AND acting as thermal cold-sink.',
    '    Action   : Side-scan sonar survey recommended (Grid: 45.695-45.710N, 84.760-84.780W)',
    '',
    '  TARGET BRAVO — lat=45.8439, lon=-84.7024  z=-3.88',
    '    Sensors  : optical_blue + optical_green + optical_red (all dark)',
    '    Location : Main Mackinac Straits shipping channel, 3km north of bridge',
    '    Depth est: 60-100m (main channel) — beyond optical penetration',
    '    Character: Triple optical dark at HIGH depth — likely surface/near-surface optical',
    '               expression (bottom sediment shadow, upwelling discoloration, or shallow',
    '               debris field on channel margin)',
    '    Assessment: MEDIUM-HIGH. In known wreck zone. Optical hits at channel depth may',
    '               indicate shallow debris or upwelling from deeper structure.',
    '    Action   : Sub-bottom profiler + multibeam survey',
    '',
    '  TARGET CHARLIE — lat=45.94-45.95, lon=-84.37  z=-3.53 to -3.87',
    '    Sensors  : optical_blue + optical_red (cluster of 3 closely spaced hits)',
    '    Location : Eastern Straits at Bois Blanc Island / Lake Huron outlet',
    '    Character: Multiple co-located optical dark hits — consistent with a reef or large',
    '               submerged structure creating localized surface optical changes',
    '    Assessment: Notable due to clustering and proximity. Cross-check NOAA chart 14882.',
    '',
    '  TARGET DELTA — Line 5 corridor thermal cluster',
    '    Sensors  : Landsat-9 LWIR thermal WARM (+3.27 sigma)',
    '    Location : lat=45.777-45.781, lon=-84.728-84.733 (south Line 5 crossing)',
    '    Assessment: Warm anomaly at pipeline anchor. Not a wreck candidate. Pipeline',
    '               infrastructure monitoring flag. Track for change over time.',
]

report_lines += [
    '',
    'METHODOLOGY NOTES',
    '-' * 70,
    '  Sensors used  : HLS Landsat-30m (2015), Sentinel-2 10m (2024), Landsat-9 30m (2024)',
    '  Dates         : March 27 2015 (winter, possible ice) | September 3 2024 (clear, 4% cloud)',
    '  Optical depth : ~25m maximum for blue band in clear Great Lakes water',
    '  Known wrecks  : All at 49-110m depth — BELOW optical penetration (calibration baseline)',
    '  HC scan status: SWIR resolution fix applied; re-scan in progress (HC seep detection)',
    '  Coordinate sys: WGS84 (EPSG:4326)',
    '  KMZ outputs   : outputs/straits_mackinac_2024_scan_*.kmz (Google Earth ready)',
    '',
    '=' * 80,
]

report_text = '\n'.join(report_lines)
print(report_text)

report_path = REPO / 'outputs' / 'cesarops_straits_report.txt'
report_path.write_text(report_text, encoding='utf-8')
print(f'\n[OK] Report saved: {report_path}')
