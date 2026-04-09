import requests, os
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent
output_dir = REPO / "downloads" / "hls" / "straits_2015_2016"
output_dir.mkdir(parents=True, exist_ok=True)

env = {}
for line in Path(REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

token = env.get("EARTHDATA_TOKEN", "")
session = requests.Session()
session.headers.update({
    "Authorization": "Bearer " + token,
    "Accept": "application/json",
    "User-Agent": "CESAROPS/1.0"
})

bbox = "-87.9,45.78,-84.6,46.0"
temporal = "2015-01-01T00:00:00Z/2016-12-31T23:59:59Z"

# Get granule metadata to build S3 URLs
all_granules = []
for prod, version in [("HLSL30", "HLSL30.020"), ("HLSS30", "HLSS30.020")]:
    r = session.get("https://cmr.earthdata.nasa.gov/search/granules.json",
        params={"short_name": prod, "bounding_box": bbox, "temporal": temporal,
                "page_size": 2000, "sort_key": "-start_date"})
    if r.status_code == 200:
        entries = r.json().get("feed", {}).get("entry", [])
        print(prod + ": " + str(len(entries)) + " granules")
        for e in entries:
            title = e.get("title", "")
            date = e.get("time_start", "")[:10]
            tile = title.split(".")[2] if "." in title else "?"
            # Check if granule has links with type data
            has_tif = any(l.get("href", "").endswith(".tif") for l in e.get("links", []))
            all_granules.append({
                "product": prod, "version": version, "title": title,
                "date": date, "tile": tile, "has_tif_links": has_tif,
                "links": e.get("links", [])
            })

# Group by date
by_date = defaultdict(list)
for g in all_granules:
    by_date[g["date"]].append(g)

# Select one date per month Mar-Dec for 2015-2016
selected_dates = []
for year in ["2015", "2016"]:
    for month in range(3, 13):
        mk = year + "-" + str(month).zfill(2)
        matching = [d for d in by_date.keys() if d.startswith(mk)]
        if matching:
            selected_dates.append(sorted(matching)[-1])

print("\nSelected " + str(len(selected_dates)) + " dates")
for d in selected_dates:
    tiles = set(g["tile"] for g in by_date[d])
    print("  " + d + ": " + " ".join(sorted(tiles)))

# Build S3 URLs
# Format: https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/VERSION/TILE/TILE.BAND.tif
key_bands = ["B03.tif", "B04.tif", "B08.tif", "B10.tif", "B11.tif", "Fmask.tif"]

to_download = []
for date in selected_dates:
    to_download.extend(by_date[date])

print("\nDownloading " + str(len(to_download)) + " granules...")
downloaded_bytes = 0

for i, g in enumerate(to_download):
    gdir = output_dir / g["title"]
    gdir.mkdir(exist_ok=True)
    tile = g["title"]
    base = tile.replace(".", "/")
    version = g["version"]
    
    print("\n[" + str(i+1) + "/" + str(len(to_download)) + "] " + tile + " (" + g["date"] + ")")
    
    for band in key_bands:
        band_name = band.replace(".tif", "")
        # Try S3 direct URL
        s3_url = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/" + version + "/" + base + "/" + tile + "." + band_name + ".tif"
        dest = gdir / (tile + "." + band_name + ".tif")
        
        if dest.exists() and dest.stat().st_size > 0:
            print("  [skip] " + band)
            continue
        
        print("  " + band + "...", end=" ", flush=True)
        try:
            r = session.get(s3_url, stream=True, timeout=300)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1<<20):
                        f.write(chunk)
                sz = dest.stat().st_size
                downloaded_bytes += sz
                print(str(round(sz/1024/1024, 1)) + "MB")
            elif r.status_code == 403 or r.status_code == 401:
                # Try CMR link directly
                cmr_link = None
                for link in g.get("links", []):
                    href = link.get("href", "")
                    if band in href or band_name + ".tif" in href:
                        cmr_link = href
                        break
                if cmr_link:
                    print("trying CMR...", end=" ", flush=True)
                    r2 = session.get(cmr_link, stream=True, timeout=300)
                    if r2.status_code == 200:
                        with open(dest, "wb") as f:
                            for chunk in r2.iter_content(chunk_size=1<<20):
                                f.write(chunk)
                        sz = dest.stat().st_size
                        downloaded_bytes += sz
                        print(str(round(sz/1024/1024, 1)) + "MB")
                    else:
                        print("HTTP " + str(r2.status_code))
                else:
                    print("HTTP " + str(r.status_code))
            else:
                print("HTTP " + str(r.status_code))
        except Exception as e:
            print("ERR: " + str(e)[:60])

print("\n=== DOWNLOADED: " + str(round(downloaded_bytes/1024/1024, 1)) + "MB ===")

# Summary
for root, dirs, files in os.walk(output_dir):
    for f in files:
        path = os.path.join(root, f)
        sz = os.path.getsize(path) / 1024 / 1024
        print("  " + os.path.relpath(path, output_dir) + " (" + str(round(sz, 1)) + "MB)")
