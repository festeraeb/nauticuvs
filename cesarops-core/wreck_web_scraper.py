"""
wreck_web_scraper.py  —  Scrape verified shipwreck GPS from NOAA Thunder Bay and
Michigan Underwater Preserve Wikipedia pages.

Sources:
  - https://thunderbay.noaa.gov/shipwrecks/           (wreck index + individual pages)
  - https://en.wikipedia.org/wiki/Straits_of_Mackinac_Shipwreck_Preserve
  - https://en.wikipedia.org/wiki/Michigan_Underwater_Preserves  (crawls all sub-pages)

Output:
  outputs/web_scraped_wrecks.json   — deduplicated list with coord_quality flags
  outputs/web_scraped_wrecks.csv    — same, as CSV for easy inspection

Usage:
  python wreck_web_scraper.py
  python wreck_web_scraper.py --merge   # also merge into known_wrecks.json
"""

import re
import json
import time
import math
import argparse
import csv
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("Install deps first:  pip install requests beautifulsoup4")

# ── Config ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WreckScraper/1.0; "
        "research project; +https://github.com/cesarops)"
    )
}
REQUEST_DELAY = 1.2   # seconds between requests — be polite

THUNDERBAY_INDEX = "https://thunderbay.noaa.gov/shipwrecks/"
THUNDERBAY_BASE  = "https://thunderbay.noaa.gov"

# All Wikipedia Michigan Underwater Preserve pages
WIKI_PRESERVE_PAGES = [
    "https://en.wikipedia.org/wiki/Straits_of_Mackinac_Shipwreck_Preserve",
    "https://en.wikipedia.org/wiki/Whitefish_Point_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Keweenaw_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Alger_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/De_Tour_Passage_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Grand_Traverse_Bay_Bottomland_Preserve",
    "https://en.wikipedia.org/wiki/Manitou_Passage_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Marquette_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Sanilac_Shores_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Southwest_Michigan_Underwater_Preserve",
    "https://en.wikipedia.org/wiki/Thumb_Area_Bottomland_Preserve",
    "https://en.wikipedia.org/wiki/Thunder_Bay_National_Marine_Sanctuary",
]

OUTPUT_JSON = Path("outputs/web_scraped_wrecks.json")
OUTPUT_CSV  = Path("outputs/web_scraped_wrecks.csv")
KNOWN_FILE  = Path("known_wrecks.json")

# ── HTTP helpers ─────────────────────────────────────────────────────────────

_session = requests.Session()
_session.headers.update(HEADERS)

def fetch(url: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=20)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  [WARN] {url} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None

def polite_get(url: str) -> str | None:
    time.sleep(REQUEST_DELAY)
    return fetch(url)

# ── DMS coordinate parsers ────────────────────────────────────────────────────

def dms_to_dd(deg: float, minutes: float, seconds: float = 0.0) -> float:
    return deg + minutes / 60.0 + seconds / 3600.0


# Pattern A: "45°43.239′N 085°11.401′W"  (Wikipedia Straits / Whitefish style)
# Also handles Unicode prime ′ and ASCII apostrophe '
_PAT_A = re.compile(
    r"(\d{1,3})\xb0(\d{1,2}(?:\.\d+)?)[′']\s*N\s+"
    r"0?(\d{1,3})\xb0(\d{1,2}(?:\.\d+)?)[′']\s*W",
    re.IGNORECASE
)

# Pattern B: "N 47° 28.340 W 087° 51.880"  (Keweenaw style)
_PAT_B = re.compile(
    r"N\s+(\d{1,3})\xb0\s+(\d{1,2}(?:\.\d+)?)\s+W\s+0?(\d{1,3})\xb0\s+(\d{1,2}(?:\.\d+)?)",
    re.IGNORECASE
)

# Pattern C: "46°43.02′N 84°52.00′W" (no leading zero on lon, optional spaces)
_PAT_C = re.compile(
    r"(\d{1,3})°(\d{1,2}(?:\.\d+)?)['′]\s*N\s+"
    r"(\d{1,3})°(\d{1,2}(?:\.\d+)?)['′]\s*W",
    re.IGNORECASE
)

