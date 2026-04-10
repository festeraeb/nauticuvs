#!/usr/bin/env python3
"""
Full Lake Michigan / Straits of Mackinac Scan — CPU/GPU Hybrid Z-Score to KMZ Output

Sensors processed:
  - B02 (Blue 458-523nm)  : Water-penetrating optical — up to ~55m/180ft in Lake Michigan clarity
  - B03 (Green 543-578nm) : Stumpf-ratio bathymetry partner
  - B04 (Red 650-680nm)   : Surface reference + thin oil sheen (elevated Red over dark water)
  - B10 (Thermal LWIR)    : Cold-sink detection — submerged steel mass
  - B11 (SWIR 1565nm)     : Hydrocarbon/oil DARK anomaly — fuel and oil absorb SWIR
  - SAR VV/VH GeoTIFF     : Corner-reflector (bright wreck) OR smooth-dark (oil slick)

Hydrocarbon mode:
  - Oil/fuel sheens: B11 DARK (z < -1.8) + B04 elevated (z > 1.5) = HC_CANDIDATE
  - Line 5 corridor: lat 45.78-45.82, lon -84.73 ± 0.01 → tagged LINE5_CANDIDATE
  - Wake suppression: linear blobs (aspect ratio > 8) rejected as vessel wakes
  - SAR smooth-dark: BOTH VV and VH anomalously low = oil damping capillary waves

Known wreck flagging:
  - If any detection falls within 0.004° (~444m) of a known wreck anchor, it is flagged KNOWN_WRECK_HIT
  - These are ground-truth calibration hits (good!) — still reported, tagged separately

GPU: CuPy on Quadro P1000/M2200.  Falls back to NumPy CPU automatically.
CPU: NumPy with rasterio.warp for CRS transforms (no anchor math).
"""

import os
import sys
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import simplekml

# Ensure UTF-8 output on Windows (handles ← → arrows in print statements)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Set CUDA paths via helper (preferred CUDA 13.2 -> fallback 11.8)
from cuda_env import configure_cuda_environment
cuda_info = configure_cuda_environment()
cuda_bin = cuda_info['cuda_bin']

import rasterio
from rasterio.transform import xy
from pyproj import Transformer

# Try to import CuPy for GPU acceleration
# We test in a subprocess with timeout to avoid hanging on nvcc
HAS_GPU = False
cp = None

def _test_cupy():
    """Test if CuPy works without nvcc dependency (runs in subprocess)."""
    try:
        import cupy as _cp
        _test = _cp.array([1.0, 2.0, 3.0])
        _s = float(_cp.sum(_test))
        return abs(_s - 6.0) < 0.001
    except Exception:
        return False

if __name__ != '__main__':
    # When imported as module, test CuPy via subprocess with timeout
    import subprocess
    result = subprocess.run(
        [sys.executable, '-c',
         'import cupy; t=cupy.array([1,2,3]); print(float(cupy.sum(t)))'],
        capture_output=True, text=True, timeout=8
    )
    if result.returncode == 0 and '6.0' in result.stdout:
        import cupy as cp
        HAS_GPU = True
        print("  GPU: CuPy enabled (M2200 Quadro)")
    else:
        print(f"  GPU: CuPy unavailable — using NumPy CPU fallback")

# 8-POINT ANCHOR LOCK CALIBRATION - Full Lake Michigan Coverage
ANCHOR_POINTS = {
    # Southern Lake Michigan
    "chicago": {"lat": 41.8900, "lon": -87.6044, "name": "Chicago Harbor Light (IL)"},
    "waukegan": {"lat": 42.3638, "lon": -87.8034, "name": "Waukegan Harbor Light (IL)"},
    "michigan_city": {"lat": 41.7258, "lon": -86.9047, "name": "Michigan City Light (IN)"},
    
    # Mid Lake Michigan
    "wind_point": {"lat": 42.8000, "lon": -87.8178, "name": "Wind Point Light (WI)"},
    "holland": {"lat": 42.7784, "lon": -86.2066, "name": "Holland Harbor Light (MI)"},
    "muskegon": {"lat": 43.2314, "lon": -86.3481, "name": "Muskegon South Pier Light (MI)"},
    
    # Northern Lake Michigan / Straits
    "sturgeon_bay": {"lat": 44.7947, "lon": -87.3142, "name": "Sturgeon Bay Ship Canal Light (WI)"},
    "point_betsie": {"lat": 44.6919, "lon": -86.2544, "name": "Point Betsie Light (MI)"},
}

def apply_anchor_calibration(lat, lon):
    """
    DEPRECATED: This function blends detections toward anchor centroids,
    which systematically biases results toward the geometric center of the lake.

    The process_tiff_with_coords() function uses proper CRS-aware geotransform
    via rasterio.warp.transform — no anchor math needed.

    This function is kept as a no-op for backward compatibility.
    Do NOT use it — it returns coordinates unchanged.
    """
    # No-op: return coordinates unchanged
    return lat, lon


# ── Known Wreck Registry (for ground-truth hit tagging) ──────────────────────
# Loaded dynamically from known_wrecks.json so all wrecks — including Straits
# bridge wrecks (Minneapolis, Cedarville, M. Stalker, etc.) — are included.
# Only entries with confirmed numeric lat/lon are added.  Falls back to a
# minimal stub if the file is missing or unreadable.
def _load_known_wrecks() -> list:
    _here = Path(__file__).parent if "__file__" in globals() else Path(".")
    _json_path = _here / "known_wrecks.json"
    try:
        import json as _json
        raw = _json.load(open(_json_path, encoding="utf-8"))
        wrecks_dict = raw.get("wrecks", {})
        out = []
        for wreck_id, w in wrecks_dict.items():
            lat = w.get("lat")
            lon = w.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue  # skip entries with unknown coords ("?")
            out.append({
                "id":        wreck_id,
                "name":      w.get("name", wreck_id),
                "lat":       float(lat),
                "lon":       float(lon),
                "depth_ft":  w.get("depth_ft"),
                "type":      w.get("type", "unknown"),
                "year_lost": w.get("year_lost"),
            })
        print(f"[registry] Loaded {len(out)} known wrecks from {_json_path.name}", flush=True)
        return out
    except Exception as _e:
        print(f"[registry] WARNING: could not load {_json_path}: {_e} — using stub", flush=True)
        return [
            {"id": "lumberman", "name": "Lumberman", "lat": 42.8476, "lon": -87.82946, "depth_ft": 50, "type": "lumber_schooner", "year_lost": 1893},
        ]

KNOWN_WRECKS = _load_known_wrecks()
KNOWN_WRECK_RADIUS_DEG = 0.004  # ~444m (~500 yards) — tight ground-truth hit radius

# Line 5 pipeline corridor through Straits of Mackinac (Enbridge dual pipeline)
LINE5_CORRIDOR = {"lat_min": 45.775, "lat_max": 45.825, "lon_min": -84.740, "lon_max": -84.715}

