#!/usr/bin/env python3
"""
CMR Search — NASA Common Metadata Repository live granule query.

Called by the Tauri/Rust layer (nasa_agent.rs) to provide real NASA catalog data.
Also callable directly for testing or scripted use.

Usage:
    python cmr_search.py --bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX \
                         --start 2024-06-01 --end 2024-10-31 \
                         --sensor hls,sar

Output: JSON to stdout:
    {
      "granules": [...],
      "count": N,
      "errors": [...],
      "bbox": [...],
      "sensors_queried": [...]
    }

Auth: set EARTHDATA_TOKEN in environment or .env file.
"""

import sys
import json
import argparse
import os
from pathlib import Path

import requests


# ── Auth ──────────────────────────────────────────────────────────────────────

def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

_dotenv = _load_env(Path(__file__).parent / ".env")
EARTHDATA_TOKEN = os.environ.get("EARTHDATA_TOKEN", _dotenv.get("EARTHDATA_TOKEN", ""))

CMR_BASE = "https://cmr.earthdata.nasa.gov/search"

# NASA CMR concept IDs for each sensor product
CONCEPT_IDS = {
    "hls_l30": "C2021957657-LPCLOUD",      # HLS Landsat-30m
    "hls_s30": "C2021957295-LPCLOUD",      # HLS Sentinel-30m
    "sar":     "C1214354438-ASF",          # Sentinel-1 SLC (ASF)
    "swot":    "C2799438271-POCLOUD",       # SWOT L2 SSH Expert
    "atl13":   "C2144800918-NSIDC_ECS",    # ICESat-2 ATL13 inland water
    "modis_lst": "C1621091311-LPDAAC_ECS", # MODIS MOD11A1 LST 1km daily
}


# ── Core query ────────────────────────────────────────────────────────────────

def cmr_query(concept_id: str, bbox: list, start: str, end: str,
              max_results: int = 20) -> list:
    """
    Query NASA CMR for granules matching concept_id, bbox, and date range.

    bbox = [lat_min, lon_min, lat_max, lon_max]
    CMR expects: lon_min,lat_min,lon_max,lat_max
    """
    lat_min, lon_min, lat_max, lon_max = bbox
    cmr_bbox = f"{lon_min},{lat_min},{lon_max},{lat_max}"

    params = {
        "concept_id": concept_id,
        "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z",
        "bounding_box": cmr_bbox,
        "page_size": min(max_results, 200),
        "sort_key": "-start_date",
    }

    headers = {"Accept": "application/json"}
    if EARTHDATA_TOKEN:
        headers["Authorization"] = f"Bearer {EARTHDATA_TOKEN}"

    try:
        resp = requests.get(
            f"{CMR_BASE}/granules.json",
            params=params,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("feed", {}).get("entry", [])
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        body = e.response.text[:200] if e.response is not None else ""
        return [{"_error": f"CMR HTTP {code}: {body}"}]
    except requests.exceptions.ConnectionError as e:
        return [{"_error": f"CMR unreachable: {e}"}]
    except Exception as e:
        return [{"_error": str(e)}]


def _first_download_url(links: list) -> str:
    """Return the first data download link from a CMR granule link list."""
    DATA_REL = "http://esipfed.org/ns/fedsearch/1.1/data#"
    for link in links:
        if link.get("rel") == DATA_REL and link.get("href", "").startswith("http"):
            return link["href"]
    return ""


# ── Per-sensor handlers ───────────────────────────────────────────────────────

def _query_hls(bbox, start, end, max_results) -> tuple:
    """Returns (granules_list, errors_list)."""
    granules, errors = [], []
    for cid_key in ("hls_l30", "hls_s30"):
        label = "HLS-Landsat" if cid_key == "hls_l30" else "HLS-Sentinel"
        for g in cmr_query(CONCEPT_IDS[cid_key], bbox, start, end, max_results):
            if "_error" in g:
                errors.append({"sensor": label, "error": g["_error"]})
            else:
                granules.append({
                    "id": g.get("id", ""),
                    "sensor": label,
                    "title": g.get("title", ""),
                    "time_start": g.get("time_start", ""),
                    "cloud_cover": g.get("cloud_cover"),
                    "download_url": _first_download_url(g.get("links", [])),
                })
    return granules, errors


def _query_sar(bbox, start, end, max_results) -> tuple:
    granules, errors = [], []
    for g in cmr_query(CONCEPT_IDS["sar"], bbox, start, end, max_results):
        if "_error" in g:
            errors.append({"sensor": "SAR", "error": g["_error"]})
        else:
            granules.append({
                "id": g.get("id", ""),
                "sensor": "SAR-Sentinel1",
                "title": g.get("title", ""),
                "time_start": g.get("time_start", ""),
                "polarization": "VV+VH",
                "download_url": _first_download_url(g.get("links", [])),
            })
    return granules, errors


def _query_generic(sensor_key: str, label: str, bbox, start, end, max_results) -> tuple:
    granules, errors = [], []
    for g in cmr_query(CONCEPT_IDS[sensor_key], bbox, start, end, max_results):
        if "_error" in g:
            errors.append({"sensor": label, "error": g["_error"]})
        else:
            granules.append({
                "id": g.get("id", ""),
                "sensor": label,
                "title": g.get("title", ""),
                "time_start": g.get("time_start", ""),
                "download_url": _first_download_url(g.get("links", [])),
            })
    return granules, errors


# ── Main ──────────────────────────────────────────────────────────────────────

def search(bbox: list, start: str, end: str, sensors: list,
           max_results: int = 20) -> dict:
    """Core search callable from other modules."""
    all_granules, all_errors = [], []

    sensor_map = {
        "hls":      lambda: _query_hls(bbox, start, end, max_results),
        "hls_l30":  lambda: _query_hls(bbox, start, end, max_results),
        "hls_s30":  lambda: _query_hls(bbox, start, end, max_results),
        "sar":      lambda: _query_sar(bbox, start, end, max_results),
        "swot":     lambda: _query_generic("swot",    "SWOT",       bbox, start, end, max_results),
        "atl13":    lambda: _query_generic("atl13",   "ICESat2",    bbox, start, end, max_results),
        "modis":    lambda: _query_generic("modis_lst","MODIS-LST", bbox, start, end, max_results),
    }

    seen = set()
    for s in sensors:
        key = s.strip().lower()
        if key in seen or key not in sensor_map:
            if key not in sensor_map:
                all_errors.append({"sensor": key, "error": "unknown sensor key"})
            continue
        seen.add(key)
        gs, es = sensor_map[key]()
        all_granules.extend(gs)
        all_errors.extend(es)

    return {
        "granules": all_granules,
        "count": len(all_granules),
        "errors": all_errors,
        "bbox": bbox,
        "start": start,
        "end": end,
        "sensors_queried": list(seen),
        "auth": "token" if EARTHDATA_TOKEN else "none (public only)",
    }


def main():
    p = argparse.ArgumentParser(description="Query NASA CMR for satellite granules")
    p.add_argument("--bbox", required=True,
                   help="lat_min,lon_min,lat_max,lon_max")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end",   required=True, help="YYYY-MM-DD")
    p.add_argument("--sensor", default="hls",
                   help="comma-sep: hls, sar, swot, atl13, modis")
    p.add_argument("--max-results", type=int, default=20)
    args = p.parse_args()

    bbox    = [float(x) for x in args.bbox.split(",")]
    sensors = [s.strip() for s in args.sensor.split(",")]
    result  = search(bbox, args.start, args.end, sensors, args.max_results)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
