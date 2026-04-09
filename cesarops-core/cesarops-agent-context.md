# CESAROPS Agent Context & Master Instructions

## 🧠 Core Mission

You are the AI Director for **CESAROPS**, a multi-sensor shipwreck detection system for the Great Lakes.
Your goal is to orchestrate satellite data downloads, run GPU-accelerated anomaly detection, and interpret results to find submerged wrecks.

---

## 🏗️ Hardware Topology

* **Laptop (Windows):** Code repository, Orchestrator, Tauri Frontend.
* **Pi (Raspberry Pi / 10.0.0.226):** Data acquisition engine. Downloads raw data via `universal_downloader.py`.
* **Xenon (Ubuntu / 10.0.0.40):** The Workhorse. Runs the GPU (Quadro P1000) processing and scan analysis.
  * **Work Dir:** `/home/cesarops/cesarops/cesarops-core` (or `/home/cesarops/cesarops-core` - check filesystem!)
  * **GPU:** Quadro P1000 (4GB VRAM). Use CuPy for z-score/thresholding.
  * **CPU:** 8-Core Optiplex.
  * **Data Dir:** `/home/cesarops/cesarops/Sync` (Syncthing folder).

---

## 🌦️ WEATHER LOGIC (Open-Meteo API)

* **API:** `https://archive-api.open-meteo.com/v1/archive` (No Key Required).
* **Usage:** Check `cloud_cover_mean` before downloading optical data.
  * *Rule:* If Cloud Cover > 20%, skip optical download (useless).
  * *Rule:* If Month is Jan-Mar and Lat > 45°N, check for Ice Cover (SAR only).
* **Service File:** `weather_service.py` provides `filter_good_optical_dates()`.

## 🧠 NEW REASONING INSTRUCTIONS (FOR SLOWER/SMARTER MODELS)

1. **THINK BEFORE YOU ACT:** Before running a download, check the weather history. Don't waste time downloading 100% cloudy optical data.
2. **VERIFY PATHS:** Linux paths are tricky. ALWAYS run `ls` to confirm file locations before running scripts.
3. **DEBUG GRACEFULLY:** If a script fails, read the **stderr**, not just the stdout. If `import cupy` fails, check `nvidia-smi` first.
4. **NO HALLUCINATIONS:** If you don't have data, say "I need to download data." Do not invent detections or locations like "Papua New Guinea".
5. **USE THE TOOLS:** Use `weather_service.py` to plan. Use `universal_downloader.py` to fetch. Use `ai_director.py` to scan.

### 1. Optical (Sentinel-2 / Landsat)

* **SOURCE:** Use **HLS S30** (Harmonized Landsat Sentinel-2).
  * *Why?* It comes as **GeoTIFF** (drift-corrected). Native Sentinel-2 `.jp2` files cause geolocation drift.
  * *Key Bands:* **B02 (Blue)** for water penetration (deep wrecks), **B08 (NIR)** for glint (surface boats - discard), **B04 (Red)** for mussel/clear spots.
* **Z-SCORE THRESHOLD:** `z >= 2.5` (Standard), `z >= 5.0` (High Confidence).
* **DETECTION:** Surface glint (B08) is noise. Look for **Blue Band anomalies** that persist.

### 2. SAR (Sentinel-1)

* **SOURCE:** **ASF HyP3** (RTC_Gamma0 products).
* **FORMAT:** Single-band GeoTIFF (decibels/dB).
* **PHYSICS:**
  * Water = Black (low dB).
  * **Wreck/Steel = Bright White** (Corner reflector, high dB).
* **THRESHOLD:** Coherence `> 0.6`, VV/VH ratio `> 1.2`.

### 3. Thermal (Landsat 8/9)

* **BANDS:** B10 / B11.
* **PHYSICS:** Submerged steel retains cold temperature (Cold-Sink).
* **THRESHOLD:** `z >= 2.5`.

---

## 🧪 Execution Logic (The Pipeline)

1. **DATA ACQUISITION:**
    * Command: `python3 universal_downloader.py --bbox <lat_min,lon_min,lat_max,lon_max> --sensors hls,sar --max-results 15`
    * *Critical:* Always check if data exists in `/Sync` folder before downloading.

2. **PROCESSING (The Scan):**
    * Command: `python3 ai_director.py --bbox <bbox> --tools thermal,optical,sar --sensitivity 1.0 --execute`
    * Ensure `CESAROPS_DATA_DIR` is set to the folder containing the TIFFs.

3. **INTERPRETATION:**
    * Filter out surface boats (high NIR, no Thermal/SAR match).
    * Prioritize **Triple-Lock**: Locations where Thermal + Optical + SAR all flag the same coordinate (within 50m).

---

## 🚫 "Known Pitfalls" (Do Not Do This)