def _flag_known_wreck(lat: float, lon: float):
    """Return matching known wreck dict if detection is within radius, else None."""
    for w in KNOWN_WRECKS:
        dlat = abs(lat - w["lat"])
        dlon = abs(lon - w["lon"])
        if dlat < KNOWN_WRECK_RADIUS_DEG and dlon < KNOWN_WRECK_RADIUS_DEG:
            return w
    return None

def _flag_line5(lat: float, lon: float) -> bool:
    """Return True if coordinate falls inside the Line 5 pipeline corridor."""
    c = LINE5_CORRIDOR
    return c["lat_min"] <= lat <= c["lat_max"] and c["lon_min"] <= lon <= c["lon_max"]

def _is_linear_wake(rows, cols, min_aspect: float = 8.0) -> bool:
    """
    Reject vessel wakes.  A genuine oil slick or submerged anomaly is diffuse.
    A wake is a narrow linear smear: bounding-box aspect ratio >> 1.
    Returns True if the pixel cluster looks like a wake (should be suppressed).
    """
    if len(rows) < 4:
        return False
    row_span = int(rows.max() - rows.min()) + 1
    col_span = int(cols.max() - cols.min()) + 1
    if row_span == 0 or col_span == 0:
        return False
    aspect = max(row_span, col_span) / max(min(row_span, col_span), 1)
    return aspect > min_aspect


def process_hydrocarbon_bands(b11_path: Path, b04_path: Path,
                               swir_thresh: float = -1.8,
                               red_thresh: float = 1.5) -> list:
    """
    Hydrocarbon / oil-slick detection using B11 SWIR + B04 Red.

    Physics:
      - Hydrocarbons strongly absorb SWIR (B11 1565nm) → dark (negative z-score).
      - Thin oil sheens slightly elevate Red (B04 650nm) reflectance over dark water.
      - Dual confirmation: B11 dark AND B04 elevated = HC_CANDIDATE.
      - Wake suppression: linear blob clusters (aspect_ratio > 8) are rejected.
      - Line 5 tagging: any HC hit inside the pipeline corridor gets line5_candidate=True.

    Returns list of detection dicts with type='hydrocarbon'.
    """
    from rasterio.warp import transform as warp_transform

    if not b11_path.exists():
        print(f"    [HC] B11 SWIR not found: {b11_path.name}")
        return []

    print(f"  [HC] Hydrocarbon scan: {b11_path.name}")

    with rasterio.open(b11_path) as src11:
        b11 = src11.read(1).astype(np.float32)
        b11[b11 == -9999] = np.nan
        valid11 = np.isfinite(b11) & (b11 > 0)
        if not np.any(valid11):
            print(f"    [HC] No valid B11 pixels")
            return []
        mean11 = float(np.nanmean(b11[valid11]))
        std11  = float(np.nanstd(b11[valid11]))
        if std11 < 1e-6:
            return []
        z11 = np.full_like(b11, np.nan)
        z11[valid11] = (b11[valid11] - mean11) / std11
        # Dark SWIR = hydrocarbon candidate
        dark_mask = z11 < swir_thresh
        crs11 = src11.crs

    # Load B04 Red for confirmation if available
    z04 = None
    if b04_path and b04_path.exists():
        with rasterio.open(b04_path) as src04:
            b04 = src04.read(1).astype(np.float32)
            b04[b04 == -9999] = np.nan
            valid04 = np.isfinite(b04) & (b04 > 0)
            if np.any(valid04):
                mean04 = float(np.nanmean(b04[valid04]))
                std04  = float(np.nanstd(b04[valid04]))
                if std04 > 1e-6:
                    z04_raw = np.full_like(b04, np.nan)
                    z04_raw[valid04] = (b04[valid04] - mean04) / std04
                    # If shapes differ (e.g. SWIR at 20m vs Red at 10m),
                    # downsample the Red z-score to match B11 shape
                    if z04_raw.shape != b11.shape:
                        import skimage.transform as skt
                        z04 = skt.resize(z04_raw, b11.shape,
                                         order=1, anti_aliasing=True,
                                         preserve_range=True).astype(np.float32)
                    else:
                        z04 = z04_raw
                    # Require B04 elevated (thin sheen brightens red band); relax if no B04
                    dark_mask = dark_mask & (z04 > red_thresh)

    hc_count = int(np.sum(dark_mask))
    print(f"    [HC] Candidate pixels (B11 dark{'+B04 bright' if z04 is not None else ''}): {hc_count}")
    if hc_count == 0:
        return []

    # Label connected blobs and apply wake suppression
    # Simple label via scipy if available, else treat each pixel independently
    from scipy import ndimage as ndi
    labeled, num_features = ndi.label(dark_mask)
    print(f"    [HC] Connected blobs: {num_features}")

    detections = []
    for blob_id in range(1, num_features + 1):
        blob_mask = labeled == blob_id
        blob_rows, blob_cols = np.where(blob_mask)
        if len(blob_rows) < 4:          # too small — skip noise pixels
            continue
        if _is_linear_wake(blob_rows, blob_cols):
            print(f"      Blob {blob_id}: rejected as linear wake (aspect > 8)")
            continue

        # Centroid in pixel space
        row_c = int(np.mean(blob_rows))
        col_c = int(np.mean(blob_cols))
        z_val = float(np.nanmean(z11[blob_mask]))
        area_px = int(np.sum(blob_mask))

        # Convert centroid to WGS84
        with rasterio.open(b11_path) as src11:
            local_x, local_y = src11.xy(row_c, col_c)
            lon_pt, lat_pt = warp_transform(crs11, 'EPSG:4326', [local_x], [local_y])
        lat_v = lat_pt[0]
        lon_v = lon_pt[0]

        known = _flag_known_wreck(lat_v, lon_v)
        line5 = _flag_line5(lat_v, lon_v)

        det = {
            "lat": lat_v,
            "lon": lon_v,
            "zscore": z_val,
            "type": "hydrocarbon",
            "source": b11_path.name,
            "band": "B11_SWIR",
            "area_pixels": area_px,
            "line5_candidate": line5,
            "known_wreck_hit": known["id"] if known else None,
            "known_wreck_name": known["name"] if known else None,
            "hc_subtype": (
                "LINE5_CANDIDATE" if line5 else
                ("WRECK_SEEP_CANDIDATE" if known else "UNKNOWN_DIFFUSE")
            ),
            "pixel": {"row": row_c, "col": col_c},
        }
        detections.append(det)
        tag = det["hc_subtype"]
        print(f"      Blob {blob_id}: {area_px}px  z={z_val:.2f}  → {tag}  ({lat_v:.5f}, {lon_v:.5f})")

    return detections


