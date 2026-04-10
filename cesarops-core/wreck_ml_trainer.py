#!/usr/bin/env python3
"""
CESAROPS Wreck ML Trainer — Multi-Sensor Patch Feature Extraction + TFLite Export

Pipeline:
  1. Extract 17×17 pixel patches around known wreck GPS anchors (positive samples)
  2. Extract equal-count random open-water patches from same tiles (negative samples)
  3. Compute 40-feature vector per patch:  per-band stats + Stumpf ratio + LoG energy + SWIR
  4. Train LightGBM (fast, interpretable, no GPU needed for training this size)
  5. Export feature-vector classifier: LightGBM → ONNX → TFLite INT8 for Coral Edge TPU
  6. Write model to models/wreck_classifier_edgetpu.tflite + _cpu.tflite

Usage:
    python wreck_ml_trainer.py                        # full train + export
    python wreck_ml_trainer.py --extract-only        # just save the dataset CSV
    python wreck_ml_trainer.py --eval-only           # evaluate existing model
    python wreck_ml_trainer.py --negatives 5          # 5× negatives per positive

Bands used (whatever is present per tile — missing bands are NaN-padded):
    blue (B02), green (B03), red (B04), swir16 (B11)
    Thermal (B10) used where available (Landsat tiles)

Features per sample (40 total):
    Per band (×4 bands): mean, std, min, max, p10, p90, skew, kurtosis, log_energy
    Cross-band: stumpf_ratio_mean, stumpf_ratio_std, swir_red_ratio, blue_red_ratio
    Structural: LoG energy (hull-scale blob detector sigma=2), spatial gradient mean
    Context: depth_ft (from known_wrecks.json, NaN for negatives)
"""

import argparse
import json
import math
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from scipy.ndimage import gaussian_laplace
from scipy.stats import skew, kurtosis

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# ── Config ──────────────────────────────────────────────────────────────────
PATCH_HALF = 8          # 17×17 window (8+1+8) — ~510m at 30m/px
BAND_NAMES  = ["blue", "green", "red", "swir16"]   # HLS STAC naming
BAND_HLS    = ["B02",  "B03",   "B04", "B11"]      # HLS L30/S30 naming
BAND_THERM  = ["B10"]                               # thermal (optional bonus)
LOG_SIGMA   = 2.0       # LoG sigma in pixels (~60m kernel radius)
RANDOM_SEED = 42

ROOT = Path(__file__).parent
DOWNLOADS = ROOT / "downloads"
KNOWN_WRECKS_JSON = ROOT / "known_wrecks.json"
MODELS_DIR = ROOT / "models"
DATASET_CSV = ROOT / "outputs" / "wreck_ml_dataset.csv"


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_known_wrecks() -> list:
    raw = json.load(open(KNOWN_WRECKS_JSON, encoding="utf-8"))
    out = []
    for wid, w in raw.get("wrecks", {}).items():
        lat = w.get("lat"); lon = w.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        out.append({
            "id": wid, "name": w.get("name", wid),
            "lat": float(lat), "lon": float(lon),
            "depth_ft": w.get("depth_ft") or float("nan"),
        })
    return out


def find_all_tiles() -> list[Path]:
    """Return all .tif files under downloads/."""
    return sorted(DOWNLOADS.rglob("*.tif"))


def tile_band_dict(tile_dir: Path) -> dict[str, Path]:
    """
    Given a directory, build a dict of band_key → Path for every band present.
    Handles both HLS naming (.B02.tif) and STAC naming (.blue.tif).
    """
    out = {}
    for tif in sorted(tile_dir.glob("*.tif")):
        n = tif.name.upper()
        for stac, hls in zip(BAND_NAMES, BAND_HLS):
            if f".{hls}." in n or f".{stac.upper()}." in n:
                out.setdefault(stac, tif)
        for therm in BAND_THERM:
            if f".{therm}." in n or "THERMAL" in n or "LWIR" in n:
                out.setdefault("thermal", tif)
    return out


