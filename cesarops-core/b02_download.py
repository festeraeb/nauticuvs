import requests, os
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent
output_dir = REPO / "downloads" / "hls" / "straits_2015_2016"

env = {}
for line in Path(REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

token = env.get("EARTHDATA_TOKEN", "")
session = requests.Session()
session.headers.update({"Authorization": "Bearer " + token, "Accept": "application/json"})

# Get all granule directories
granule_dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()])
print(f"Found {len(granule_dirs)} granule directories")

downloaded_bytes = 0
skipped = 0

for i, gdir in enumerate(granule_dirs):
    # Read the title from the existing files
    existing_files = list(gdir.glob("*.tif"))
    if not existing_files:
        continue
    # Extract title from filename
    title = existing_files[0].name.split(".Fmask")[0].split(".B")[0]
    if title.endswith(".v2.0"):
        pass  # Already correct
    else:
        title = gdir.name
    
    # Check if B02 already exists
    b02_dest = gdir / (title + ".B02.tif")
    if b02_dest.exists() and b02_dest.stat().st_size > 0:
        skipped += 1
        continue
    
    # Search CMR for this specific granule to get B02 link
    r = session.get("https://cmr.earthdata.nasa.gov/search/granules.json",
        params={"short_name": "HLSL30" if "L30" in title else "HLSS30",
                "page_size": 1, "granule_ur": title})
    
    if r.status_code != 200:
        continue
    
    d = r.json()
    entries = d.get("feed", {}).get("entry", [])
    if not entries:
        continue
    
    e = entries[0]
    b02_link = None
    for link in e.get("links", []):
        href = link.get("href", "")
        if "B02.tif" in href and "data#" in link.get("rel", ""):
            b02_link = href
            break
    
    if not b02_link:
        continue
    
    # Download B02
    print(f"[{i+1}/{len(granule_dirs)}] {title} B02...", end=" ", flush=True)
    try:
        r2 = session.get(b02_link, stream=True, timeout=300)
        if r2.status_code == 200:
            with open(b02_dest, "wb") as f:
                for chunk in r2.iter_content(chunk_size=1<<20):
                    f.write(chunk)
            sz = b02_dest.stat().st_size
            downloaded_bytes += sz
            print(f"{sz/1024/1024:.1f}MB")
        else:
            print(f"HTTP {r2.status_code}")
    except Exception as e:
        print(f"ERR: {str(e)[:50]}")

print(f"\n=== B02 DOWNLOAD COMPLETE ===")
print(f"  Downloaded: {round(downloaded_bytes/1024/1024, 1)}MB")
print(f"  Skipped (already exists): {skipped}")