def compute_nauticuvs_pass(band_tif: Path, scan_bbox=None,
                            sigma_scales=(1.5, 2.5, 4.0),
                            energy_threshold: float = 3.5,
                            top_n: int = 50) -> list:
    """
    NauticUVs proxy: multi-scale Laplacian-of-Gaussian (LoG) blob detection.

    Approximates the key output of the FDCT (Fast Discrete Curvelet Transform)
    curvelet energy analysis without requiring the curvelop library.

    Physics behind this for submerged wrecks:
      At 75-128ft depth (Straits wrecks), the hull is at/beyond the optical floor
      BUT creates a surface expression:
        - Upwelling from the cold hull disturbs water surface capillary wave pattern
        - Thermal plume (B10): cold water rises from hull, creates a 50-200m cold pool
        - B02 blue: sediment disturbance halo and slight subsurface reflectance bump
      The surface expression is a compact blob (~3-6 pixels at 30m/px = 90-180m).

    LoG at sigma=1.5-4.0 pixels (45-120m hull-scale features):
      - Strong positive LoG peak = compact bright blob (reflectance bump or turbidity ring)
      - Strong negative LoG peak = compact dark blob (cold pool over cold-sink hull in B10)
      - fdct_energy_ratio = peak LoG response / scene LoG std (curvelet energy proxy)
      - directional_strength = max oriented gradient / mean gradient (hull elongation)
      - edge_density = fraction of supra-threshold LoG pixels in 5x5 neighborhood

    Works on:
      - B02 (blue): surface disturbance blobs, subsurface reflectance anomalies
      - B10 (thermal): cold plume blobs over submerged steel cold-sinks
    """
    from rasterio.warp import transform as warp_transform
    from scipy.ndimage import gaussian_laplace, sobel

    if not band_tif.exists():
        return []

    band_label = 'B10_thermal' if ('B10' in band_tif.name.upper() or
                                    'THERMAL' in band_tif.name.upper()) else 'B02_blue'
    print(f"  [NUV] NauticUVs LoG scan: {band_tif.name}  ({band_label})")

    with rasterio.open(band_tif) as src:
        data = src.read(1).astype(np.float32)

    # Mask nodata
    nodata_mask = (data <= 0) | ~np.isfinite(data)
    data[nodata_mask] = np.nan

    # Normalize to 0-1 range on valid pixels for consistent LoG response
    valid_data = data[~nodata_mask]
    if valid_data.size < 200:
        print(f"    [NUV] Insufficient valid pixels — skipping")
        return []
    v_min = float(np.nanpercentile(valid_data, 2))
    v_max = float(np.nanpercentile(valid_data, 98))
    if v_max - v_min < 1e-6:
        return []
    norm = np.where(nodata_mask, 0.0, np.clip((data - v_min) / (v_max - v_min), 0.0, 1.0))

    # Multi-scale LoG: sum responses across hull-scale sigmas
    # For B10 cold-sink → invert so cold blobs become positive peaks
    invert = 'B10' in band_tif.name.upper() or 'THERMAL' in band_tif.name.upper()
    log_combined = np.zeros_like(norm)
    for sigma in sigma_scales:
        log_scale = -gaussian_laplace(norm, sigma=sigma)  # negative LoG = bright blob detector
        if invert:
            log_scale = -log_scale  # cold blobs are negative before inversion
        log_combined += log_scale

    # Scene statistics for energy ratio (curvelet energy proxy)
    log_valid = log_combined[~nodata_mask]
    log_mean = float(np.nanmean(log_valid))
    log_std  = float(np.nanstd(log_valid))
    if log_std < 1e-9:
        return []

    # Directional gradient strength (hull elongation proxy)
    gx = sobel(norm, axis=1)
    gy = sobel(norm, axis=0)
    grad_mag = np.hypot(gx, gy)

    # Find peaks above energy_threshold sigma
    peak_mask = ((log_combined - log_mean) / log_std) > energy_threshold
    peak_mask &= ~nodata_mask

    peak_count = int(np.count_nonzero(peak_mask))
    print(f"    [NUV] LoG blob peaks (energy>{energy_threshold}σ): {peak_count}")
    if peak_count == 0:
        return []

    # Spatial filter to scan bbox
    with rasterio.open(band_tif) as src:
        rows, cols = np.where(peak_mask)
        energies = ((log_combined[peak_mask] - log_mean) / log_std)
        if scan_bbox is not None and len(rows) > 0:
            xs_4326 = [scan_bbox[1], scan_bbox[1], scan_bbox[3], scan_bbox[3]]
            ys_4326 = [scan_bbox[0], scan_bbox[2], scan_bbox[0], scan_bbox[2]]
            xs_crs, ys_crs = warp_transform('EPSG:4326', src.crs, xs_4326, ys_4326)
            bbox_rows, bbox_cols = [], []
            for x, y in zip(xs_crs, ys_crs):
                try:
                    r, c = src.index(x, y)
                    bbox_rows.append(r); bbox_cols.append(c)
                except Exception:
                    pass
            if bbox_rows:
                r_min = max(0, min(bbox_rows)); r_max = min(data.shape[0]-1, max(bbox_rows))
                c_min = max(0, min(bbox_cols)); c_max = min(data.shape[1]-1, max(bbox_cols))
                if r_min > r_max: r_min, r_max = r_max, r_min
                if c_min > c_max: c_min, c_max = c_max, c_min
                in_bbox = ((rows >= r_min) & (rows <= r_max) &
                           (cols >= c_min) & (cols <= c_max))
                rows = rows[in_bbox]; energies = energies[in_bbox]
                cols_filt = cols[in_bbox]
                cols = cols_filt
                print(f"    [NUV] In-bbox peaks: {len(rows)}")

        if len(rows) == 0:
            return []

        # Sort by energy, take top_n
        sorted_idx = np.argsort(energies)[::-1][:top_n]
        detections = []
        for idx in sorted_idx:
            r = int(rows[idx])
            c = int(cols[idx])
            lx, ly = src.xy(r, c)
            lon_v, lat_v = warp_transform(src.crs, 'EPSG:4326', [lx], [ly])
            energy = float(energies[idx])
            dir_strength = float(grad_mag[r, c]) / (float(np.nanmean(grad_mag[~nodata_mask])) + 1e-9)
            # 5x5 edge density
            r0 = max(0, r-2); r1 = min(data.shape[0]-1, r+2)
            c0 = max(0, c-2); c1 = min(data.shape[1]-1, c+2)
            neigh = peak_mask[r0:r1+1, c0:c1+1]
            edge_density = float(np.count_nonzero(neigh)) / max(neigh.size, 1)

            known = _flag_known_wreck(lat_v[0], lon_v[0])
            line5 = _flag_line5(lat_v[0], lon_v[0])
            detections.append({
                "lat":                    lat_v[0],
                "lon":                    lon_v[0],
                "zscore":                 energy,  # energy ratio as z-proxy
                "type":                   "nauticuvs_candidate",
                "source":                 band_tif.name,
                "band_label":             band_label,
                "nauticuvs_fdct_energy_ratio":   round(energy, 3),
                "nauticuvs_directional_strength": round(dir_strength, 3),
                "nauticuvs_edge_density":         round(edge_density, 3),
                "known_wreck_hit":        known["id"]   if known else None,
                "known_wreck_name":       known["name"] if known else None,
                "line5_candidate":        line5,
                "pixel":                  {"row": r, "col": c},
            })
    return detections