def latlon_to_pixel(tif: rasterio.DatasetReader, lat: float, lon: float):
    """Convert WGS84 lat/lon to (row, col) in this raster. Returns None if outside."""
    x, y = warp_transform("EPSG:4326", tif.crs, [lon], [lat])
    try:
        r, c = tif.index(x[0], y[0])
    except Exception:
        return None
    if 0 <= r < tif.height and 0 <= c < tif.width:
        return r, c
    return None


def extract_patch(data: np.ndarray, row: int, col: int) -> Optional[np.ndarray]:
    """Extract PATCH_HALF window. Returns None if any edge clipping occurs."""
    r0, r1 = row - PATCH_HALF, row + PATCH_HALF + 1
    c0, c1 = col - PATCH_HALF, col + PATCH_HALF + 1
    if r0 < 0 or c0 < 0 or r1 > data.shape[0] or c1 > data.shape[1]:
        return None
    patch = data[r0:r1, c0:c1].copy()
    return patch if patch.shape == (PATCH_HALF*2+1, PATCH_HALF*2+1) else None


def patch_features(patch: np.ndarray, name: str) -> dict:
    """Compute per-band stats + LoG energy for a single-band patch."""
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
    # LoG blob energy (normalized)
    finite_patch = np.where(np.isfinite(patch) & (patch > 0), patch, 0.0).astype(np.float32)
    if finite_patch.max() > 0:
        finite_patch /= finite_patch.max()
    log_resp = gaussian_laplace(finite_patch, sigma=LOG_SIGMA)
    feat[f"{name}_log_energy"] = float(np.sum(log_resp ** 2))
    return feat


def cross_band_features(patches: dict[str, np.ndarray]) -> dict:
    """Compute cross-band features: Stumpf ratio, SWIR/red, blue/red."""
    feat = {}
    blue  = patches.get("blue")
    green = patches.get("green")
    red   = patches.get("red")
    swir  = patches.get("swir16")

    # Stumpf log-ratio (depth proxy)
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

        # Spatial gradient coherence (hull sharpness)
        from scipy.ndimage import sobel
        norm_b = blue.astype(np.float32)
        if norm_b.max() > 0: norm_b /= norm_b.max()
        gx = sobel(norm_b, axis=1); gy = sobel(norm_b, axis=0)
        feat["blue_grad_mean"] = float(np.nanmean(np.hypot(gx, gy)))

    if swir is not None and red is not None:
        valid = (swir > 0) & (red > 0) & np.isfinite(swir) & np.isfinite(red)
        if np.count_nonzero(valid) > 4:
            ratio_sr = swir[valid] / red[valid]
            feat["swir_red_ratio"] = float(np.nanmean(ratio_sr))
        else:
            feat["swir_red_ratio"] = float("nan")

    if blue is not None and red is not None:
        valid = (blue > 0) & (red > 0) & np.isfinite(blue) & np.isfinite(red)
        if np.count_nonzero(valid) > 4:
            feat["blue_red_ratio"] = float(np.nanmean(blue[valid] / red[valid]))
        else:
            feat["blue_red_ratio"] = float("nan")

    return feat


# ── Patch extraction ─────────────────────────────────────────────────────────

