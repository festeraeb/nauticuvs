#!/usr/bin/env python3
"""
wreck_ml_predictor.py — Score scan detections with the trained wreck classifier.

Given a scan output JSON from lake_michigan_scan.py, this script:
  1. Loads the trained model from models/wreck_classifier.pkl
  2. For each scan detection (lat/lon), finds the closest HLS tile
  3. Extracts the same 17×17 feature vector used during training
  4. Scores each candidate with the model's probability estimate
  5. Writes an annotated JSON + filtered KMZ with ML confidence scores

Usage:
    python wreck_ml_predictor.py                        # auto-find latest scan JSON
    python wreck_ml_predictor.py --scan outputs/xyz.json
    python wreck_ml_predictor.py --threshold 0.5        # min ML score to include
    python wreck_ml_predictor.py --scan-all             # score all tiles as raster scan
"""

import argparse
import json
import pickle
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from scipy.ndimage import gaussian_laplace
from scipy.stats import skew, kurtosis

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

ROOT       = Path(__file__).parent
MODELS_DIR = ROOT / "models"
DOWNLOADS  = ROOT / "downloads"
OUTPUTS    = ROOT / "outputs"

MODEL_PKL  = MODELS_DIR / "wreck_classifier.pkl"
MODEL_META = MODELS_DIR / "wreck_classifier_meta.json"

# Shared constants (must match wreck_ml_trainer.py)
PATCH_HALF = 8
BAND_NAMES = ["blue", "green", "red", "swir16"]
BAND_HLS   = ["B02",  "B03",   "B04", "B11"]
LOG_SIGMA  = 2.0


# ── Load model ───────────────────────────────────────────────────────────────

def load_model():
    if not MODEL_PKL.exists():
        raise FileNotFoundError(
            f"No model found at {MODEL_PKL}\nRun: python wreck_ml_trainer.py first"
        )
    with open(MODEL_PKL, "rb") as f:
        model = pickle.load(f)
    meta = json.loads(MODEL_META.read_text()) if MODEL_META.exists() else {}
    feat_names = meta.get("feature_names", [])
    print(f"[predict] Model loaded: {MODEL_PKL.name}")
    print(f"[predict] Features: {len(feat_names)}")
    return model, feat_names, meta


# ── Tile utilities (shared with trainer) ─────────────────────────────────────

def tile_band_dict(tile_dir: Path) -> dict[str, Path]:
    out = {}
    for tif in sorted(tile_dir.glob("*.tif")):
        n = tif.name.upper()
        for stac, hls in zip(BAND_NAMES, BAND_HLS):
            if f".{hls}." in n or f".{stac.upper()}." in n:
                out.setdefault(stac, tif)
    return out


def latlon_to_pixel(tif: rasterio.DatasetReader, lat: float, lon: float):
    x, y = warp_transform("EPSG:4326", tif.crs, [lon], [lat])
    try:
        r, c = tif.index(x[0], y[0])
    except Exception:
        return None
    if 0 <= r < tif.height and 0 <= c < tif.width:
        return r, c
    return None


def extract_patch(data: np.ndarray, row: int, col: int):
    r0, r1 = row - PATCH_HALF, row + PATCH_HALF + 1
    c0, c1 = col - PATCH_HALF, col + PATCH_HALF + 1
    if r0 < 0 or c0 < 0 or r1 > data.shape[0] or c1 > data.shape[1]:
        return None
    patch = data[r0:r1, c0:c1].copy()
    return patch if patch.shape == (PATCH_HALF*2+1, PATCH_HALF*2+1) else None


def patch_features(patch: np.ndarray, name: str) -> dict:
    p = patch.flatten()
    p_valid = p[np.isfinite(p) & (p > 0)]
    if p_valid.size < 4:
        p_valid = np.array([0.0])
    feat = {
        f"{name}_mean":     float(np.nanmean(p_valid)),
        f"{name}_std":      float(np.nanstd(p_valid)),
        f"{name}_min":      float(np.nanmin(p_valid)),
        f"{name}_max":      float(np.nanmax(p_valid)),
        f"{name}_p10":      float(np.nanpercentile(p_valid, 10)),
        f"{name}_p90":      float(np.nanpercentile(p_valid, 90)),
        f"{name}_skew":     float(skew(p_valid)) if p_valid.size >= 4 else 0.0,
        f"{name}_kurtosis": float(kurtosis(p_valid)) if p_valid.size >= 4 else 0.0,
    }
    finite_patch = np.where(np.isfinite(patch) & (patch > 0), patch, 0.0).astype(np.float32)
    if finite_patch.max() > 0:
        finite_patch /= finite_patch.max()
    log_resp = gaussian_laplace(finite_patch, sigma=LOG_SIGMA)
    feat[f"{name}_log_energy"] = float(np.sum(log_resp ** 2))
    return feat


