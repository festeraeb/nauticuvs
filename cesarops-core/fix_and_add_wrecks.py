"""
Fix known_wrecks.json (trailing fragment), add all Straits dive-community wrecks,
then cross-reference against crossref_report.json anomalies with haversine distance.
"""
import json, math

def dms(deg, dec_min):
    return deg + dec_min / 60.0

def bbox(lat, lon, r=0.015):
    return lat-r, lat+r, lon-r, lon+r

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2-lat1)
    dlon = math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

# ── 1. Load and fix malformed JSON (trailing fragment) ───────────────────────
with open('known_wrecks.json', 'r') as f:
    content = f.read()

decoder = json.JSONDecoder()
obj1, end1 = decoder.raw_decode(content.strip())
remainder = content.strip()[end1:].strip()
if remainder:
    try:
        extra = json.loads('{' + remainder + '}')
        before = len(obj1['wrecks'])
        for k, v in extra.items():
            if k not in obj1['wrecks']:
                obj1['wrecks'][k] = v
        print("Merged", len(obj1['wrecks'])-before, "from trailing fragment")
    except json.JSONDecodeError as e:
        print("Fragment parse failed:", e)

print("Wrecks before additions:", len(obj1['wrecks']))

# ── 2. All Straits of Mackinac wrecks (dive community GPS) ──────────────────
# (key, name, lat_dd, lon_dd, d_min, d_max, year, type, length, confidence, gps_status, desc)
straits = [
    ("cayuga",        "Cayuga",           dms(45,43.239), -dms(85,11.401), 75,  102, None, "freighter",            None, "high",   "published",              "Straits of Mackinac, western"),
    ("cedarville",    "Cedarville",        dms(45,47.235), -dms(84,40.248), 40,  110, 1965, "steel_freighter",      None, "high",   "published",              "SE of Mackinac Bridge; stern 45d47.325N/84d40.321W bow 45d47.247N/84d40.272W"),
    ("eber_ward",     "Eber Ward",         dms(45,48.763), -dms(84,49.133),111,  145, 1909, "wooden_bulk_freighter",None, "high",   "published_tried",        "West of bridge mid-channel; alt 45d48.772N/84d49.142W"),
    ("fred_mcbrier",  "Fred McBrier",      dms(45,48.342), -dms(85,55.301), 96,  104, None, "freighter",            None, "high",   "published",              "Western Straits approach"),
    ("maitland",      "Maitland",          dms(45,48.249), -dms(85,52.555), 85,   85, None, "wooden_bark",          137,  "medium", "published_untried",      "West of bridge mid-channel; alt 45d48.20N/84d52.29W or 45d48.06N/84d42.09W"),
    ("minneapolis",   "Minneapolis",       dms(45,48.511), -dms(84,43.904),124,  124, None, "freighter",            None, "high",   "published",              "East of Mackinac Bridge"),
    ("newell_eddy",   "Newell Eddy",       dms(45,46.890), -dms(84,13.810),165,  165, None, "freighter",            None, "high",   "published",              "Eastern Straits near Bois Blanc Island"),
    ("northwest",     "Northwest",         dms(45,47.450), -dms(84,51.465), 75,   75, None, "vessel",               None, "high",   "published",              "West of bridge mid-channel"),
    ("rock_maze",     "Rock Maze",         dms(45,51.803), -dms(84,36.410),  0,   35, None, "reef_site",            None, "high",   "published",              "North Straits, shallow dive site"),
    ("sandusky",      "Sandusky",          dms(45,47.959), -dms(84,50.249), 70,   85, None, "schooner",             None, "medium", "published_tried_not_close","West of bridge mid-channel; alt 45d48.09N/84d50.08W"),
    ("m_stalker",     "M. Stalker",        dms(45,47.620), -dms(84,41.062), 85,   85, None, "vessel",               None, "high",   "published",              "SE of Mackinac Bridge"),
    ("st_andrew",     "St. Andrew",        dms(45,42.051), -dms(84,31.795), 62,   62, None, "vessel",               None, "high",   "published",              "SE Straits; formerly St. Andrew"),
    ("uganda",        "Uganda",            dms(45,50.553), -dms(85, 2.998),185,  207, None, "vessel",               None, "high",   "published",              "West of bridge, deep channel"),
    ("william_barnum","William H. Barnum", dms(45,44.708), -dms(84,37.866), 58,   75, None, "wooden_freighter",     218,  "medium", "published_untried",      "SE of bridge off Mackinaw City; alt 45d44.42N/84d37.53W"),
    ("william_young", "William Young",     dms(45,48.777), -dms(84,41.923),120,  120, None, "schooner",             None, "high",   "published",              "East of bridge off upper shore"),
]