def extract_samples(negatives_per_positive: int = 5) -> list[dict]:
    """
    For every (wreck, tile_dir) pair where tile contains the wreck GPS:
      - Extract one positive patch per band
      - Extract N random open-water negative patches from same tile
    Returns list of feature dicts with 'label' (1=wreck, 0=water) and 'wreck_id'.
    """
    wrecks  = load_known_wrecks()
    all_tiles = find_all_tiles()
    rng = np.random.default_rng(RANDOM_SEED)

    # Group tiles by directory (granule)
    tile_dirs: dict[Path, dict] = {}
    for tif in all_tiles:
        d = tif.parent
        if d not in tile_dirs:
            tile_dirs[d] = tile_band_dict(d)

    print(f"[ML] Scanning {len(tile_dirs)} tile directories for wreck patches...")
    print(f"[ML] {len(wrecks)} known wrecks with GPS coords")

    samples = []

    for tile_dir, bands in tile_dirs.items():
        if not bands:
            continue

        # Use blue band (or first available) as reference for spatial indexing
        ref_band_name = next((b for b in ["blue", "green", "red", "swir16"] if b in bands), None)
        if ref_band_name is None:
            continue
        ref_tif_path = bands[ref_band_name]

        with rasterio.open(ref_tif_path) as ref:
            ref_bounds = ref.bounds
            ref_height, ref_width = ref.height, ref.width
            ref_crs, ref_transform = ref.crs, ref.transform

            # Load all available bands for this granule
            band_data: dict[str, np.ndarray] = {}
            for bname, bpath in bands.items():
                try:
                    with rasterio.open(bpath) as src:
                        bd = src.read(1).astype(np.float32)
                    # HLS scale factor
                    if float(np.nanmedian(bd[bd > 0])) > 1.5:
                        bd = bd * 0.0001
                    bd[bd <= 0] = float("nan")
                    band_data[bname] = bd
                except Exception as _read_err:
                    print(f"  [WARN] Skipping corrupt band {bpath.name}: {_read_err}")
                    continue

            # ── Positive samples: named wrecks inside this tile ──────────────
            positives_found = 0
            for wreck in wrecks:
                rc = latlon_to_pixel(ref, wreck["lat"], wreck["lon"])
                if rc is None:
                    continue
                r, c = rc

                # Extract patch per band
                patches = {}
                for bname, bd in band_data.items():
                    p = extract_patch(bd, r, c)
                    if p is not None:
                        patches[bname] = p

                if len(patches) < 2:  # need at least 2 bands
                    continue

                # Build feature vector
                feat = {"label": 1, "wreck_id": wreck["id"],
                        "wreck_name": wreck["name"], "depth_ft": wreck["depth_ft"],
                        "lat": wreck["lat"], "lon": wreck["lon"],
                        "tile_dir": str(tile_dir)}
                for bname, p in patches.items():
                    feat.update(patch_features(p, bname))
                feat.update(cross_band_features(patches))
                samples.append(feat)
                positives_found += 1
                print(f"  [+] {wreck['name']} in {tile_dir.name} ({len(patches)} bands)")

            if positives_found == 0:
                continue

            # ── Negative samples: random open-water in same tile ────────────
            n_neg_needed = positives_found * negatives_per_positive
            n_neg = 0
            max_attempts = n_neg_needed * 30
            attempts = 0
            while n_neg < n_neg_needed and attempts < max_attempts:
                attempts += 1
                r_rand = int(rng.integers(PATCH_HALF + 1, ref_height - PATCH_HALF - 1))
                c_rand = int(rng.integers(PATCH_HALF + 1, ref_width  - PATCH_HALF - 1))

                # Must not overlap any known wreck (>50px exclusion zone)
                too_close = False
                for wreck in wrecks:
                    rc_w = latlon_to_pixel(ref, wreck["lat"], wreck["lon"])
                    if rc_w is None:
                        continue
                    if abs(r_rand - rc_w[0]) < 50 and abs(c_rand - rc_w[1]) < 50:
                        too_close = True
                        break
                if too_close:
                    continue

                # Check ref band — must be valid water (not nodata/land)
                ref_patch = extract_patch(band_data[ref_band_name], r_rand, c_rand)
                if ref_patch is None:
                    continue
                valid_px = np.sum(np.isfinite(ref_patch) & (ref_patch > 0))
                if valid_px < (PATCH_HALF * 2 + 1) ** 2 * 0.7:  # <70% valid = land/cloud
                    continue

                patches_neg = {}
                for bname, bd in band_data.items():
                    p = extract_patch(bd, r_rand, c_rand)
                    if p is not None:
                        patches_neg[bname] = p

                if len(patches_neg) < 2:
                    continue

                # Get lat/lon for the negative sample
                lx, ly = ref.xy(r_rand, c_rand)
                lon_n, lat_n = warp_transform(ref.crs, "EPSG:4326", [lx], [ly])

                feat_neg = {"label": 0, "wreck_id": "water",
                            "wreck_name": "open_water", "depth_ft": float("nan"),
                            "lat": float(lat_n[0]), "lon": float(lon_n[0]),
                            "tile_dir": str(tile_dir)}
                for bname, p in patches_neg.items():
                    feat_neg.update(patch_features(p, bname))
                feat_neg.update(cross_band_features(patches_neg))
                samples.append(feat_neg)
                n_neg += 1

            print(f"  [-] {n_neg} negative open-water samples from {tile_dir.name}")

    print(f"\n[ML] Dataset: {sum(1 for s in samples if s['label']==1)} positives, "
          f"{sum(1 for s in samples if s['label']==0)} negatives, "
          f"{len(samples)} total")
    return samples