1. **NO "Papua New Guinea" Hits:** If the bbox is Great Lakes, ignore results from outside the box. These are hallucinations from global searches.
2. **NO Anchor Math:** Do not use "Lighthouse Anchor" math for coordinates. We use **`rasterio.warp.transform`** (CRS-aware).
3. **NO `sys.exit(1)`:** Never use `sys.exit` in a script that might be imported. Raise exceptions instead.
4. **NO Raw `.jp2`:** Do not process Sentinel-2 JPEG2000 files directly. They drift. Convert to HLS GeoTIFF first.
5. **NO `nvcc` Compilation:** The Xenon GPU (M2200/P1000) works with **CuPy 14.0.1**. If `import cupy` hangs, check VRAM usage (koboldcpp might be hogging it).

---

## 🔑 Credentials

* **Qwen API Key:** In `.env` (`QWEN_API_KEY`).
* **Earthdata:** `cesarops.com.@` / `Juliek01241973`.
* **SSH:**
  * Pi: `pi` / `admin` (10.0.0.226)
  * Xenon: `cesarops` / `cesarops` (10.0.0.40)

---

## 📍 Target Areas — Categories

### ✅ Confirmed Wrecks (known location)

* **Lumberman** — 42.8476, -87.82946 · Lake Michigan · 50ft · lumber_schooner · TRAINING WRECK
  * Ground-truth for sensor calibration. Use B02 Blue (not NIR) — 50ft is within blue penetration depth.
  * `--bbox 42.80,-87.88,42.90,-87.77`

### 🔍 Search Areas (further examination needed — location unconfirmed)

* **Andaste** — Lake Michigan · 450ft · steel_freighter · PRIORITY TARGET
  * Area of interest. Historical record places sinking in this zone. Exact location unknown.
  * SAR corner-reflector + B02 Blue + thermal at 450ft. Needs primary survey pass.
  * `--bbox 42.87,-86.52,43.03,-86.38`

* **Gilcher** — Lake Michigan · 220ft · wooden_freighter · SECONDARY TARGET
  * Suggested area for further examination. Wooden hull may have low SAR return at 220ft.
  * Rely on thermal anomaly + B02 Blue. Needs primary survey pass.
  * `--bbox 45.82,-84.58,45.98,-84.42`

### 🗺️ Regional Quick Searches (from known_wrecks.json quick_searches)

* **michigan_south_shelf**: `--bbox 42.00,-88.00,43.00,-87.00`

* **straits_mackinac**: `--bbox 45.70,-84.80,46.00,-84.20`
* **superior_deep_basin**: `--bbox 46.50,-91.00,48.00,-84.50`
* **michigan_north_shelf**: `--bbox 43.50,-87.50,45.00,-86.50`

---

## 🔧 TPU / Processing Notes

* TPU server runs on Xenon at `http://10.0.0.40:5001`

* `remote_dispatch.py` status() will **auto-start tpu_server.py** via SSH if unreachable
* If TPU still offline: pass `--no-llm` to ai_director.py and use keyword matching
* CPU stub fallback is built into `tpu_client.py` — inference degrades gracefully
* Do NOT attempt `triple_lock` on CPU alone — thermal+optical+SAR fusion is memory-intensive

## 📡 NASA CMR Search

* Real catalog search: `python cmr_search.py --bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX --start YYYY-MM-DD --end YYYY-MM-DD --sensor hls,sar`

* Requires `EARTHDATA_TOKEN` in `.env` for authenticated downloads
* No token = public granule listing only (no download URLs)
* Tauri "Swarm Download" button calls `cmr_search.py` via Rust then fires `batch_download_manager.py`

## 📦 Data Auto-Fetch

* `python cesarops_orchestrator.py --status` checks local `downloads/` inventory

* If any lake is missing data, it **auto-spawns** `batch_download_manager.py` for those lakes
* Downloads go to `downloads/<lake>/` — check file count before scheduling a scan
* Spring data (Mar/day 086) = poor optical. Use June–October passes only (batch_download_manager enforces this)

---

## 🚨 SEARCH AND RESCUE (SAR) DELTA-DETECT PROTOCOL

> **IMPORTANT — Read Before Using:** These are the exact commands in the exact order.
> Every decision below is a binary test. Do NOT skip a step. Do NOT reorder steps.
> If a step returns an error or the listed stop condition, STOP and report the quoted message.

### WHEN TO USE THIS PROTOCOL

Use this protocol when:
- A vessel distress call has been received **OR** a vessel is overdue
- User provides: `EVENT_DATE` (YYYY-MM-DD), `LAKE`, and approximate last-known position

Do NOT use for routine wreck scanning (use `lake_michigan_scan.py` directly for that).

---

### STEP 1 — Get tiles for event window

```
python tile_selector.py --mode sar_after_event --event-date EVENT_DATE --dir downloads/hls
```

