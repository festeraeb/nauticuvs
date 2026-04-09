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

bbox = "-87.9,45.78,-84.6,46.0"
temporal = "2015-01-01T00:00:00Z/2016-12-31T23:59:59Z"

all_granules = []
for prod in ["HLSL30", "HLSS30"]:
    r = session.get("https://cmr.earthdata.nasa.gov/search/granules.json",
        params={"short_name": prod, "bounding_box": bbox, "temporal": temporal, "page_size": 2000, "sort_key": "-start_date"})
    if r.status_code == 200:
        entries = r.json().get("feed", {}).get("entry", [])
        print(prod + ": " + str(len(entries)) + " granules")
        for e in entries:
            title = e.get("title", "")
            date = e.get("time_start", "")[:10]
            tile = title.split(".")[2] if "." in title else "?"
            links = [l for l in e.get("links", []) if l.get("href", "").endswith(".tif")]
            all_granules.append({"product": prod, "title": title, "date": date, "tile": tile, "links": links})

# Group by date
by_date = defaultdict(list)
for g in all_granules:
    by_date[g["date"]].append(g)

# Pick latest date per month Mar-Dec for 2015-2016
selected_dates = []
for year in ["2015", "2016"]:
    for month in range(3, 13):
        mk = year + "-" + str(month).zfill(2)
        matching = [d for d in by_date.keys() if d.startswith(mk)]
        if matching:
            selected_dates.append(sorted(matching)[-1])

print("\nSelected " + str(len(selected_dates)) + " dates:")
for d in selected_dates:
    tiles = set(g["tile"] for g in by_date[d])
    print("  " + d + ": " + " ".join(sorted(tiles)))

# Key bands for glint: B08(NIR/glint), B04(Red), B03(Green/water), B10/B11(thermal), Fmask
key_bands = ["B03.tif", "B04.tif", "B08.tif", "B10.tif", "B11.tif", "Fmask.tif"]

to_download = []
for date in selected_dates:
    to_download.extend(by_date[date])

print("\nDownloading " + str(len(to_download)) + " granules (key bands only)...")

for i, g in enumerate(to_download):
    gdir = output_dir / g["title"]
    gdir.mkdir(exist_ok=True)
    print("\n[" + str(i+1) + "/" + str(len(to_download)) + "] " + g["title"] + " " + g["date"])
    
    for link in g["links"]:
        band = link["href"].split("/")[-1]
        if band not in key_bands:
            continue
        dest = gdir / band
        if dest.exists() and dest.stat().st_size > 0:
            print("  [skip] " + band)
            continue
        print("  " + band + "...", end=" ", flush=True)
        try:
            r = session.get(link["href"], stream=True, timeout=300)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1<<20):
                        f.write(chunk)
                sz = dest.stat().st_size / 1024 / 1024
                print(str(round(sz, 1)) + "MB")
            else:
                print("HTTP " + str(r.status_code))
        except Exception as e:
            print("ERR: " + str(e)[:60])

total = sum(os.path.getsize(os.path.join(r, f)) for r, d, fs in os.walk(output_dir) for f in fs)
print("\n=== DOWNLOADED: " + str(round(total/1024/1024, 1)) + "MB ===")