def compute_stumpf_pass(blue_tif: Path, green_tif: Path,
                        scan_bbox=None, top_n: int = 100) -> list:
    """
    Stumpf (2003) log-ratio bathymetric depth estimation for optical B02/B03 pairs.

    Physics:
      depth_relative = ln(R_blue) / ln(R_green)
      Shallower water → higher ratio (blue penetrates less, green dominates)
      Deeper water    → lower ratio

    Solar zenith correction:
      Raw Stumpf depths assume nadir illumination.
      When sun is not straight overhead, the actual light path through water is:
        path_length = depth / cos(solar_zenith)
      So the water column attenuates more — Beer-Lambert gives shallower apparent depth.
      Correction: depth_corrected = depth_raw / cos(solar_zenith)
      Example: Sep 3 2024, zenith=43.3° → factor=1.375 → depths 38% too shallow without this.

    Geometry sidecar:
      Reads <blue_tif_stem>.geometry.json if present (written by tile_geometry.py).
      Falls back to cos_zenith=1.0 (no correction) if sidecar absent.

    Returns list of detection dicts tagged with type='stumpf_shallow'.
    """
    import json, math
    from rasterio.warp import transform as warp_transform

    if not blue_tif.exists() or not green_tif.exists():
        print(f"    [ST] Blue/Green pair incomplete — skipping")
        return []

    print(f"  [ST] Stumpf pass: {blue_tif.name}")

    # Load solar zenith correction from geometry sidecar
    cos_zenith = 1.0  # default: nadir (no correction)
    depth_correction_factor = 1.0
    sidecar_path = blue_tif.parent / (blue_tif.stem + '.geometry.json')
    if sidecar_path.exists():
        with open(sidecar_path) as f:
            geo = json.load(f)
        cos_zenith = geo.get('cos_solar_zenith', 1.0)
        depth_correction_factor = geo.get('depth_correction', 1.0)
        print(f"    [ST] Geometry sidecar loaded: zenith={geo.get('solar_zenith_deg', '?')}°  "
              f"correction_factor={depth_correction_factor:.3f}")
    else:
        print(f"    [ST] No geometry sidecar — using nadir assumption (no zenith correction)")

    with rasterio.open(blue_tif) as src_b:
        blue = src_b.read(1).astype(np.float32)
        transform_b = src_b.transform
        crs_b = src_b.crs
        shape_b = blue.shape

    with rasterio.open(green_tif) as src_g:
        green_raw = src_g.read(1).astype(np.float32)
        if green_raw.shape != shape_b:
            # Resize to match blue (handles 10m vs 20m)
            try:
                import skimage.transform as skt
                green = skt.resize(green_raw, shape_b, order=1,
                                   anti_aliasing=True, preserve_range=True).astype(np.float32)
            except ImportError:
                print("    [ST] skimage not available for resize — skipping")
                return []
        else:
            green = green_raw

    # Mask invalid pixels — HLS reflectance scale is ~0–10000
    valid = (blue > 10) & (green > 10) & np.isfinite(blue) & np.isfinite(green)

    # Normalize to reflectance [0..1] if values > 1 (HLS scale factor = 0.0001)
    if float(np.nanmedian(blue[valid])) > 1.5:
        blue  = blue  * 0.0001
        green = green * 0.0001

    # Stumpf log ratio
    with np.errstate(divide='ignore', invalid='ignore'):
        log_blue  = np.log(np.where(valid & (blue  > 1e-6), blue,  np.nan))
        log_green = np.log(np.where(valid & (green > 1e-6), green, np.nan))

    # Avoid divide-by-zero where log_green ≈ 0
    valid_ratio = valid & np.isfinite(log_blue) & np.isfinite(log_green) & (np.abs(log_green) > 0.01)
    ratio = np.full_like(blue, np.nan)
    ratio[valid_ratio] = log_blue[valid_ratio] / log_green[valid_ratio]

    # Normalize ratio to depth proxy [0..1] range within scene
    ratio_valid = ratio[valid_ratio]
    if ratio_valid.size < 100:
        print("    [ST] Insufficient valid ratio pixels — skipping")
        return []

    ratio_min = float(np.nanpercentile(ratio_valid, 2))
    ratio_max = float(np.nanpercentile(ratio_valid, 98))
    if (ratio_max - ratio_min) < 0.01:
        print("    [ST] Ratio range too narrow — homogeneous scene, skipping")
        return []

    # Shallow anomalies: high ratio values (relatively blue-dominant = shallow)
    # Z-score the ratio to find statistically unusual shallow patches
    mean_r = float(np.nanmean(ratio_valid))
    std_r  = float(np.nanstd(ratio_valid))
    if std_r < 1e-6:
        return []

    ratio_z = np.full_like(ratio, np.nan)
    ratio_z[valid_ratio] = (ratio[valid_ratio] - mean_r) / std_r

    # High positive z-score → unusually shallow (potential wreck/shoal)
    shallow_threshold = 2.0
    anomaly_mask = (ratio_z > shallow_threshold) & valid_ratio

    # Apply scan bbox
    rows, cols = np.where(anomaly_mask)
    if scan_bbox is not None and len(rows) > 0:
        from rasterio.warp import transform as warp_t
        lat_min, lon_min, lat_max, lon_max = scan_bbox
        xs_4326 = [lon_min, lon_min, lon_max, lon_max]
        ys_4326 = [lat_min, lat_max, lat_min, lat_max]
        with rasterio.open(blue_tif) as src_b2:
            xs_c, ys_c = warp_t('EPSG:4326', src_b2.crs, xs_4326, ys_4326)
            bbox_rows, bbox_cols = [], []
            for x, y in zip(xs_c, ys_c):
                try:
                    r2, c2 = src_b2.index(x, y)
                    bbox_rows.append(r2); bbox_cols.append(c2)
                except Exception:
                    pass
        if bbox_rows:
            r_min = max(0, min(bbox_rows)); r_max = min(shape_b[0]-1, max(bbox_rows))
            c_min = max(0, min(bbox_cols)); c_max = min(shape_b[1]-1, max(bbox_cols))
            if r_min > r_max: r_min, r_max = r_max, r_min
            if c_min > c_max: c_min, c_max = c_max, c_min
            in_bbox = ((rows >= r_min) & (rows <= r_max) &
                       (cols >= c_min) & (cols <= c_max))
            rows = rows[in_bbox]
            cols = cols[in_bbox]

    print(f"    [ST] Shallow anomalies (ratio z>{shallow_threshold:.1f}): {len(rows)}")
    if len(rows) == 0:
        return []

    # Sort by ratio_z descending, take top_n
    zvals = ratio_z[rows, cols]
    sort_idx = np.argsort(-zvals)[:top_n]

    detections = []
    with rasterio.open(blue_tif) as src_b3:
        for idx in sort_idx:
            row_i = int(rows[sort_idx[np.where(sort_idx == idx)[0][0]]])
            col_i = int(cols[sort_idx[np.where(sort_idx == idx)[0][0]]])
            row_i = int(rows[idx])
            col_i = int(cols[idx])
            z_v   = float(ratio_z[row_i, col_i])
            raw_r = float(ratio[row_i, col_i])

            # Depth proxy (relative, uncalibrated but corrected for zenith)
            # Stumpf m1 coefficient for Great Lakes: empirically ~55 (Stumpf 2003 eq.2)
            # This gives relative depth in meters — not absolute without ground truth
            m1 = 55.0
            depth_raw = m1 * raw_r
            depth_corrected = depth_raw * depth_correction_factor

            local_x, local_y = src_b3.xy(row_i, col_i)
            lon_pt, lat_pt = warp_transform(crs_b, 'EPSG:4326', [local_x], [local_y])
            lat_v = lat_pt[0]; lon_v = lon_pt[0]

            known = _flag_known_wreck(lat_v, lon_v)
            line5 = _flag_line5(lat_v, lon_v)

            det = {
                'lat':   lat_v,
                'lon':   lon_v,
                'zscore': z_v,
                'type':  'stumpf_shallow',
                'source': blue_tif.name,
                'stumpf_ratio':          round(raw_r, 4),
                'depth_m_raw':           round(depth_raw, 1),
                'depth_m_corrected':     round(depth_corrected, 1),
                'solar_zenith_correction': round(depth_correction_factor, 3),
                'known_wreck_hit':   known['id']   if known else None,
                'known_wreck_name':  known['name'] if known else None,
                'line5_candidate':   line5,
                'pixel': {'row': row_i, 'col': col_i},
            }
            detections.append(det)

    return detections