added = updated = 0
for row in straits:
    key, name, lat, lon, d_min, d_max, year, wtype, length, conf, gps_status, desc = row
    lamin, lamax, lomin, lomax = bbox(lat, lon)
    entry = {
        "name": name, "category": ["confirmed_wreck"],
        "lat": round(lat,6), "lon": round(lon,6),
        "lat_min": round(lamin,6), "lat_max": round(lamax,6),
        "lon_min": round(lomin,6), "lon_max": round(lomax,6),
        "depth_ft": (d_min+d_max)//2,
        "depth_range": (str(d_min)+"-"+str(d_max)) if d_min!=d_max else str(d_min),
        "year_lost": year, "type": wtype, "length_ft": length,
        "confidence": conf, "gps_status": gps_status,
        "location_desc": desc, "lake": "michigan_huron_straits",
        "source": "dive_community", "sensors_tested": [], "probe_results": []
    }
    if key in obj1['wrecks']:
        obj1['wrecks'][key].update(entry); updated += 1
    else:
        obj1['wrecks'][key] = entry; added += 1

print("Added:", added, " Updated:", updated, " Total:", len(obj1['wrecks']))

# Write clean
with open('known_wrecks.json', 'w') as f:
    json.dump(obj1, f, indent=2)
print("known_wrecks.json saved cleanly.")

# ── 3. Cross-reference anomalies against ALL wrecks ─────────────────────────
all_wrecks = []
for k, v in obj1['wrecks'].items():
    if 'lat' in v:
        wlat, wlon = v['lat'], v['lon']
    elif 'lat_min' in v:
        wlat = (v['lat_min']+v['lat_max'])/2
        wlon = (v['lon_min']+v['lon_max'])/2
    else:
        continue
    all_wrecks.append((k, v.get('name',k), wlat, wlon,
                       v.get('depth_ft','?'), v.get('year_lost','?'),
                       v.get('type','?'), v.get('gps_status','?')))

with open('outputs/crossref_report.json') as f:
    report = json.load(f)

hits = report.get('high_confidence', []) + report.get('medium_confidence', [])

MATCH_KM  = 0.457   # 500 yards
NEARBY_KM = 1.500   # ~1 mile — show anything within 1 mile as "nearby"

print()
print("="*72)
print("ANOMALY vs KNOWN WRECK DISTANCE TABLE")
print("="*72)
print("{:<4} {:<9} {:<10} {:>6}  {:<22} {:>8}  {:<18} {}".format(
    "Rank","Conf","Coords","z","Wreck","Dist km","Type","GPS status"))
print("-"*100)

best_matches = []
for i, hit in enumerate(hits):
    alat, alon = hit['lat'], hit['lon']
    z = hit.get('max_abs_z', hit.get('zscore',0))
    conf = hit.get('confidence','?')
    label = ("H"+str(i+1)) if conf=="HIGH" else ("M"+str(i-12))

    candidates = sorted(all_wrecks, key=lambda w: haversine_m(alat,alon,w[2],w[3]))
    best = candidates[0]
    dist_m = haversine_m(alat, alon, best[2], best[3])
    dist_km = dist_m / 1000.0

    flag = ""
    if dist_km <= MATCH_KM:   flag = "<<< MATCH"
    elif dist_km <= NEARBY_KM: flag = "< nearby"

    coord_str = "{:.4f},{:.4f}".format(alat,alon)
    print("{:<4} {:<9} {:<21} {:>5.2f}  {:<22} {:>6.2f}km  {:<18} {}{}".format(
        label, conf, coord_str, z,
        best[1][:22], dist_km, str(best[6])[:18], best[7], "  "+flag if flag else ""))

    if dist_km <= NEARBY_KM:
        best_matches.append((label, alat, alon, z, conf, best, dist_km, flag))
        # show 2nd nearest if also nearby
        if len(candidates) > 1:
            b2 = candidates[1]
            d2 = haversine_m(alat,alon,b2[2],b2[3])/1000
            if d2 <= NEARBY_KM:
                print("     also near: {:} ({:.2f}km)".format(b2[1], d2))

print()
print("="*72)
print("SUMMARY - anomalies within", NEARBY_KM, "km of a known wreck:")
print("="*72)
for label,alat,alon,z,conf,best,dist_km,flag in best_matches:
    k,name,wlat,wlon,depth,year,wtype,gps = best
    print("  {} {:.4f},{:.4f} z={:.2f}  ->  {} ({}ft, {}) {:.2f}km  {}".format(
        label,alat,alon,z,name,depth,year,dist_km,flag))

# ── 4. Wrecks inside scan bbox ───────────────────────────────────────────────
BBOX = (45.70, -84.80, 46.05, -84.10)
print()
print("Wrecks inside Straits scan bbox", BBOX)
print("-"*60)
for k,name,wlat,wlon,depth,year,wtype,gps in sorted(all_wrecks, key=lambda x:x[2]):
    if BBOX[0]<=wlat<=BBOX[2] and BBOX[1]<=wlon<=BBOX[3]:
        print("  {:<24} {:.5f}N {:.5f}W  {}ft  {}".format(name,wlat,wlon,depth,gps))