def cross_band_features(patches: dict) -> dict:
    feat = {}
    blue  = patches.get("blue")
    green = patches.get("green")
    red   = patches.get("red")
    swir  = patches.get("swir16")

    if blue is not None and green is not None:
        valid = (blue > 0.001) & (green > 0.001) & np.isfinite(blue) & np.isfinite(green)
        if np.count_nonzero(valid) > 4:
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.log(blue[valid]) / np.log(green[valid])
            feat["stumpf_mean"] = float(np.nanmean(ratio))
            feat["stumpf_std"]  = float(np.nanstd(ratio))
        else:
            feat["stumpf_mean"] = float("nan")
            feat["stumpf_std"]  = float("nan")
        from scipy.ndimage import sobel
        norm_b = blue.astype(np.float32)
        if norm_b.max() > 0: norm_b /= norm_b.max()
        gx = sobel(norm_b, axis=1); gy = sobel(norm_b, axis=0)
        feat["blue_grad_mean"] = float(np.nanmean(np.hypot(gx, gy)))

    if swir is not None and red is not None:
        valid = (swir > 0) & (red > 0) & np.isfinite(swir) & np.isfinite(red)
        feat["swir_red_ratio"] = float(np.nanmean(swir[valid] / red[valid])) if np.count_nonzero(valid) > 4 else float("nan")

    if blue is not None and red is not None:
        valid = (blue > 0) & (red > 0) & np.isfinite(blue) & np.isfinite(red)
        feat["blue_red_ratio"] = float(np.nanmean(blue[valid] / red[valid])) if np.count_nonzero(valid) > 4 else float("nan")

    return feat


# ── Feature vector builder ────────────────────────────────────────────────────

def build_tile_bbox_cache(tile_dirs: dict) -> dict:
    """
    Pre-compute WGS84 bounding boxes for all tile directories.
    Returns {tile_dir: (lat_min, lat_max, lon_min, lon_max)} or skips on error.
    """
    cache = {}
    for tile_dir, bands in tile_dirs.items():
        ref = next((bands[b] for b in ["blue", "green", "red"] if b in bands), None)
        if ref is None:
            continue
        try:
            with rasterio.open(ref) as src:
                bounds = src.bounds
                xs, ys = warp_transform(src.crs, "EPSG:4326",
                                        [bounds.left, bounds.right],
                                        [bounds.bottom, bounds.top])
                cache[tile_dir] = (min(ys), max(ys), min(xs), max(xs))
        except Exception:
            pass
    return cache


def features_for_latlon(lat: float, lon: float,
                        tile_dirs: dict,
                        tile_bboxes: dict | None = None) -> dict | None:
    """
    Find the best tile covering (lat, lon), extract features.
    Returns feature dict or None if no tile found.
    tile_bboxes: optional pre-computed WGS84 bbox cache from build_tile_bbox_cache()
    """
    for tile_dir, bands in tile_dirs.items():
        # Fast bbox skip if cache available
        if tile_bboxes and tile_dir in tile_bboxes:
            lat_min, lat_max, lon_min, lon_max = tile_bboxes[tile_dir]
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue
        if not bands:
            continue
        ref_band_name = next((b for b in ["blue", "green", "red", "swir16"] if b in bands), None)
        if ref_band_name is None:
            continue
        ref_tif_path = bands[ref_band_name]

        with rasterio.open(ref_tif_path) as ref:
            rc = latlon_to_pixel(ref, lat, lon)
            if rc is None:
                continue
            r, c = rc

            # Load all band data
            band_data: dict[str, np.ndarray] = {}
            for bname, bpath in bands.items():
                try:
                    with rasterio.open(bpath) as src:
                        bd = src.read(1).astype(np.float32)
                    if float(np.nanmedian(bd[bd > 0])) > 1.5:
                        bd = bd * 0.0001
                    bd[bd <= 0] = float("nan")
                    band_data[bname] = bd
                except Exception:
                    continue

            patches = {}
            for bname, bd in band_data.items():
                p = extract_patch(bd, r, c)
                if p is not None:
                    patches[bname] = p

            if len(patches) < 2:
                continue

            feat = {}
            for bname, p in patches.items():
                feat.update(patch_features(p, bname))
            feat.update(cross_band_features(patches))
            feat["_tile"] = str(tile_dir)
            return feat

    return None


