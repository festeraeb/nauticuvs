#!/usr/bin/env python3
import requests, os
from pathlib import Path
from collections import defaultdict

REPO = Path.home() / "cesarops-core"
output_dir = REPO / "downloads" / "hls" / "straits_2015_2016"
output_dir.mkdir(parents=True, exist_ok=True)

env = {}
for line in Path(REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

token = env.get("EARTHDATA_TOKEN", "")
print("Token loaded:", len(token), "chars")

session = requests.Session()
session.headers.update({
    "Authorization": "Bearer " + token,
    "Accept": "application/json",
    "User-Agent": "CESAROPS/1.0"
})

bbox = "-87.9,45.78,-84.6,46.0"
temporal = "2015-01-01T00:00:00Z/2016-12-31T23:59:59Z"

all_granules = []
for prod in ["HLSL30", "HLSS30"]:
    params = {
        "short_name": prod,
        "bounding_box": bbox,
        "temporal": temporal,
        "page_size": 2000,
        "sort_key": "-start_date",
    }
    r = session.get("https://cmr.earthdata.nasa.gov/search/granules.json", params=params)
    if r.status_code == 200:
        d = r.json()
        entries = d.get("feed", {}).get("entry", [])
        print(prod + ": " + str(len(entries)) + " granules")

        dates = defaultdict(set)
        for e in entries:
            title = e.get("title", "")
            date = e.get("time_start", "")[:10]
            tile = title.split(".")[2] if "." in title else "?"
            dates[date].add(tile)

            links = [l for l in e.get("links", []) if l.get("href", "").endswith(".tif")]
            all_granules.append({
                "product": prod, "title": title, "date": date,
                "tile": tile, "links": links
            })

        print("  Date coverage:")
        for date in sorted(dates.keys())[:8]:
            tiles = sorted(dates[date])
            print("    " + date + ": " + " ".join(tiles))
        if len(dates) > 8:
            print("    ... +" + str(len(dates)-8) + " more dates")

key_bands = ["B02.tif","B03.tif","B04.tif","B05.tif","B06.tif","B07.tif","B10.tif","B11.tif","Fmask.tif"]
to_download = sorted(all_granules, key=lambda x: x["date"], reverse=True)[:20]

print("\nDownloading " + str(len(to_download)) + " granules (key bands only)...")

for i, g in enumerate(to_download):
    print("\n[" + str(i+1) + "/" + str(len(to_download)) + "] " + g["title"] + " (" + g["date"] + ")")
    granule_dir = output_dir / g["title"]
    granule_dir.mkdir(exist_ok=True)

    for link in g["links"]:
        band = link["href"].split("/")[-1]
        if band not in key_bands:
            continue
        dest = granule_dir / band
        if dest.exists() and dest.stat().st_size > 0:
            print("  [skip] " + band)
            continue
        print("  Downloading " + band + "...", end=" ", flush=True)
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
            print("Error: " + str(e))

total = sum(os.path.getsize(os.path.join(r, f)) for r, d, fs in os.walk(output_dir) for f in fs)
print("\n=== DOWNLOADED: " + str(round(total/1024/1024)) + "MB ===")