def process_tiff_with_coords(tiff_path: Path, threshold: float = 1.5,
                              scan_bbox=None, top_n: int = 200,
                              cold_sink_mode: bool = False):
    """
    Process TIFF on M2200 and extract coordinates using PROPER CRS transform.
    No anchor math - uses geotransform from file metadata.
    Handles UTM zone crossovers automatically via rasterio.warp.transform

    scan_bbox:      [lat_min, lon_min, lat_max, lon_max] to restrict anomaly collection.
    top_n:          max anomaly detections to return per file (sorted highest |z| first).
    cold_sink_mode: if True, detects NEGATIVE z only (z < -threshold).
                    Use for B10 thermal — submerged steel retains cold, not heat.
                    Default False = abs(z) > threshold (both directions).
    """
    from rasterio.warp import transform as warp_transform
    
    print(f"  Loading {tiff_path.name}...")
    
    # Load with georeferencing
    with rasterio.open(tiff_path) as src:
        data = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        
        print(f"    Shape: {data.shape}, CRS: {crs}")

        # Mask NODATA/fill values & extreme outliers (ice, cloud, fill = 0 or negative DN)
        # These corrupt mean/std and produce spurious extreme z-scores
        nodata_val = src.nodata
        nodata_mask = np.zeros(data.shape, dtype=bool)
        if nodata_val is not None:
            nodata_mask |= (data == nodata_val)
        nodata_mask |= (data <= 0)  # 0-fill and negative = NODATA/ice floor
        valid_data = data[~nodata_mask]
        if valid_data.size < 100:
            print("    Skipped — insufficient valid pixels")
            return []

        # Stats on CPU (fast single pass) — computed on valid pixels only
        mean_val = float(np.mean(valid_data))
        std_val  = float(np.std(valid_data))

        if HAS_GPU:
            # GPU path: upload to M2200, compute z-score on GPU, suppress nodata pixels
            nodata_mask_gpu = cp.asarray(nodata_mask)
            data_gpu = cp.asarray(data)
            zscore_gpu = (data_gpu - mean_val) / std_val
            cp.cuda.Stream.null.synchronize()
            if cold_sink_mode:
                anomaly_mask = (zscore_gpu < -threshold) & (~nodata_mask_gpu)
            else:
                anomaly_mask = (cp.abs(zscore_gpu) > threshold) & (~nodata_mask_gpu)
            anomaly_count = int(cp.count_nonzero(anomaly_mask))
            # Move mask to CPU for index extraction
            mask_cpu = cp.asnumpy(anomaly_mask)
            zscores_all = cp.asnumpy(zscore_gpu[mask_cpu])
        else:
            # CPU path: pure NumPy, suppress nodata pixels
            zscore = (data - mean_val) / std_val
            if cold_sink_mode:
                anomaly_mask = (zscore < -threshold) & (~nodata_mask)
            else:
                anomaly_mask = (np.abs(zscore) > threshold) & (~nodata_mask)
            anomaly_count = int(np.count_nonzero(anomaly_mask))
            mask_cpu = anomaly_mask
            zscores_all = zscore[mask_cpu]
        
        print(f"    Anomalies: {anomaly_count}")

        if anomaly_count == 0:
            return []

        # Get anomaly row/col positions
        rows, cols = np.where(mask_cpu)
        # zscores_all already set in the GPU/CPU branch above

        # ── Bbox spatial filter ───────────────────────────────────────────────
        # Restrict to the scan area (e.g. Straits of Mackinac) to avoid
        # filling the top-N with land features or cloud edges in the tile margins.
        if scan_bbox is not None and len(rows) > 0:
            lat_min, lon_min, lat_max, lon_max = scan_bbox
            # Convert bbox corners to file CRS coords, then to pixel indices
            xs_4326 = [lon_min, lon_min, lon_max, lon_max]
            ys_4326 = [lat_min, lat_max, lat_min, lat_max]
            xs_crs, ys_crs = warp_transform('EPSG:4326', src.crs, xs_4326, ys_4326)
            bbox_rows, bbox_cols = [], []
            for x, y in zip(xs_crs, ys_crs):
                try:
                    r, c = src.index(x, y)
                    bbox_rows.append(r)
                    bbox_cols.append(c)
                except Exception:
                    pass
            if bbox_rows:
                r_min = max(0, min(bbox_rows))
                r_max = min(data.shape[0] - 1, max(bbox_rows))
                c_min = max(0, min(bbox_cols))
                c_max = min(data.shape[1] - 1, max(bbox_cols))
                if r_min > r_max:
                    r_min, r_max = r_max, r_min
                if c_min > c_max:
                    c_min, c_max = c_max, c_min
                in_bbox = ((rows >= r_min) & (rows <= r_max) &
                           (cols >= c_min) & (cols <= c_max))
                rows = rows[in_bbox]
                cols = cols[in_bbox]
                zscores_all = zscores_all[in_bbox]
                print(f"    In-bbox anomalies: {len(rows)}")

        if len(rows) == 0:
            return []

        # Sort by z-score magnitude, take top top_n
        sorted_idx = np.argsort(np.abs(zscores_all))[::-1][:top_n]
        
        detections = []
        for idx in sorted_idx:
            row = int(rows[idx])
            col = int(cols[idx])
            zscore = float(zscores_all[idx])
            
            # PROPER CRS-AWARE COORDINATE EXTRACTION
            # Get real-world coordinates in file's LOCAL system (meters)
            local_x, local_y = src.xy(row, col)
            
            # Transform local meters to global lat/lon (WGS84)
            # This handles UTM zone crossover automatically!
            lon, lat = warp_transform(src.crs, 'EPSG:4326', [local_x], [local_y])
            
            lat_v = lat[0]
            lon_v = lon[0]

            # Tag known wrecks
            known = _flag_known_wreck(lat_v, lon_v)
            line5 = _flag_line5(lat_v, lon_v)

            # Determine band from filename for detection type labelling
            # Handles both HLS naming (.B02., .B10., .B11.) and
            # AWS STAC naming (.blue., .green., .red., .swir16.)
            fname = tiff_path.name.upper()
            if 'B10' in fname or 'THERMAL' in fname or 'LWIR' in fname or 'ST_B10' in fname:
                det_type = 'thermal'
            elif 'B02' in fname or fname.endswith('.BLUE.TIF') or '.BLUE.' in fname:
                det_type = 'optical_blue'
            elif 'B04' in fname or fname.endswith('.RED.TIF') or '.RED.' in fname:
                det_type = 'optical_red'
            elif 'B03' in fname or fname.endswith('.GREEN.TIF') or '.GREEN.' in fname:
                det_type = 'optical_green'
            elif 'VV' in fname or 'VH' in fname:
                det_type = 'sar'
            else:
                det_type = 'optical'

            det = {
                "lat": lat_v,
                "lon": lon_v,
                "zscore": zscore,
                "type": det_type,
                "source": tiff_path.name,
                "known_wreck_hit": known["id"] if known else None,
                "known_wreck_name": known["name"] if known else None,
                "line5_candidate": line5,
                "pixel": {"row": row, "col": col},
            }
            detections.append(det)
        
        return detections


