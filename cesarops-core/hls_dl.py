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
session.headers.update({"Authorization": "Bearer " + token, "Accept": "application/json"})

key_bands = ["B03.tif", "B04.tif", "B08.tif", "B10.tif", "B11.tif", "Fmask.tif"]

# Get all granules with their CMR download links
all_granules = []
for prod in ["HLSL30", "HLSS30"]:
    r = session.get("https://cmr.earthdata.nasa.gov/search/granules.json",
        params={"short_name": prod, "bounding_box": "-87.9,45.78,-84.6,46.0",
                "temporal": "2015-01-01T00:00:00Z/2016-12-31T23:59:59Z",
                "page_size": 2000, "sort_key": "-start_date"})
    if r.status_code == 200:
        entries = r.json().get("feed", {}).get("entry", [])
        print(prod + ": " + str(len(entries)) + " granules")
        for e in entries:
            title = e.get("title", "")
            date_str = e.get("time_start", "")[:10]
            tile = title.split(".")[2]
            band_links = {}
            for link in e.get("links", []):
                href = link.get("href", "")
                if href.endswith(".tif"):
                    band = href.split("/")[-1]
                    if band in key_bands:
                        band_links[band] = href
            if band_links:
                all_granules.append({"title": title, "date": date_str, "tile": tile, "band_links": band_links})

# Group by year-month
by_month = defaultdict(list)
for g in all_granules:
    ym = g["date"][:7]  # "2015-03"
    by_month[ym].append(g)

# Pick latest granule per month for Mar-Dec 2015-2016
selected = []
for year in ["2015", "2016"]:
    for month in range(3, 13):
        ym = year + "-" + str(month).zfill(2)
        if ym in by_month:
            # Pick latest date in that month
            month_granules = by_month[ym]
            latest_date = max(g["date"] for g in month_granules)
            month_latest = [g for g in month_granules if g["date"] == latest_date]
            selected.extend(month_latest)
            print("  " + ym + ": " + str(len(month_latest)) + " tiles from " + latest_date)

print("\nSelected " + str(len(selected)) + " granules for download")

# Download
downloaded_bytes = 0
for i, g in enumerate(selected):
    gdir = output_dir / g["title"]
    gdir.mkdir(exist_ok=True)
    print("\n[" + str(i+1) + "/" + str(len(selected)) + "] " + g["title"] + " " + g["date"])
    
    for band, url in g["band_links"].items():
        dest = gdir / (g["title"] + "." + band.replace(".tif", "") + ".tif")
        if dest.exists() and dest.stat().st_size > 0:
            print("  [skip] " + band)
            continue
        print("  " + band + "...", end=" ", flush=True)
        try:
            r = session.get(url, stream=True, timeout=300)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1<<20):
                        f.write(chunk)
                sz = dest.stat().st_size
                downloaded_bytes += sz
                print(str(round(sz/1024/1024, 1)) + "MB")
            else:
                print("HTTP " + str(r.status_code))
        except Exception as e:
            print("ERR: " + str(e)[:60])

print("\n=== DOWNLOADED: " + str(round(downloaded_bytes/1024/1024, 1)) + "MB ===")