def feat_dict_to_vector(feat: dict, feat_names: list[str]) -> np.ndarray:
    """Convert feature dict to ordered numpy vector matching model's feature_names."""
    row = [feat.get(k, float("nan")) for k in feat_names]
    x = np.array(row, dtype=np.float32)
    # NaN impute with 0 (model was trained with median imputation — 0 is safe fallback)
    x = np.where(np.isfinite(x), x, 0.0)
    return x.reshape(1, -1)


# ── Score scan detections ─────────────────────────────────────────────────────

def find_latest_scan_json() -> Path | None:
    scan_files = sorted(OUTPUTS.glob("straits_*_scan_*.json"), reverse=True)
    if not scan_files:
        scan_files = sorted(OUTPUTS.glob("*_scan_*.json"), reverse=True)
    return scan_files[0] if scan_files else None


def score_scan_detections(scan_json: Path, model, feat_names: list[str],
                          tile_dirs: dict, threshold: float = 0.3,
                          tile_bboxes: dict | None = None) -> list[dict]:
    """
    Load scan JSON, score each detection with the ML model.
    Returns list of detections with 'ml_score' added.
    tile_bboxes: optional pre-computed bbox cache for fast tile lookup.
    """
    data = json.loads(scan_json.read_text(encoding="utf-8"))
    detections = data.get("detections", [])
    print(f"[predict] Scoring {len(detections)} scan detections from {scan_json.name}")

    scored = []
    skipped = 0
    for i, det in enumerate(detections):
        lat = det.get("lat") or det.get("latitude")
        lon = det.get("lon") or det.get("longitude")
        if lat is None or lon is None:
            skipped += 1
            continue

        feat = features_for_latlon(float(lat), float(lon), tile_dirs, tile_bboxes)
        if feat is None:
            det["ml_score"] = None
            det["ml_note"] = "no_tile_coverage"
            scored.append(det)
            continue

        x = feat_dict_to_vector(feat, feat_names)
        try:
            prob = float(model.predict_proba(x)[0, 1])
        except Exception as e:
            prob = -1.0
            det["ml_note"] = f"error: {e}"

        det["ml_score"] = round(prob, 4)
        det["ml_tile"] = feat.get("_tile", "")
        if prob >= threshold:
            det["ml_flag"] = "WRECK_CANDIDATE"
        scored.append(det)

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(detections)}] scored...")

    # Sort by ml_score descending
    scored.sort(key=lambda d: (d.get("ml_score") or -1), reverse=True)

    above = sum(1 for d in scored if (d.get("ml_score") or 0) >= threshold)
    print(f"\n[predict] Results: {above} detections ≥ {threshold:.2f} ML score")
    print(f"[predict] (Skipped {skipped} with missing coordinates)")
    return scored


# ── KMZ writer ────────────────────────────────────────────────────────────────