def create_kmz(detections, output_path: Path):
    """
    Create KMZ for Google Earth.

    Folders:
      Known Wreck Hits        — ground-truth calibration hits (cyan star)
      Wreck Candidates        — unknown submerged anomalies (red/orange/yellow by z-score)
      Hydrocarbon — Line 5    — SWIR dark anomaly inside Line 5 corridor (magenta)
      Hydrocarbon — Wreck Seep — HC anomaly co-located with wreck candidate (purple)
      Hydrocarbon — Unknown   — diffuse HC anomaly, source TBD (teal)
      Anchor Points           — lighthouse calibration points (green)
      Known Wreck Anchors     — documented wreck positions drawn as reference (blue)
    """
    kml = simplekml.Kml()

    # ── Known wreck reference anchors (documentation layer) ──────────────────
    known_ref_folder = kml.newfolder(name="Known Wreck Reference Positions")
    for w in KNOWN_WRECKS:
        pnt = known_ref_folder.newpoint(
            name=f"{w['name']} ({w['year_lost']})",
            coords=[(w["lon"], w["lat"])]
        )
        pnt.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/blu-blank.png"
        pnt.style.iconstyle.scale = 1.2
        pnt.description = (
            f"<b>{w['name']}</b><br/>"
            f"Year lost: {w['year_lost']}<br/>"
            f"Depth: {w['depth_ft']} ft<br/>"
            f"Type: {w['type']}<br/>"
            f"Lat: {w['lat']:.5f}  Lon: {w['lon']:.5f}<br/>"
            f"<i>Reference position — use as ground-truth calibration.</i>"
        )

    # ── Lighthouse anchor calibration points ─────────────────────────────────
    anchor_folder = kml.newfolder(name="Anchor Points (Calibration)")
    for key, anchor in ANCHOR_POINTS.items():
        pnt = anchor_folder.newpoint(
            name=anchor["name"],
            coords=[(anchor["lon"], anchor["lat"])]
        )
        pnt.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/grn-blank.png"
        pnt.style.iconstyle.scale = 1.2
        pnt.description = (
            f"<b>Calibration Anchor</b><br/>"
            f"Lat: {anchor['lat']:.6f}<br/>Lon: {anchor['lon']:.6f}"
        )

    # ── Detection folders ─────────────────────────────────────────────────────
    known_hit_folder   = kml.newfolder(name="Known Wreck HITS (confirmed ground-truth)")
    wreck_folder       = kml.newfolder(name="Wreck Candidates (unknown submerged anomaly)")
    hc_line5_folder    = kml.newfolder(name="Hydrocarbon — Line 5 Pipeline Candidate")
    hc_seep_folder     = kml.newfolder(name="Hydrocarbon — Wreck Seep Candidate")
    hc_unknown_folder  = kml.newfolder(name="Hydrocarbon — Unknown Diffuse")

    for det in detections:
        lat, lon = det["lat"], det["lon"]
        z = det.get("zscore", 0.0)
        z_abs = abs(z)
        det_type = det.get("type", "optical")
        is_hc   = det_type == "hydrocarbon"
        known   = det.get("known_wreck_hit")
        line5   = det.get("line5_candidate", False)
        area_px = det.get("area_pixels", "n/a")
        hc_sub  = det.get("hc_subtype", "")

        # Build description
        desc = (
            f"<b>{'KNOWN WRECK HIT' if known else ('HYDROCARBON ANOMALY' if is_hc else 'WRECK CANDIDATE')}</b><br/>"
            f"Type: {det_type}<br/>"
            f"Z-Score: {z:.3f}<br/>"
            f"Source: {det['source']}<br/>"
            + (f"Area: {area_px} pixels<br/>" if is_hc else "")
            + (f"<b>KNOWN WRECK: {det.get('known_wreck_name')}</b><br/>" if known else "")
            + (f"<b style='color:magenta'>LINE 5 PIPELINE CORRIDOR</b><br/>" if line5 else "")
            + (f"HC Subtype: {hc_sub}<br/>" if hc_sub else "")
            + f"<br/><b>Coordinates (WGS84):</b><br/>"
            + f"Lat: {lat:.6f}<br/>Lon: {lon:.6f}<br/>"
            + f"<i>CRS-aware geotransform applied.</i>"
        )

        # ── Route to correct folder + pick icon/colour ─────────────────────
        if known:
            icon  = "http://maps.google.com/mapfiles/kml/paddle/wht-stars.png"
            color = simplekml.Color.cyan
            folder = known_hit_folder
            label = f"KNOWN HIT: {det.get('known_wreck_name','?')} Z={z:.2f}"
        elif is_hc:
            if "LINE5" in hc_sub:
                icon   = "http://maps.google.com/mapfiles/kml/paddle/pink-blank.png"
                color  = simplekml.Color.fuchsia
                folder = hc_line5_folder
            elif "SEEP" in hc_sub:
                icon   = "http://maps.google.com/mapfiles/kml/paddle/purple-blank.png"
                color  = simplekml.Color.purple
                folder = hc_seep_folder
            else:
                icon   = "http://maps.google.com/mapfiles/kml/paddle/ltblu-blank.png"
                color  = simplekml.Color.lightblue
                folder = hc_unknown_folder
            label = f"HC {hc_sub} Z={z:.2f}"
        else:
            # Wreck candidate — colour by z-score magnitude
            if z_abs > 4.0:
                icon  = "http://maps.google.com/mapfiles/kml/paddle/red-stars.png"
                color = simplekml.Color.red
            elif z_abs > 3.0:
                icon  = "http://maps.google.com/mapfiles/kml/paddle/orange-circle.png"
                color = simplekml.Color.orange
            else:
                icon  = "http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png"
                color = simplekml.Color.yellow
            folder = wreck_folder
            label = f"{det_type.upper()} Z={z:.2f}"

        pnt = folder.newpoint(name=label, coords=[(lon, lat)])
        pnt.style.iconstyle.icon.href = icon
        pnt.style.iconstyle.color = color
        pnt.description = desc

    kml.save(str(output_path))
    print(f"\n[OK] KMZ saved: {output_path}")


