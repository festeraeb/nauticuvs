#!/usr/bin/env python3
"""
Cross-reference CESAROPS scan outputs to find persistent multi-year anomalies.

Persistent anomalies (same location in both 2015-2016 and 2024 scans) are 
flagged HIGH CONFIDENCE — they represent stable submerged features, not
transient surface noise.

Usage:
    python crossref_scans.py
    python crossref_scans.py outputs/file1.json outputs/file2.json
"""
import json
import sys
import math
import glob
from pathlib import Path
from collections import defaultdict

MATCH_RADIUS_DEG = 0.004  # ~444m (~500 yards) — tightened to suppress false proximity matches
HI_CONF_THRESHOLD = 2     # seen in >= N scan epochs = HIGH CONFIDENCE

REPO = Path(__file__).parent

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

STRAITS_BBOX = (45.70, -84.80, 46.05, -84.10)   # (lat_min, lon_min, lat_max, lon_max)

def load_scan(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    tag = data.get('scan_tag', Path(path).stem)
    dets = data.get('detections', [])
    # Filter to primary Straits bbox (some older scans lacked spatial filter)
    lat_min, lon_min, lat_max, lon_max = STRAITS_BBOX
    dets = [d for d in dets
            if lat_min <= d['lat'] <= lat_max and lon_min <= d['lon'] <= lon_max]
    # Exclude extreme z-scores that indicate NODATA/ice fill artifacts (|z| > 8)
    dets = [d for d in dets if abs(d.get('zscore', 0)) <= 8.0]
    print(f"  {tag}: {len(dets)} detections (within Straits bbox, z-filtered)")
    return tag, dets

def cluster_detections(dets, radius_deg=0.01):
    """Merge pixels within radius_deg into site clusters (centroid + max z)."""
    clusters = []
    for d in sorted(dets, key=lambda x: abs(x['zscore']), reverse=True):
        lat, lon = d['lat'], d['lon']
        merged = False
        for c in clusters:
            if abs(lat - c['lat']) < radius_deg and abs(lon - c['lon']) < radius_deg:
                c['count'] += 1
                if abs(d['zscore']) > abs(c['zscore']):
                    c['zscore'] = d['zscore']
                    c['type'] = d['type']
                c['types'].add(d['type'])
                c['lat'] = (c['lat'] * (c['count'] - 1) + lat) / c['count']  # running mean
                c['lon'] = (c['lon'] * (c['count'] - 1) + lon) / c['count']
                merged = True
                break
        if not merged:
            clusters.append({
                'lat': lat, 'lon': lon, 'zscore': d['zscore'],
                'type': d['type'], 'types': {d['type']},
                'count': 1,
                'known_wreck_name': d.get('known_wreck_name'),
                'line5_candidate': d.get('line5_candidate', False),
            })
    return clusters

def crossref(scan_groups):
    """Find clusters that appear across multiple scan epochs."""
    persistent = []
    all_clusters = {}
    for tag, dets in scan_groups.items():
        all_clusters[tag] = cluster_detections(dets)
        print(f"  {tag}: {len(all_clusters[tag])} clusters after merging")

    tags = list(scan_groups.keys())
    if len(tags) < 2:
        print("\n[!] Only 1 data era — no cross-reference possible; reporting single-era clusters.")
        single = []
        for tag, clusters in all_clusters.items():
            for c in clusters:
                c['epoch_hits'] = [tag]
                c['max_abs_z'] = abs(c['zscore'])
                if abs(c['zscore']) > 3.5:
                    c['confidence'] = 'MEDIUM'
                else:
                    c['confidence'] = 'LOW'
                single.append(c)
        return sorted(single, key=lambda x: (x['confidence'] != 'MEDIUM', -x['max_abs_z']))

    base_tag = tags[0]
    other_tags = tags[1:]
    results = []
    for c in all_clusters[base_tag]:
        hits = [base_tag]
        best_match_z = abs(c['zscore'])
        for ot in other_tags:
            for c2 in all_clusters[ot]:
                dist = haversine_km(c['lat'], c['lon'], c2['lat'], c2['lon'])
                if dist < MATCH_RADIUS_DEG * 111:  # convert deg to km approx
                    hits.append(ot)
                    best_match_z = max(best_match_z, abs(c2['zscore']))
                    c['types'].update(c2['types'])
                    break
        c['epoch_hits'] = hits
        c['max_abs_z'] = best_match_z
        if len(hits) >= HI_CONF_THRESHOLD:
            c['confidence'] = 'HIGH'
        elif abs(c['zscore']) > 3.0:
            c['confidence'] = 'MEDIUM'
        else:
            c['confidence'] = 'LOW'
        results.append(c)

    return sorted(results, key=lambda x: (x['confidence'] != 'HIGH', -x.get('max_abs_z', 0)))

def main():
    if len(sys.argv) > 2:
        files = sys.argv[1:]
    else:
        # Default: only use straits_mackinac scan outputs (not legacy lake_michigan_ scans)
        files = sorted(glob.glob(str(REPO / 'outputs' / 'straits_mackinac_*_scan_*.json')))

    if not files:
        print("No scan JSON files found.")
        sys.exit(1)

    print(f"Loading {len(files)} scan file(s):")
    # Group scans by DATA ERA (part of scan_tag before _scan_ timestamp).
    # Multiple runs of the same era are merged into one epoch so that
    # HIGH confidence requires DIFFERENT year ranges, not just different runs.
    era_dets: dict[str, list] = defaultdict(list)
    for f in files:
        try:
            tag, dets = load_scan(f)
            # era = everything before "_scan_" suffix, e.g. "straits_mackinac_2015_2016"
            era = tag.split('_scan_')[0] if '_scan_' in tag else tag
            era_dets[era].extend(dets)
        except Exception as e:
            print(f"  [error] {f}: {e}")

    scan_groups = dict(era_dets)
    print(f"\n  Grouped into {len(scan_groups)} data era(s): {list(scan_groups.keys())}")

    print()
    print("Clustering and cross-referencing...")
    results = crossref(scan_groups)

    print()
    print("="*80)
    print("CROSS-REFERENCE REPORT — STRAITS OF MACKINAC")
    print("="*80)

    hi = [r for r in results if r['confidence'] == 'HIGH']
    med = [r for r in results if r['confidence'] == 'MEDIUM']
    lo = [r for r in results if r['confidence'] == 'LOW']
    print(f"\n  HIGH confidence (multi-epoch persistent):  {len(hi)}")
    print(f"  MEDIUM confidence (strong single-epoch):   {len(med)}")
    print(f"  LOW confidence (weak single-epoch):        {len(lo)}")

    if hi:
        print("\n--- HIGH CONFIDENCE ANOMALIES ---")
        for r in hi:
            kw = f" | KW:{r['known_wreck_name']}" if r.get('known_wreck_name') else ""
            l5 = " | LINE5" if r.get('line5_candidate') else ""
            types_str = '+'.join(sorted(r['types']))
            print(f"  lat={r['lat']:.4f} lon={r['lon']:.4f}  z={r['zscore']:.2f}  "
                  f"epochs={r['epoch_hits']}  sensors={types_str}{kw}{l5}")

    if med:
        print("\n--- MEDIUM CONFIDENCE ANOMALIES ---")
        for r in med[:20]:
            kw = f" | KW:{r['known_wreck_name']}" if r.get('known_wreck_name') else ""
            l5 = " | LINE5" if r.get('line5_candidate') else ""
            types_str = '+'.join(sorted(r['types']))
            print(f"  lat={r['lat']:.4f} lon={r['lon']:.4f}  z={r['zscore']:.2f}  "
                  f"sensor={types_str}{kw}{l5}")

    # Write JSON summary
    out_path = REPO / 'outputs' / 'crossref_report.json'
    report = {
        'scan_files': files,
        'epochs': list(scan_groups.keys()),
        'high_confidence': [r for r in results if r['confidence'] == 'HIGH'],
        'medium_confidence': [r for r in results if r['confidence'] == 'MEDIUM'],
        'all_results': results,
    }
    # Make sets JSON-serializable
    for item in report['all_results'] + report['high_confidence'] + report['medium_confidence']:
        item['types'] = sorted(item['types'])
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Cross-reference report saved: {out_path}")

if __name__ == '__main__':
    main()