def parse_coords(text: str):
    """Return (lat_dd, lon_dd) or None.  text is a table cell or full row."""
    for pat in (_PAT_A, _PAT_B, _PAT_C):
        m = pat.search(text)
        if m:
            d1, m1, d2, m2 = (float(x) for x in m.groups())
            lat = dms_to_dd(d1, m1)
            lon = -dms_to_dd(d2, m2)   # West is negative
            if 40 <= lat <= 50 and -95 <= lon <= -75:   # Great Lakes sanity check
                return round(lat, 6), round(lon, 6)
    return None


# ── Depth parser ──────────────────────────────────────────────────────────────

_PAT_DEPTH = re.compile(r"(\d+)\s*(?:to\s*\d+)?\s*ft", re.IGNORECASE)
_PAT_DEPTH2 = re.compile(r"(\d+)'?\s*to\s*(\d+)'?\s*ft", re.IGNORECASE)

def parse_depth_ft(text: str) -> int | None:
    m = _PAT_DEPTH.search(text)
    return int(m.group(1)) if m else None


# ── Wikipedia preserve scraper ────────────────────────────────────────────────

def scrape_wikipedia_preserve(url: str) -> list[dict]:
    """Extract all wreck rows with coordinates from a Wikipedia preserve page."""
    print(f"\n[Wikipedia] {url}")
    html = polite_get(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    preserve_name = soup.find("h1").get_text(strip=True) if soup.find("h1") else "Unknown Preserve"

    results = []
    seen_names = set()

    # Look for tables that have lat/lon data
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Check if table has coordinate-looking content
        table_text = table.get_text()
        has_coords = (
            parse_coords(table_text) is not None or
            "°" in table_text
        )
        if not has_coords:
            continue

        print(f"  [table] found coordinate table with {len(rows)} rows")

        for row in rows[1:]:   # skip header
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            # Name is usually first cell
            name = cells[0].get_text(strip=True)
            if not name or len(name) < 2:
                continue

            # Skip header-like rows
            if name.lower() in ("wreck name", "site name", "name", "vessel"):
                continue

            # Join all cell text to search for coords
            row_text = " ".join(c.get_text(" ", strip=True) for c in cells)
            coords = parse_coords(row_text)
            if not coords:
                continue

            lat, lon = coords
            depth = None
            vessel_type = None

            # Try to find depth and type in cells
            for c in cells[1:]:
                ct = c.get_text(strip=True)
                d = parse_depth_ft(ct)
                if d and depth is None:
                    depth = d
                # Pick up vessel type (second or third cell, short text, no digits)
                if not re.search(r"\d", ct) and 3 < len(ct) < 60 and vessel_type is None:
                    vessel_type = ct

            # Deduplicate by name
            key = re.sub(r"\s+", " ", name.strip().upper())
            if key in seen_names:
                continue
            seen_names.add(key)

            entry = {
                "name": name.strip(),
                "lat": lat,
                "lon": lon,
                "depth_ft": depth,
                "vessel_type": vessel_type,
                "source": url,
                "preserve": preserve_name,
                "coord_quality": "preserve_registry",
            }
            results.append(entry)
            print(f"    {name:<40} lat={lat:.4f}  lon={lon:.4f}  depth={depth}")

    return results


# ── Thunder Bay NOAA scraper ──────────────────────────────────────────────────

def scrape_thunderbay_index() -> list[str]:
    """Return list of individual wreck page URLs from the Thunder Bay index."""
    print(f"\n[Thunder Bay] index: {THUNDERBAY_INDEX}")
    html = polite_get(THUNDERBAY_INDEX)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    wreck_urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/shipwrecks/" in href and href.endswith(".html"):
            full = urljoin(THUNDERBAY_BASE, href)
            if full not in wreck_urls:
                wreck_urls.append(full)

    print(f"  Found {len(wreck_urls)} wreck pages")
    return wreck_urls


def scrape_thunderbay_wreck(url: str) -> dict | None:
    """Scrape one Thunder Bay wreck page for name, depth, GPS, type."""
    html = polite_get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else Path(urlparse(url).path).stem

    # Extract structured facts from the page body
    body_text = soup.get_text(" ", strip=True)

    # GPS — some pages have it, most say TBA
    coords = parse_coords(body_text)

    # Try to pull GPS from "GPS Location:" line
    gps_line = re.search(
        r"GPS\s+Location[:\s]+([0-9°′′'.NSEW\s,]+?)(?:Depth|Wreck Length|\n|$)",
        body_text, re.IGNORECASE
    )
    if gps_line and coords is None:
        coords = parse_coords(gps_line.group(1))

    # Depth
    depth_match = re.search(r"Depth[:\s]+(\d+)\s*(?:to\s*(\d+))?\s*feet", body_text, re.IGNORECASE)
    depth_ft = int(depth_match.group(1)) if depth_match else None

    # Vessel type
    type_match = re.search(r"Vessel\s+Type[:\s]+([^\n.]{3,50})", body_text, re.IGNORECASE)
    vessel_type = type_match.group(1).strip() if type_match else None

    # Year wrecked
    year_match = re.search(r"Wrecked[:\s]+.*?(\d{4})", body_text, re.IGNORECASE)
    year_wrecked = int(year_match.group(1)) if year_match else None

    quality = "preserve_registry" if coords else "name_only"

    return {
        "name": name,
        "lat": coords[0] if coords else None,
        "lon": coords[1] if coords else None,
        "depth_ft": depth_ft,
        "vessel_type": vessel_type,
        "year_wrecked": year_wrecked,
        "lake": "Lake Huron",
        "source": url,
        "preserve": "Thunder Bay National Marine Sanctuary",
        "coord_quality": quality,
    }


def scrape_thunderbay_all() -> list[dict]:
    wreck_urls = scrape_thunderbay_index()
    results = []
    for url in wreck_urls:
        entry = scrape_thunderbay_wreck(url)
        if entry:
            results.append(entry)
            status = f"lat={entry['lat']:.4f}" if entry["lat"] else "GPS=TBA"
            print(f"  {entry['name']:<40} {status}  depth={entry['depth_ft']}")
    return results


# ── Deduplication ─────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Normalize for dedup: uppercase, strip punctuation/articles."""
    name = name.upper()
    name = re.sub(r"\b(SS|MV|THE|A|AN)\b", "", name)
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def deduplicate(records: list[dict]) -> list[dict]:
    """Keep best record per name (prefer ones with coords)."""
    by_name: dict[str, dict] = {}
    for r in records:
        key = normalize_name(r["name"])
        if key not in by_name:
            by_name[key] = r
        else:
            existing = by_name[key]
            # Prefer record with coords; if both have, prefer preserve_registry
            if existing["lat"] is None and r["lat"] is not None:
                by_name[key] = r
            elif r["lat"] is not None and existing.get("coord_quality") == "name_only":
                by_name[key] = r
    return list(by_name.values())


# ── Merge into known_wrecks.json ──────────────────────────────────────────────

def infer_lake(lat: float, lon: float) -> str:
    """Coarse lake inference from centroid coordinates."""
    if lat >= 46.4 and lon <= -84.4:
        return "superior"
    if lat >= 45.5 and -86.0 <= lon <= -83.5:
        return "huron"   # Straits / northern Huron
    if lon <= -84.5 and lat < 46.4:
        return "huron"
    if -87.5 <= lon <= -76.0 and lat < 45.5:
        return "huron"
    return "michigan"


def _slugify(name: str) -> str:
    """Create a filesystem/dict-safe slug from a wreck name."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:60]


def merge_into_known_wrecks(new_wrecks: list[dict], known_path: Path) -> int:
    """Add new wrecks to known_wrecks.json (v2 schema). Returns count added."""
    if known_path.exists():
        data = json.loads(known_path.read_text(encoding="utf-8"))
    else:
        data = {"_schema": "v2", "_categories": {}, "quick_searches": {}, "wrecks": {}}

    # Support flat-list legacy format
    if isinstance(data, list):
        data = {"_schema": "v2", "_categories": {}, "quick_searches": {}, "wrecks": {}}

    wrecks_dict: dict = data.setdefault("wrecks", {})
    existing_keys = {normalize_name(v.get("name", "")) for v in wrecks_dict.values()}

    added = 0
    for w in new_wrecks:
        if w["lat"] is None:
            continue
        key = normalize_name(w["name"])
        if key in existing_keys:
            continue

        slug = _slugify(w["name"])
        # Avoid slug collisions
        if slug in wrecks_dict:
            slug = f"{slug}_{added}"

        lake = w.get("lake") or infer_lake(w["lat"], w["lon"])

        entry = {
            "name": w["name"],
            "category": ["confirmed_wreck"],
            "lat": w["lat"],
            "lon": w["lon"],
            "depth_ft": w.get("depth_ft"),
            "lake": lake,
            "type": w.get("vessel_type"),
            "source": w.get("source"),
            "preserve": w.get("preserve"),
            "coord_quality": w.get("coord_quality", "preserve_registry"),
            "confidence": "confirmed",
            "sensors_tested": [],
            "probe_results": [],
        }
        wrecks_dict[slug] = entry
        existing_keys.add(key)
        added += 1

    known_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return added


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape Great Lakes wreck GPS data")
    parser.add_argument("--merge", action="store_true",
                        help="Merge GPS-verified results into known_wrecks.json")
    parser.add_argument("--no-thunderbay", action="store_true",
                        help="Skip Thunder Bay NOAA individual pages (faster)")
    args = parser.parse_args()

    all_wrecks: list[dict] = []

    # 1. Scrape all Wikipedia preserve pages
    print("=" * 60)
    print("PHASE 1: Wikipedia Michigan Underwater Preserve pages")
    print("=" * 60)
    for url in WIKI_PRESERVE_PAGES:
        wrecks = scrape_wikipedia_preserve(url)
        all_wrecks.extend(wrecks)

    # 2. Scrape Thunder Bay NOAA
    print("\n" + "=" * 60)
    print("PHASE 2: Thunder Bay NOAA wreck pages")
    print("=" * 60)
    if args.no_thunderbay:
        print("  [skipped]")
    else:
        tb_wrecks = scrape_thunderbay_all()
        all_wrecks.extend(tb_wrecks)

    # 3. Deduplicate
    print(f"\nTotal scraped (raw): {len(all_wrecks)}")
    all_wrecks = deduplicate(all_wrecks)
    print(f"After deduplication: {len(all_wrecks)}")

    with_gps = [w for w in all_wrecks if w["lat"] is not None]
    print(f"With GPS: {len(with_gps)}")

    # 4. Save outputs
    OUTPUT_JSON.parent.mkdir(exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(all_wrecks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nSaved → {OUTPUT_JSON}")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["name", "lat", "lon", "depth_ft", "vessel_type",
                      "year_wrecked", "lake", "preserve", "coord_quality", "source"]
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_wrecks)
    print(f"Saved → {OUTPUT_CSV}")

    # 5. Optional merge
    if args.merge:
        added = merge_into_known_wrecks(with_gps, KNOWN_FILE)
        print(f"\nMerged {added} new GPS-verified wrecks into {KNOWN_FILE}")

    # 6. Summary by preserve
    print("\n--- GPS-verified wrecks by preserve ---")
    by_preserve: dict[str, int] = {}
    for w in with_gps:
        p = w.get("preserve", "Unknown")
        by_preserve[p] = by_preserve.get(p, 0) + 1
    for p, cnt in sorted(by_preserve.items(), key=lambda x: -x[1]):
        print(f"  {p:<55} {cnt:>3} wrecks")
    print(f"\nTotal GPS-verified wrecks: {len(with_gps)}")


if __name__ == "__main__":
    main()