def main():
    print("="*80)
    print("FULL LAKE MICHIGAN SCAN - M2200 CUDA TO KMZ")
    print("="*80)
    print()
    
    # Find ALL TIFFs
    # Cross-platform data paths — use env var, Syncthing folder, or current dir
    data_base = Path(os.environ.get('CESAROPS_DATA_DIR', Path(__file__).parent / 'data'))
    repo_root = Path(__file__).parent
    search_paths = [
        data_base,                                              # direct CESAROPS_DATA_DIR
        data_base / 'rossa_forensic_cache',
        data_base / 'sentinel_hunt_cache',
        data_base / 'bagrecovery' / 'outputs' / 'rossa_forensic_cache',
        data_base / 'bagrecovery' / 'sentinel_hunt' / 'cache',
        # Local downloads tree (all lakes, all years, recursive)
        repo_root / 'downloads' / 'hls',
        repo_root / 'downloads' / 'michigan',
        repo_root / 'downloads' / 'superior',
        repo_root / 'downloads' / 'straits',
        repo_root / 'downloads' / 'huron',
        repo_root / 'downloads' / 'erie',
        repo_root / 'downloads' / 'ontario',
        # Fallback: any 'data' or 'outputs' subdirectory in parent dirs
        Path(__file__).parent.parent / 'cesarops-data' / 'tiffs',
    ]
    
    tiffs = []
    for search_path in search_paths:
        if search_path.exists():
            tiffs.extend(search_path.rglob("*.tif"))
    
    tiffs = sorted(set(tiffs))
    
    print(f"Found {len(tiffs)} TIFFs")
    print(f"Processing on M2200 CUDA cores...")
    print()

    all_detections = []

    # Straits of Mackinac scan bbox — constrains anomaly selection to water area
    # [lat_min, lon_min, lat_max, lon_max]
    # Extended west to -84.90 to include Eber Ward (-84.819) and Sandusky (-84.837)
    STRAITS_BBOX = [45.70, -84.90, 46.05, -84.10]

    # ── PASS 1: Standard anomaly scan (thermal / optical / SAR) ──────────────
    # Notes:
    # - B11 / swir16 → dedicated HC pass below
    # - B10 (thermal LWIR) kept in standard pass
    # - NIR / nir08 (B08/B8A) don't penetrate water — skip
    # - scl / qa_pixel / Fmask → quality masks, skip
    # - swir22 → second SWIR, skip in standard pass
    _SKIP_UPPER = {'FMASK', '.B11.', '.SWIR16.', '.SWIR22.',
                   '.SCL.', '.QA_PIXEL.', '.NIR08.', '.NIR.'}
    standard_tiffs = [t for t in tiffs if
                      not any(tag in t.name.upper() for tag in _SKIP_UPPER)]

    print(f"PASS 1 — Standard anomaly scan ({len(standard_tiffs)} bands, threshold=band-adaptive)")
    print("-"*60)
    for i, tiff in enumerate(standard_tiffs, 1):
        print(f"[{i}/{len(standard_tiffs)}] {tiff.name}")
        tname_upper = tiff.name.upper()
        # Band-adaptive thresholds:
        #   B10 thermal → cold-sink only (submerged steel retains cold), z < -2.0
        #   B02 blue    → 1.2 (deep penetration in Lake Michigan clarity up to ~55m/180ft)
        #   all others  → 1.5 default
        is_thermal = ('B10' in tname_upper or 'THERMAL' in tname_upper or
                      'LWIR' in tname_upper or 'ST_B10' in tname_upper)
        is_blue    = ('.B02.' in tname_upper or '.BLUE.' in tname_upper)
        if is_thermal:
            band_thresh, cold_sink = 2.0, True
        elif is_blue:
            band_thresh, cold_sink = 1.2, False
        else:
            band_thresh, cold_sink = 1.5, False
        try:
            detections = process_tiff_with_coords(
                tiff, threshold=band_thresh, scan_bbox=STRAITS_BBOX,
                top_n=200, cold_sink_mode=cold_sink)
            all_detections.extend(detections)
        except Exception as e:
            print(f"    ERROR: {e}")
        print()

    # ── PASS 2: Hydrocarbon scan (B11 SWIR dark + B04 Red cross-check) ───────
    # HC SWIR band — HLS naming (.B11.) or AWS STAC naming (.swir16.)
    b11_tiffs = [t for t in tiffs if
                 ('.B11.' in t.name.upper() or '.SWIR16.' in t.name.upper()) and
                 'FMASK' not in t.name.upper() and
                 '.SCL.' not in t.name.upper()]
    print()
    print(f"PASS 2 — Hydrocarbon / oil-slick scan ({len(b11_tiffs)} B11 SWIR scenes)")
    print("-"*60)
    for b11_path in b11_tiffs:
        # Companion Red band — resolve for both HLS and AWS STAC naming
        pname = b11_path.name.lower()
        if '.swir16.tif' in pname:
            b04_path = Path(str(b11_path).replace('.swir16.tif', '.red.tif'))
        else:
            b04_path = Path(str(b11_path).replace('.B11.tif', '.B04.tif'))
        try:
            hc_detections = process_hydrocarbon_bands(b11_path, b04_path)
            all_detections.extend(hc_detections)
        except ImportError:
            print("    [HC] scipy not available — skipping blob labelling (pip install scipy)")
        except Exception as e:
            print(f"    [HC] ERROR: {e}")

    # ── PASS 3: Stumpf log-ratio bathymetric shallow-anomaly scan ────────────
    # Pairs blue (.B02. or .blue.) with green (.B03. or .green.) from the same
    # granule.  Applies solar zenith correction from .geometry.json sidecar
    # written by tile_geometry.py (run once after download).
    blue_tiffs = [t for t in tiffs if
                  ('.B02.' in t.name.upper() or '.BLUE.' in t.name.upper()) and
                  'FMASK' not in t.name.upper() and
                  '.SCL.' not in t.name.upper()]
    print()
    print(f"PASS 3 — Stumpf bathymetric shallow-anomaly scan ({len(blue_tiffs)} blue bands)")
    print("-"*60)
    for blue_path in blue_tiffs:
        pname = blue_path.name.lower()
        if '.blue.tif' in pname:
            green_path = Path(str(blue_path).replace('.blue.tif', '.green.tif'))
        elif '.B02.tif' in blue_path.name:
            green_path = Path(str(blue_path).replace('.B02.tif', '.B03.tif'))
        else:
            green_path = Path(str(blue_path).lower().replace('b02', 'b03').replace('.blue.', '.green.'))
            green_path = Path(str(blue_path).replace('B02', 'B03').replace('blue', 'green'))
        try:
            st_detections = compute_stumpf_pass(blue_path, green_path,
                                                 scan_bbox=STRAITS_BBOX)
            all_detections.extend(st_detections)
        except Exception as e:
            print(f"    [ST] ERROR: {e}")

    # ── PASS 4: NauticUVs LoG blob scan ──────────────────────────────────────
    # Multi-scale Laplacian-of-Gaussian blob detection on B02 (surface disturbance /
    # subsurface reflectance) and B10 thermal (cold plume over submerged steel).
    # Approximates FDCT curvelet energy from NauticUVs (schema: nauticuvs_* columns).
    # Detects hull-scale features (45-120m / 1.5-4 px at 30m) even when hull is below
    # the optical floor — the upwelling/cold-plume surface expression is in range.
    try:
        from scipy.ndimage import gaussian_laplace  # noqa — just test availability
        nuv_bands = (
            [t for t in tiffs if ('.B02.' in t.name.upper() or '.BLUE.' in t.name.upper())
             and 'FMASK' not in t.name.upper() and '.SCL.' not in t.name.upper()] +
            [t for t in tiffs if ('B10' in t.name.upper() or 'THERMAL' in t.name.upper()
             or 'LWIR' in t.name.upper())
             and 'FMASK' not in t.name.upper() and '.SCL.' not in t.name.upper()]
        )
        print()
        print(f"PASS 4 — NauticUVs LoG blob scan ({len(nuv_bands)} bands: B02+B10)")
        print("-"*60)
        for nuv_tif in nuv_bands:
            print(f"  {nuv_tif.name}")
            try:
                nuv_dets = compute_nauticuvs_pass(nuv_tif, scan_bbox=STRAITS_BBOX)
                all_detections.extend(nuv_dets)
            except Exception as e:
                print(f"    [NUV] ERROR: {e}")
    except ImportError:
        print()
        print("PASS 4 — NauticUVs LoG SKIPPED (pip install scipy)")

    # ── Summary ───────────────────────────────────────────────────────────────
    wreck_hits   = [d for d in all_detections if d.get("known_wreck_hit")]
    hc_hits      = [d for d in all_detections if d.get("type") == "hydrocarbon"]
    stumpf_hits  = [d for d in all_detections if d.get("type") == "stumpf_shallow"]
    nuv_hits     = [d for d in all_detections if d.get("type") == "nauticuvs_candidate"]
    line5_hits   = [d for d in all_detections if d.get("line5_candidate")]
    unknown_wreck = [d for d in all_detections if d.get("type") not in
                     ("hydrocarbon", "stumpf_shallow", "nauticuvs_candidate")
                     and not d.get("known_wreck_hit")]

    print()
    print("="*80)
    print("SCAN SUMMARY")
    print("="*80)
    print(f"  Total detections           : {len(all_detections)}")
    print(f"  Known wreck hits           : {len(wreck_hits)}  ← ground-truth calibration")
    print(f"  Unknown wreck candidates   : {len(unknown_wreck)}")
    print(f"  Hydrocarbon anomalies      : {len(hc_hits)}")
    print(f"  Stumpf shallow anomalies   : {len(stumpf_hits)}  ← zenith-corrected depth proxy")
    print(f"  NauticUVs LoG candidates   : {len(nuv_hits)}  ← hull-scale surface disturbance")
    print(f"  Line 5 corridor flags      : {len(line5_hits)}")
    if wreck_hits:
        print()
        print("  KNOWN WRECK HIT DETAILS:")
        for d in wreck_hits:
            print(f"    {d['known_wreck_name']}  Z={d['zscore']:.2f}  ({d['lat']:.5f}, {d['lon']:.5f})")
    if hc_hits:
        print()
        print("  HYDROCARBON ANOMALY DETAILS:")
        for d in hc_hits:
            print(f"    {d.get('hc_subtype','?')}  area={d.get('area_pixels','?')}px  "
                  f"Z={d['zscore']:.2f}  ({d['lat']:.5f}, {d['lon']:.5f})")
    print("="*80)

    # Save JSON
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    scan_tag = os.environ.get('CESAROPS_SCAN_TAG', 'straits_mackinac')
    json_file = output_dir / f"{scan_tag}_scan_{timestamp}.json"
    with open(json_file, 'w') as f:
        json.dump({
            "scan_area": scan_tag,
            "generated": timestamp,
            "total_detections": len(all_detections),
            "known_wreck_hits": len(wreck_hits),
            "unknown_wreck_candidates": len(unknown_wreck),
            "hydrocarbon_anomalies": len(hc_hits),
            "stumpf_shallow": len(stumpf_hits),
            "nauticuvs_candidates": len(nuv_hits),
            "line5_flags": len(line5_hits),
            "detections": all_detections,
        }, f, indent=2)
    print(f"[OK] JSON saved: {json_file}")

    # Create KMZ
    kmz_file = output_dir / f"{scan_tag}_scan_{timestamp}.kmz"
    create_kmz(all_detections, kmz_file)

    print()
    print("="*80)
    print("SCAN COMPLETE — Open KMZ in Google Earth Pro")
    print("Layers: Known Wreck Hits | Wreck Candidates | HC Line5 | HC Seep | HC Unknown")
    print("="*80)

if __name__ == "__main__":
    main()