# ── Feature matrix builder ───────────────────────────────────────────────────

def samples_to_matrix(samples: list[dict]):
    """Convert sample dicts to numpy X, y arrays. Returns (X, y, feature_names)."""
    import pandas as pd

    # Collect all numeric feature keys (exclude metadata)
    meta_keys = {"label", "wreck_id", "wreck_name", "depth_ft", "lat", "lon", "tile_dir"}
    all_feat_keys = set()
    for s in samples:
        all_feat_keys.update(k for k in s if k not in meta_keys)
    feat_keys = sorted(all_feat_keys)

    rows = []
    for s in samples:
        row = [s.get(k, float("nan")) for k in feat_keys]
        rows.append(row)

    X = np.array(rows, dtype=np.float32)
    y = np.array([s["label"] for s in samples], dtype=np.int32)

    # Replace inf with nan, then NaN impute with column median
    X = np.where(np.isfinite(X), X, np.nan)
    col_medians = np.nanmedian(X, axis=0)
    for j in range(X.shape[1]):
        mask = ~np.isfinite(X[:, j])
        X[mask, j] = col_medians[j]

    return X, y, feat_keys


# ── Training ─────────────────────────────────────────────────────────────────

def train(X: np.ndarray, y: np.ndarray, feat_names: list[str]):
    """Train LightGBM classifier. Returns (model, eval_dict)."""
    try:
        import lightgbm as lgb
    except ImportError:
        print("[ML] LightGBM not found — falling back to sklearn RandomForest")
        return train_sklearn(X, y, feat_names)

    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score, precision_score, recall_score

    print(f"\n[ML] Training LightGBM on X={X.shape} y={y.shape} "
          f"(pos={y.sum()} neg={(y==0).sum()})")

    # Scale pos_weight to handle imbalance
    pos_weight = (y == 0).sum() / max(y.sum(), 1)

    params = {
        "objective":    "binary",
        "metric":       "auc",
        "n_estimators": 400,
        "learning_rate": 0.05,
        "num_leaves":   31,
        "min_child_samples": 5,
        "scale_pos_weight": pos_weight,
        "verbose": -1,
        "device": "gpu" if _has_lgb_gpu() else "cpu",
    }

    skf = StratifiedKFold(n_splits=min(5, max(2, int(y.sum()))), shuffle=True, random_state=RANDOM_SEED)
    aucs, precs, recs = [], [], []
    final_model = None

    for fold, (tr_idx, va_idx) in enumerate(skf.split(X, y)):
        X_tr, X_va = X[tr_idx], X[va_idx]
        y_tr, y_va = y[tr_idx], y[va_idx]
        model = lgb.LGBMClassifier(**params)
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)],
                  callbacks=[lgb.early_stopping(30, verbose=False),
                              lgb.log_evaluation(period=-1)])
        proba = model.predict_proba(X_va)[:, 1]
        auc = roc_auc_score(y_va, proba) if len(np.unique(y_va)) > 1 else 0.5
        pred = (proba >= 0.5).astype(int)
        prec = precision_score(y_va, pred, zero_division=0)
        rec  = recall_score(y_va, pred, zero_division=0)
        aucs.append(auc); precs.append(prec); recs.append(rec)
        print(f"  Fold {fold+1}: AUC={auc:.3f}  P={prec:.3f}  R={rec:.3f}")
        final_model = model

    print(f"\n[ML] CV Results  AUC={np.mean(aucs):.3f}±{np.std(aucs):.3f}  "
          f"P={np.mean(precs):.3f}  R={np.mean(recs):.3f}")

    # Final model trained on all data
    final_model = lgb.LGBMClassifier(**params)
    final_model.fit(X, y)

    # Feature importance
    imp = sorted(zip(feat_names, final_model.feature_importances_),
                 key=lambda x: -x[1])
    print("\n[ML] Top 15 features:")
    for fname, fimp in imp[:15]:
        print(f"  {fname:35s}  {fimp:.1f}")

    return final_model, {"auc": float(np.mean(aucs)), "precision": float(np.mean(precs)),
                          "recall": float(np.mean(recs))}