def write_scored_kmz(detections: list[dict], out_path: Path, threshold: float = 0.3):
    """Write a KMZ with color-coded ML scores."""
    # Color: high score = red, medium = yellow, low = green
    def score_color(s):
        if s is None: return "ff888888"  # grey = no coverage
        if s >= 0.7:  return "ff0000ff"  # red = high confidence
        if s >= 0.4:  return "ff00aaff"  # orange = moderate
        return "ff00ff00"                # green = low

    placemarks = []
    for d in detections:
        lat = d.get("lat") or d.get("latitude") or 0
        lon = d.get("lon") or d.get("longitude") or 0
        score = d.get("ml_score")
        score_str = f"{score:.3f}" if score is not None else "N/A"
            name = d.get("known_wreck_name") or d.get("name") or f"Det@{lat:.4f},{lon:.4f}"
        color = score_color(score)

        placemark = f"""  <Placemark>
    <name>{name}</name>
    <description><![CDATA[
      ML Score: {score_str}<br/>
      Z-Score: {d.get('zscore', 'N/A')}<br/>
      Type: {d.get('type', '')}<br/>
      Source: {d.get('source', '')}<br/>
      Known Wreck: {d.get('known_wreck_name') or 'none'}<br/>
      Lat: {lat:.6f}  Lon: {lon:.6f}
    ]]></description>
    <Style><IconStyle>
      <color>{color}</color>
      <scale>1.0</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png</href></Icon>
    </IconStyle></Style>
    <Point><coordinates>{lon},{lat},0</coordinates></Point>
  </Placemark>"""
        placemarks.append(placemark)

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>ML-Scored Wreck Detections</name>
  <description>CESAROPS v3 + ML Confidence Scoring (threshold={threshold})</description>
{''.join(placemarks)}
</Document>
</kml>"""

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml.encode("utf-8"))
    print(f"[predict] KMZ written: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Score scan detections with ML classifier")
    ap.add_argument("--scan",      default=None,  help="Scan JSON to score (default: latest)")
    ap.add_argument("--threshold", type=float, default=0.3,
                    help="Min ML score to flag as wreck candidate (default 0.3)")
    ap.add_argument("--out",       default=None,  help="Output JSON path (default: auto)")
    args = ap.parse_args()

    # Load model
    model, feat_names, meta = load_model()
    if meta.get("patch_half"):
        global PATCH_HALF, LOG_SIGMA
        PATCH_HALF = meta["patch_half"]
        LOG_SIGMA  = meta.get("log_sigma", LOG_SIGMA)

    # Locate scan JSON
    scan_json = Path(args.scan) if args.scan else find_latest_scan_json()
    if scan_json is None or not scan_json.exists():
        print("No scan JSON found. Run lake_michigan_scan.py first.")
        sys.exit(1)
    print(f"[predict] Scoring: {scan_json}")

    # Index all tile directories once
    print("[predict] Indexing tile directories...")
    tile_dirs: dict[Path, dict] = {}
    for tif in sorted(DOWNLOADS.rglob("*.tif")):
        d = tif.parent
        if d not in tile_dirs:
            tile_dirs[d] = tile_band_dict(d)
    print(f"[predict] Found {len(tile_dirs)} tile directories")

    # Pre-compute bbox cache for fast tile lookup (avoids 1M+ rasterio opens)
    print("[predict] Building tile bbox cache...")
    tile_bboxes = build_tile_bbox_cache(tile_dirs)
    print(f"[predict] Bbox cache: {len(tile_bboxes)} tiles")

    # Score
    scored = score_scan_detections(scan_json, model, feat_names, tile_dirs, args.threshold, tile_bboxes)

    # Save annotated JSON
    out_json = Path(args.out) if args.out else OUTPUTS / (scan_json.stem + "_ml_scored.json")
    out_json.write_text(json.dumps(scored, indent=2), encoding="utf-8")
    print(f"[predict] Scored JSON: {out_json}")

    # Save KMZ
    out_kmz = out_json.with_suffix(".kmz")
    write_scored_kmz(scored, out_kmz, args.threshold)

    # Print top candidates
    top = [d for d in scored if (d.get("ml_score") or 0) >= args.threshold][:20]
    if top:
        print(f"\n[predict] Top {len(top)} wreck candidates:")
        print(f"  {'Name/ID':<35} {'ML':>5}  {'Z':>6}  Lat/Lon")
        print("  " + "-"*70)
        for d in top:
            lat = d.get("lat") or d.get("latitude") or 0
            lon = d.get("lon") or d.get("longitude") or 0
            name = (d.get("known_wreck_name") or d.get("name") or "unknown")[:34]
            score = d.get("ml_score", 0) or 0
            z = d.get("zscore", "?")
            print(f"  {name:<35} {score:>5.3f}  {str(z):>6.2f}  {lat:.4f},{lon:.4f}")
    else:
        print(f"\n[predict] No candidates above threshold {args.threshold}")
        print("[predict] Try --threshold 0.1 for lower cutoff")


if __name__ == "__main__":
    main()