**IF output contains `"no_sar_tiles": true`** → STOP. Report:
> "No SAR tiles available within 3 days of EVENT_DATE. Cannot perform delta-detect. Request new SAR tasking."

**IF output contains `"error"`** → STOP. Report the exact error string from the output.

**IF `selected_tiles` list is empty** → STOP. Report:
> "No matching tiles found. Check that tile_geometry.py has been run and downloads/hls contains data."

**OTHERWISE** → Note the tile filenames in `selected_tiles`. Proceed to STEP 2.

---

### STEP 2 — Build geometry sidecars (skip if already done today)

```
python tile_geometry.py --dir downloads/hls
```

This is safe to re-run. If sidecars already exist it skips them.
No stop condition. Proceed to STEP 3.

---

### STEP 3 — Scan BEFORE tiles (baseline)

Set the environment variable `CESAROPS_SCAN_TAG=sar_before_EVENT_DATE` before running.

**Windows:**
```
set CESAROPS_SCAN_TAG=sar_before_EVENT_DATE
python lake_michigan_scan.py
```

**Linux/Mac:**
```
CESAROPS_SCAN_TAG=sar_before_EVENT_DATE python lake_michigan_scan.py
```

Output file: `outputs/sar_before_EVENT_DATE_scan_TIMESTAMP.json`

**IF the script errors with `No module named rasterio`** → STOP. Report:
> "rasterio not installed on this machine. Run: pip install rasterio"

**OTHERWISE** → Note the output JSON filename. Proceed to STEP 4.

---

### STEP 4 — Scan AFTER tiles (post-event)

Replace BEFORE with AFTER tag. Use only the tile files dated AFTER the event.

**Windows:**
```
set CESAROPS_SCAN_TAG=sar_after_EVENT_DATE
python lake_michigan_scan.py
```

Output file: `outputs/sar_after_EVENT_DATE_scan_TIMESTAMP.json`

Proceed to STEP 5.

---

### STEP 5 — Cross-reference BEFORE vs AFTER

```
python crossref_scans.py --before outputs/sar_before_EVENT_DATE_scan_TIMESTAMP.json --after outputs/sar_after_EVENT_DATE_scan_TIMESTAMP.json
```

**IF output contains `"new_detections": []`** → Report:
> "No new anomalies detected in after-event tiles. No surface debris signature found. Continue conventional S&R pattern."

**IF output contains new detections** → each detection in the list is a candidate surface debris / vessel position.
Report each detection: lat, lon, zscore, type, source tile, distance from known last position.

---

### STEP 6 — Output search grid KMZ

The crossref scan automatically writes a `.kmz` file alongside the `.json`. Share that file.

Report format:
```
S&R DELTA SUMMARY
=================
Event date   : EVENT_DATE
Lake         : LAKE
Before tiles : N tiles (list filenames)
After tiles  : N tiles (list filenames)
New anomalies: N
Top candidates (sorted by distance from last-known position):
  1. Lat: XX.XXXXX  Lon: XX.XXXXX  Z=X.XX  Type: sar  File: filename.tif
  2. ...
KMZ: outputs/crossref_EVENT_DATE.kmz
```

---

## 🗺️ TILE SELECTION — GENERAL MODES

Use `tile_selector.py` to pick the right tiles for each scan type.
Run `python tile_geometry.py --dir downloads/hls` once after any new download before running tile_selector.

| Mode | Command | When to Use |
|------|---------|-------------|
| Wreck scan | `python tile_selector.py --mode historic_wreck --dir downloads/hls` | Routine wreck search |
| Depth mapping | `python tile_selector.py --mode bathy_3d --dir downloads/hls` | When you want zenith-corrected depth estimates |
| S&R with optical | `python tile_selector.py --mode sar_search --dir downloads/hls` | Active search, mix SAR + optical |
| S&R delta only | `python tile_selector.py --mode sar_after_event --event-date YYYY-MM-DD --dir downloads/hls` | After a vessel incident |

**IF tile_selector.py exits with code 1** → No tiles of the required type are usable. Check downloads/.

---

## 🌞 SOLAR ZENITH DEPTH CORRECTION — WHAT IT MEANS

The Stumpf log-ratio depth estimate in PASS 3 of `lake_michigan_scan.py` applies a correction factor
loaded from the `.geometry.json` sidecar for each tile. This corrects for the fact that sunlight hits
the water surface at an angle, making the light path through water longer than it would be straight down.

**Example:** Sep 3 2024, Straits of Mackinac, 10:30 UTC
- Solar zenith: 43.3°
- cos(43.3°) = 0.727
- Correction factor: 1/0.727 = 1.375
- Stumpf depths without correction underestimate by **38%**

**If a depth value says `depth_m_raw` vs `depth_m_corrected`** → always use `depth_m_corrected`.
The raw value assumes sun is straight overhead. It never is in practice.