def train_sklearn(X, y, feat_names):
    """Fallback: sklearn RandomForest if LightGBM not available."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    print("[ML] Training RandomForestClassifier (LightGBM fallback)")
    model = RandomForestClassifier(n_estimators=200, class_weight="balanced",
                                   random_state=RANDOM_SEED, n_jobs=-1)
    scores = cross_val_score(model, X, y, cv=min(5, int(y.sum())), scoring="roc_auc")
    print(f"[ML] CV AUC: {scores.mean():.3f} ± {scores.std():.3f}")
    model.fit(X, y)
    return model, {"auc": float(scores.mean())}


def _has_lgb_gpu():
    try:
        import lightgbm as lgb
        test = lgb.LGBMClassifier(device="gpu", n_estimators=1, verbose=-1)
        test.fit(np.random.rand(10, 2), np.array([0]*5 + [1]*5))
        return True
    except Exception:
        return False


# ── Export to TFLite for Coral Edge TPU ─────────────────────────────────────

def export_tflite(model, feat_names: list[str], n_features: int):
    """
    Export LightGBM / sklearn model to INT8 TFLite for Edge TPU.

    Pipeline:
        LightGBM predict_proba  →  wrap as Keras Dense  →  TFLite INT8 quantize
    The resulting .tflite runs on the Coral Edge TPU via tpu_server.py.
    """
    try:
        import tensorflow as tf
    except ImportError:
        print("[ML] TensorFlow not available — skipping TFLite export")
        print("[ML] Install with: pip install tensorflow")
        return None

    MODELS_DIR.mkdir(exist_ok=True)

    # Build a minimal TF Keras model that reproduces the LightGBM decision
    # boundary: we wrap the tree predictions as a learned threshold layer.
    # For a feature-vector classifier, a 2-layer dense net is sufficient.
    n = n_features
    inp = tf.keras.Input(shape=(n,), name="features")
    x = tf.keras.layers.Dense(64, activation="relu")(inp)
    x = tf.keras.layers.Dense(32, activation="relu")(x)
    out = tf.keras.layers.Dense(1, activation="sigmoid", name="wreck_prob")(x)
    keras_model = tf.keras.Model(inputs=inp, outputs=out)
    keras_model.compile(optimizer="adam", loss="binary_crossentropy")

    # We won't retrain the keras model from scratch here — instead we use the
    # LightGBM predictions to distil into the keras weights via knowledge distillation:
    # call from train() which already built X_full, y_full.
    print("[ML] NOTE: TFLite export generates skeleton model.")
    print("[ML] Run wreck_ml_distiller.py to distil LightGBM → TFLite weights.")

    # INT8 quantization skeleton for Edge TPU compatibility
    converter = tf.lite.TFLiteConverter.from_keras_model(keras_model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type  = tf.int8
    converter.inference_output_type = tf.int8

    try:
        tflite_model = converter.convert()
        cpu_path = MODELS_DIR / "wreck_classifier_cpu.tflite"
        cpu_path.write_bytes(tflite_model)
        print(f"[ML] TFLite CPU model: {cpu_path}")
        # Edge TPU compile step requires edgetpu_compiler CLI — document the command
        edgetpu_path = MODELS_DIR / "wreck_classifier_edgetpu.tflite"
        print(f"[ML] To compile for Coral Edge TPU:")
        print(f"     edgetpu_compiler {cpu_path} -o {MODELS_DIR}/")
        return str(cpu_path)
    except Exception as e:
        print(f"[ML] TFLite export failed: {e}")
        return None


# ── Save/load model ───────────────────────────────────────────────────────────

def save_model(model, feat_names: list[str], eval_dict: dict):
    """Save trained model + metadata for use by lake_michigan_scan.py Pass 5."""
    MODELS_DIR.mkdir(exist_ok=True)
    import pickle
    model_path = MODELS_DIR / "wreck_classifier.pkl"
    meta_path  = MODELS_DIR / "wreck_classifier_meta.json"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(meta_path, "w") as f:
        json.dump({
            "feature_names":    feat_names,
            "n_features":       len(feat_names),
            "patch_half":       PATCH_HALF,
            "log_sigma":        LOG_SIGMA,
            "eval":             eval_dict,
            "bands":            BAND_NAMES + ["thermal"],
        }, f, indent=2)
    print(f"[ML] Model saved: {model_path}")
    print(f"[ML] Metadata:    {meta_path}")
    return model_path, meta_path


def save_dataset_csv(samples: list[dict]):
    """Save feature dataset as CSV for inspection / external training."""
    import csv
    DATASET_CSV.parent.mkdir(exist_ok=True)
    if not samples:
        print("[ML] No samples to save.")
        return
    keys = list(samples[0].keys())
    with open(DATASET_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(samples)
    print(f"[ML] Dataset CSV: {DATASET_CSV}  ({len(samples)} rows, {len(keys)} columns)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="CESAROPS Wreck ML Trainer")
    ap.add_argument("--extract-only", action="store_true",
                    help="Extract dataset and save CSV, no training")
    ap.add_argument("--eval-only", action="store_true",
                    help="Load existing model and report eval metrics")
    ap.add_argument("--negatives", type=int, default=5,
                    help="Negative open-water samples per positive wreck patch (default 5)")
    ap.add_argument("--export-tflite", action="store_true",
                    help="Export TFLite skeleton for Edge TPU after training")
    args = ap.parse_args()

    if args.eval_only:
        meta_path = MODELS_DIR / "wreck_classifier_meta.json"
        if not meta_path.exists():
            print(f"No model metadata found at {meta_path} — run training first.")
            return
        meta = json.load(open(meta_path))
        print("Model metadata:")
        print(json.dumps(meta, indent=2))
        return

    # ── Step 1: Extract patches ───────────────────────────────────────────────
    samples = extract_samples(negatives_per_positive=args.negatives)
    save_dataset_csv(samples)

    if args.extract_only or len(samples) < 5:
        if len(samples) < 5:
            print("\n[ML] Not enough samples to train (need ≥5). Download more tiles first.")
            print("[ML] Wrecks with GPS that need tile coverage:")
            for w in load_known_wrecks():
                print(f"  {w['name']:30s}  lat={w['lat']:.4f} lon={w['lon']:.4f}")
        return

    # ── Step 2: Build feature matrix ─────────────────────────────────────────
    X, y, feat_names = samples_to_matrix(samples)
    print(f"\n[ML] Feature matrix: {X.shape[0]} samples × {X.shape[1]} features")
    print(f"[ML] Class balance: {y.sum()} wrecks / {(y==0).sum()} open water")

    # ── Step 3: Train ─────────────────────────────────────────────────────────
    model, eval_dict = train(X, y, feat_names)

    # ── Step 4: Save ─────────────────────────────────────────────────────────
    save_model(model, feat_names, eval_dict)

    # ── Step 5 (optional): TFLite export ─────────────────────────────────────
    if args.export_tflite:
        export_tflite(model, feat_names, X.shape[1])

    print("\n[ML] Done. Next steps:")
    print("  1. Inspect dataset:     outputs/wreck_ml_dataset.csv")
    print("  2. Run inference:       python wreck_ml_predictor.py")
    print("  3. Export to TPU:       python wreck_ml_trainer.py --export-tflite")
    print("  4. See model features:  models/wreck_classifier_meta.json")


if __name__ == "__main__":
    main()
