"""Match crossref anomalies against known_wrecks.json by haversine distance."""
import json, math

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

with open('outputs/crossref_report.json') as f:
    report = json.load(f)

with open('known_wrecks.json') as f:
    raw_text = f.read()
# File may have trailing frag after first valid JSON object — decode only the first
decoder = json.JSONDecoder()
kw_raw, _ = decoder.raw_decode(raw_text.strip())

# Build flat wreck list from both dict formats
all_wrecks = []
src = kw_raw.get('wrecks', kw_raw)
for k, v in src.items():
    if not isinstance(v, dict):
        continue
    if 'lat_min' in v and 'lat_max' in v:
        clat = (v['lat_min'] + v['lat_max']) / 2
        clon = (v['lon_min'] + v['lon_max']) / 2
    elif 'lat' in v and 'lon' in v:
        clat, clon = v['lat'], v['lon']
    else:
        continue
    all_wrecks.append({
        'id': k,
        'name': v.get('name', k),        'lat': clat,
        'lon': clon,
        'depth': v.get('depth_ft', '?'),
        'year': v.get('year_lost', '?'),
        'type': v.get('type', '?'),
        'category': v.get('category', [])
    })

print(f"Loaded {len(all_wrecks)} wrecks from database")
print()

def find_nearest(lat, lon, n=3):
    dists = []
    for w in all_wrecks:
        d = haversine_m(lat, lon, w['lat'], w['lon'])
        dists.append((d, w))
    return sorted(dists, key=lambda x: x[0])[:n]

MATCH_M = 457     # 500 yards — tight confirmed match
NEAR_M  = 1500    # ~1 mile — show as nearby

print("=" * 72)
print("HIGH CONFIDENCE ANOMALIES vs KNOWN WRECKS")
print("=" * 72)

matches_found = []

for i, hit in enumerate(report['high_confidence']):
    lat = hit['lat']
    lon = hit['lon']
    z = hit.get('max_abs_z', hit.get('zscore', 0))
    types = hit.get('types', [hit.get('type', '')])
    epoch_hits = hit.get('epoch_hits', {})
    if isinstance(epoch_hits, list):
        epoch_list = epoch_hits
    else:
        epoch_list = list(epoch_hits.keys())

    nearest = find_nearest(lat, lon, n=3)
    best_d, best_w = nearest[0]

    if best_d < MATCH_M:
        status = "*** POSSIBLE MATCH ***"
    elif best_d < NEAR_M:
        status = "-- NEARBY --"
    else:
        status = "(no close wreck)"

    # Also check if point falls inside any wreck search bbox
    bbox_hit = None
    for w in all_wrecks:
        raw = kw_raw.get('wrecks', kw_raw).get(w['id'], {})
        if all(k in raw for k in ('lat_min','lat_max','lon_min','lon_max')):
            if raw['lat_min'] <= lat <= raw['lat_max'] and raw['lon_min'] <= lon <= raw['lon_max']:
                bbox_hit = w
                break

    print(f"\n[{i+1:2}] {lat:.4f}N, {lon:.4f}W  |  z={z:.2f}  |  types={types}")
    print(f"      epochs: {epoch_list}")
    if bbox_hit:
        print(f"      !! INSIDE SEARCH BBOX: {bbox_hit['name']}  ({bbox_hit['year']}, {bbox_hit['depth']}ft, {bbox_hit['type']})  !!")
        status = "*** INSIDE SEARCH ZONE ***"
        if (i+1, lat, lon, z, bbox_hit, 0) not in [(m[0],m[1],m[2],m[3],m[4],m[5]) for m in matches_found]:
            matches_found.append((i+1, lat, lon, z, bbox_hit, 0.0))
    print(f"      nearest: {best_w['name']}  ({best_w['year']}, {best_w['depth']}ft, {best_w['type']})")
    print(f"      distance: {best_d/1000:.2f} km  {status}")

    if best_d < MATCH_M and not bbox_hit:
        matches_found.append((i+1, lat, lon, z, best_w, best_d))
        for d2, w2 in nearest[1:]:
            if d2 < NEAR_M:
                print(f"      also near: {w2['name']} ({w2['year']}, {w2['depth']}ft) -- {d2/1000:.2f} km")

print()
print("=" * 72)
print(f"SUMMARY: {len(matches_found)} of 13 HIGH-CONF anomalies within 5 km of a known wreck")
print("=" * 72)
for rank, lat, lon, z, w, dist in matches_found:
    cats = ','.join(w['category']) if isinstance(w['category'], list) else w['category']
    print(f"  [{rank}] {lat:.4f},{lon:.4f}  z={z:.2f}  <->  {w['name']}  ({w['year']}, {w['depth']}ft)  {dist/1000:.2f} km  [{cats}]")

print()
print("=" * 72)
print("MEDIUM CONFIDENCE ANOMALIES vs KNOWN WRECKS")
print("=" * 72)
for i, hit in enumerate(report.get('medium_confidence', [])):
    lat = hit['lat']
    lon = hit['lon']
    z = hit.get('max_abs_z', hit.get('zscore', 0))
    nearest = find_nearest(lat, lon, n=1)
    best_d, best_w = nearest[0]
    status = "POSSIBLE MATCH" if best_d < MATCH_M else ("NEARBY" if best_d < NEAR_M else "")
    print(f"  [M{i+1}] {lat:.4f},{lon:.4f}  z={z:.2f}  |  {best_w['name']} ({best_w['year']}, {best_w['depth']}ft) -- {best_d/1000:.2f} km  {status}")
