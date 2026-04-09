#!/usr/bin/env python3
"""
CESAROPS Intelligent Search Planner

The agent uses this to:
1. Search ALL available data across CMR, Copernicus, AWS
2. Pick best days based on historical weather/wind/cloud data
3. Select optimal tools for target depth and conditions
4. Auto-download and queue scans

Usage:
    python smart_search_planner.py --target "Lumberman" --auto-plan
    python smart_search_planner.py --bbox 45.78,-84.8,46.1,-84.4 --dates 2015-2016 --auto-plan
    python smart_search_planner.py --target "Straits of Mackinac" --sensors all --auto-plan
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
import requests

REPO = Path(__file__).resolve().parent

# ── Known Wreck Targets ──────────────────────────────────────────────────────

KNOWN_TARGETS = {
    "lumberman": {
        "lat": 42.8476, "lon": -87.82946, "depth_ft": 50,
        "type": "steel_hull", "bbox_margin_deg": 0.05,
        "best_sensors": ["optical_blue_green", "sar_descending"],
        "notes": "50ft dive site. B02/B03 penetrate to this depth. B08 only sees surface boats."
    },
    "andaste": {
        "lat": 42.95, "lon": -86.45, "depth_ft": 450,
        "type": "steel_freighter", "bbox_margin_deg": 0.15,
        "best_sensors": ["thermal", "sar", "swot"],
        "notes": "Deep water (450ft). Thermal cold-sink + SAR volume scattering. Optical cannot penetrate."
    },
    "gilcher": {
        "lat": 45.90, "lon": -84.50, "depth_ft": 220,
        "type": "wooden_freighter", "bbox_margin_deg": 0.10,
        "best_sensors": ["thermal", "sar"],
        "notes": "220ft depth. Thermal + SAR primary. Optical limited."
    },
    "parnell": {
        "lat": 45.70, "lon": -85.50, "depth_ft": 180,
        "type": "freighter", "bbox_margin_deg": 0.10,
        "best_sensors": ["thermal", "sar"],
        "notes": "180ft depth. Good for triple-lock calibration."
    },
    "straits_of_mackinac": {
        "lat": 45.95, "lon": -84.72, "depth_ft": 100,
        "type": "area", "bbox": [45.78, -84.80, 46.10, -84.60],
        "best_sensors": ["thermal", "optical", "sar"],
        "notes": "Gilcher, Bridge Builder X. ~100ft avg depth."
    }
}

# ── Data Source Searchers ────────────────────────────────────────────────────

def load_earthdata_token():
    env_path = REPO / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("EARTHDATA_TOKEN="):
                _, _, val = line.partition("=")
                return val.strip()
    return ""

def search_hls_cmr(bbox, start_date, end_date, tiles=None, max_results=2000):
    """Search CMR for HLS granules covering the target area."""
    token = load_earthdata_token()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    })

    lat_min, lon_min, lat_max, lon_max = bbox
    results = {"HLSL30": [], "HLSS30": []}

    for prod in ["HLSL30", "HLSS30"]:
        r = session.get("https://cmr.earthdata.nasa.gov/search/granules.json",
            params={
                "short_name": prod,
                "bounding_box": f"{lon_min},{lat_min},{lon_max},{lat_max}",
                "temporal": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
                "page_size": max_results,
                "sort_key": "-start_date"
            })
        if r.status_code == 200:
            entries = r.json().get("feed", {}).get("entry", [])
            for e in entries:
                title = e.get("title", "")
                if tiles and not any(t in title for t in tiles):
                    continue
                date = e.get("time_start", "")[:10]
                tile = title.split(".")[2] if "." in title else "?"
                band_links = {}
                for link in e.get("links", []):
                    href = link.get("href", "")
                    if href.endswith(".tif") and "data#" in link.get("rel", ""):
                        band = href.split("/")[-1].split(".")[-2]
                        band_links[band] = href
                results[prod].append({
                    "title": title, "date": date, "tile": tile,
                    "bands": list(band_links.keys()), "links": band_links
                })

    return results

def search_copernicus_odata(bbox, start_date, end_date, max_results=50):
    """Search Copernicus Data Space for Sentinel-2 L2A products."""
    lat_min, lon_min, lat_max, lon_max = bbox
    url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

    # WKT polygon for bbox
    wkt = f"POLYGON(({lon_min} {lat_min},{lon_max} {lat_min},{lon_max} {lat_max},{lon_min} {lat_max},{lon_min} {lat_min}))"

    products = []
    try:
        r = requests.get(url, params={
            "$filter": f"Collection/Name eq 'SENTINEL-2' and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}')",
            "$top": max_results,
            "$orderby": "ContentDate/Start desc"
        }, timeout=30)
        if r.status_code == 200:
            for p in r.json().get("value", []):
                name = p.get("Name", "")
                date = p.get("ContentDate", {}).get("Start", "")[:10]
                size_mb = p.get("ContentLength", 0) / 1024 / 1024
                product_id = p.get("Id", "")
                products.append({
                    "name": name, "date": date, "size_mb": round(size_mb),
                    "product_id": product_id
                })
    except Exception as e:
        print(f"  Copernicus search error: {e}")

    return products

# ── Weather Quality Scoring ──────────────────────────────────────────────────

def score_scan_date(date_str, target_lat, target_lon):
    """
    Score a date for scan quality based on historical weather patterns.
    Uses web search via KoboldCPP to check historical conditions.

    Returns dict with scores for each sensor type.
    """
    # For now, use simple heuristics based on known Great Lake patterns
    # Later: integrate with actual historical weather API
    month = int(date_str.split("-")[1])

    scores = {
        "optical": 50,
        "thermal": 50,
        "sar": 60  # SAR works in most conditions
    }

    # Great Lakes cloud cover patterns (simplified)
    if month in [6, 7, 8, 9]:  # Summer = better optical
        scores["optical"] = 70
        scores["thermal"] = 65
    elif month in [3, 4, 5]:  # Spring = moderate
        scores["optical"] = 55
        scores["thermal"] = 55
    else:  # Fall/Winter = cloudy
        scores["optical"] = 30
        scores["thermal"] = 35

    # SAR is better in fall/winter (more wind = more capillary waves)
    if month in [9, 10, 11]:
        scores["sar"] = 80

    return {
        "date": date_str,
        "scores": scores,
        "overall": sum(scores.values()) / len(scores)
    }

# ── Intelligent Plan Builder ─────────────────────────────────────────────────

def build_search_plan(target_name=None, bbox=None, date_range=None, sensors="all"):
    """
    Build an intelligent search plan:
    1. Find all available data for target area and time range
    2. Score each date for scan quality
    3. Select best dates per sensor type
    4. Output download + scan plan
    """
    if target_name and target_name.lower() in KNOWN_TARGETS:
        target = KNOWN_TARGETS[target_name.lower()]
        if "bbox" in target:
            bbox = target["bbox"]
        else:
            margin = target.get("bbox_margin_deg", 0.1)
            bbox = [target["lat"]-margin, target["lon"]-margin,
                    target["lat"]+margin, target["lon"]+margin]
    elif not bbox:
        bbox = [45.78, -84.80, 46.10, -84.60]  # Default: Straits

    if not date_range:
        date_range = ("2015-01-01", "2016-12-31")

    start_date, end_date = date_range

    print(f"\n{'='*70}")
    print(f"CESAROPS INTELLIGENT SEARCH PLAN")
    print(f"{'='*70}")
    print(f"  Target: {target_name or 'Custom'}")
    print(f"  BBOX: {bbox}")
    print(f"  Date range: {start_date} to {end_date}")
    print(f"  Sensors: {sensors}")

    # Step 1: Search all available data
    print(f"\n  Step 1: Searching CMR for HLS data...")
    hls_data = search_hls_cmr(bbox, start_date, end_date)

    l30_count = len(hls_data.get("HLSL30", []))
    s30_count = len(hls_data.get("HLSS30", []))
    print(f"    HLSL30 (Landsat): {l30_count} granules")
    print(f"    HLSS30 (Sentinel-2): {s30_count} granules")

    # Collect all unique dates
    all_dates = set()
    for prod_data in hls_data.values():
        for g in prod_data:
            all_dates.add(g["date"])

    print(f"    Unique dates: {len(all_dates)}")

    # Step 2: Score each date
    print(f"\n  Step 2: Scanning dates for quality...")
    scored_dates = []
    for date_str in sorted(all_dates):
        score = score_scan_date(date_str, (bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2)
        score["granules"] = sum(1 for g in hls_data.get("HLSL30", []) + hls_data.get("HLSS30", [])
                               if g["date"] == date_str)
        scored_dates.append(score)

    # Sort by overall score
    scored_dates.sort(key=lambda x: x["overall"], reverse=True)

    # Step 3: Select best dates per sensor
    print(f"\n  Step 3: Selecting best dates per sensor...")
    plan = {
        "target": target_name,
        "bbox": bbox,
        "date_range": date_range,
        "generated": datetime.now(timezone.utc).isoformat(),
        "best_dates": {},
        "downloads": [],
        "scan_queue": []
    }

    for sensor in ["optical", "thermal", "sar"]:
        top_dates = [d for d in scored_dates if d["scores"][sensor] >= 60][:5]
        plan["best_dates"][sensor] = [
            {"date": d["date"], "score": d["scores"][sensor]} for d in top_dates
        ]
        print(f"    {sensor}: {len(top_dates)} good dates")
        for d in top_dates[:3]:
            print(f"      {d['date']} (score: {d['scores'][sensor]})")

    # Step 4: Build download plan
    print(f"\n  Step 4: Building download plan...")
    for prod in ["HLSL30", "HLSS30"]:
        for g in hls_data.get(prod, []):
            if g["date"] in [d["date"] for d in scored_dates[:20]]:
                plan["downloads"].append({
                    "title": g["title"],
                    "date": g["date"],
                    "tile": g["tile"],
                    "product": prod,
                    "bands": g["bands"],
                    "links": g["links"]
                })

    print(f"    {len(plan['downloads'])} granules queued for download")

    # Step 5: Build scan queue
    print(f"\n  Step 5: Building scan queue...")
    for date_info in scored_dates[:10]:
        date = date_info["date"]
        granules_for_date = [d for d in plan["downloads"] if d["date"] == date]
        if granules_for_date:
            bands_available = set()
            for g in granules_for_date:
                bands_available.update(g["bands"])

            sensors_to_run = []
            if any(b in bands_available for b in ["B10", "B11"]):
                sensors_to_run.append("thermal")
            if any(b in bands_available for b in ["B02", "B03", "B04", "B08"]):
                sensors_to_run.append("optical")
            # SAR needs separate data source

            if sensors_to_run:
                plan["scan_queue"].append({
                    "date": date,
                    "weather_score": date_info["overall"],
                    "sensors": sensors_to_run,
                    "granules": len(granules_for_date),
                    "bands": sorted(bands_available)
                })

    print(f"    {len(plan['scan_queue'])} scan dates queued")

    return plan

# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CESAROPS Intelligent Search Planner")
    parser.add_argument("--target", type=str, help="Known target name")
    parser.add_argument("--bbox", type=str, help="Custom bbox: lat_min,lon_min,lat_max,lon_max")
    parser.add_argument("--dates", type=str, help="Date range: YYYY-YYYY or YYYY-MM-DD,YYYY-MM-DD")
    parser.add_argument("--sensors", type=str, default="all", help="Sensors to search")
    parser.add_argument("--auto-plan", action="store_true", help="Build full search plan")
    parser.add_argument("--output", type=str, help="Save plan to JSON")
    args = parser.parse_args()

    bbox = None
    if args.bbox:
        bbox = [float(x) for x in args.bbox.split(",")]

    date_range = None
    if args.dates:
        parts = args.dates.split(",")
        if len(parts) == 2:
            date_range = (parts[0].strip(), parts[1].strip())
        elif len(parts) == 1:
            # Single year range
            year = parts[0].strip()
            date_range = (f"{year}-01-01", f"{year}-12-31")

    if args.auto_plan:
        plan = build_search_plan(
            target_name=args.target,
            bbox=bbox,
            date_range=date_range,
            sensors=args.sensors
        )

        # Save plan
        if args.output:
            out_path = Path(args.output)
        else:
            out_path = REPO / "outputs" / "probes" / "search_plan.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, indent=2))
        print(f"\n  Plan saved: {out_path}")

        # Print summary
        print(f"\n{'='*70}")
        print("SUMMARY")
        print(f"{'='*70}")
        print(f"  Granules to download: {len(plan['downloads'])}")
        print(f"  Scans to run: {len(plan['scan_queue'])}")
        for sq in plan['scan_queue'][:5]:
            print(f"    {sq['date']}: {', '.join(sq['sensors'])} ({sq['granules']} granules, score: {sq['weather_score']:.0f})")

if __name__ == '__main__':
    main()
