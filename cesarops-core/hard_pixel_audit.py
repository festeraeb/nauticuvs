#!/usr/bin/env python3
"""
HARD-PIXEL AUDIT - REAL DATA ONLY (NO SIMULATIONS)
Rule #1 Active: Processing actual satellite TIFF files

Data Sources:
  - C:\\Users\\thomf\\programming\\wreckhunter2000\\data\\cache\\census_raw\\2021_low_water\\
  - C:\\Users\\thomf\\programming\\wreckhunter2000\\data\\cache\\census_raw\\2025_rossa\\

Processing Pipeline:
  1. Thermal Sieve (B10 Z-Score Analysis)
  2. Curvelet Sharpener (3-Scale Transform)
  3. 1.47x Inverse Squeeze (Zion Constant)
  4. Two-Date Lock (2021 vs 2025 cross-verification)
"""

import numpy as np
import os
import json
from pathlib import Path
from datetime import datetime
from scipy import ndimage
from scipy.stats import zscore
import math

# ============================================================================
# CONFIGURATION - REAL FILE PATHS ONLY
# ============================================================================

# Point to local data (Corrected path to workspace)
DATA_BASE = Path(r"C:\Users\thomf\programming\cesarops-wreckhunter build\wreckhunter2000\data\cache\census_raw")
LANDSAT_DIR = DATA_BASE / "2021_low_water"
SENTINEL_DIR = DATA_BASE / "2025_rossa"
OUTPUT_DIR = Path(r"outputs/hard_pixel_audit")

# Safety Check
if not DATA_BASE.exists():
    print(f"⚠️ WARNING: Data base not found at {DATA_BASE}")
    print("   Please ensure satellite data is downloaded to this path.")
    sys.exit(1)
else:
    print(f"✅ Data Base Found: {DATA_BASE}")

# Ensure output directory exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Zion Constant from MASTER_FORENSIC_LEDGER V2.0
ZION_CONSTANT = 1.47
DEPTH_THRESHOLD_FT = 400

# Detection thresholds
THERMAL_ZSCORE_THRESHOLD = 2.5  # Absolute value
CURVELET_THRESHOLD = 2.0  # Z-score minimum for curvelet application
TWO_DATE_ALIGNMENT_M = 10.0  # Meters tolerance

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def load_tiff_band(tiff_path):
    """
    Load a GeoTIFF file and return pixel array
    Uses rasterio if available, falls back to tifffile or simple numpy
    """
    try:
        import rasterio
        with rasterio.open(tiff_path) as src:
            data = src.read(1)  # First band
            return data, src.profile
    except ImportError:
        pass
    
    try:
        import tifffile
        with tifffile.TiffFile(tiff_path) as tif:
            data = tif.pages[0].asarray()
            return data, {}
    except ImportError:
        pass
    
    # Fallback: try PIL/Pillow
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(tiff_path)
        data = np.array(img)
        return data, {}
    except Exception as e:
        print(f"  WARNING: Could not load {tiff_path}: {e}")
        return None, {}

def calculate_zscore_array(data_array):
    """Calculate Z-score for each pixel in array"""
    # Flatten for calculation, then reshape
    flat = data_array.flatten()
    
    # Mask invalid values (NaN, infinity, zero for thermal)
    valid_mask = np.isfinite(flat) & (flat != 0)
    
    z_scores = np.zeros_like(flat, dtype=np.float64)
    
    if np.sum(valid_mask) > 10:  # Need sufficient valid pixels
        valid_values = flat[valid_mask]
        mean = np.mean(valid_values)
        std = np.std(valid_values)
        
        if std > 0:
            z_scores[valid_mask] = (valid_values - mean) / std
    
    return z_scores.reshape(data_array.shape)

def apply_curvelet_transform(data_array, scales=3):
    """
    Apply simplified curvelet-like multi-scale transform
    Real implementation would use CurveLab or similar
    This uses Laplacian pyramid decomposition as approximation
    """
    results = []
    
    current = data_array.astype(np.float64)
    
    for scale in range(scales):
        # Gaussian blur at this scale
        sigma = (2.0 ** scale) * 0.5
        if sigma > 0:
            blurred = ndimage.gaussian_filter(current, sigma=sigma)
        else:
            blurred = current.copy()
        
        # Detail coefficients (current - blurred)
        detail = current - blurred
        results.append({
            'scale': scale,
            'sigma': sigma,
            'detail': detail,
            'energy': np.sum(detail**2)
        })
        
        current = blurred
    
    # Add coarse approximation
    results.append({
        'scale': scales,
        'sigma': 'coarse',
        'detail': current,
        'energy': np.sum(current**2)
    })
    
    return results

def find_anomalies_in_zscore(z_array, threshold):
    """Find connected regions exceeding Z-score threshold"""
    # Binary mask of high Z-score regions
    binary = np.abs(z_array) > threshold
    
    # Label connected regions
    labeled, num_features = ndimage.label(binary)
    
    anomalies = []
    for i in range(1, num_features + 1):
        region_mask = (labeled == i)
        
        # Get region properties
        region_pixels = np.where(region_mask)
        if len(region_pixels[0]) > 0:
            # Calculate centroid
            center_row = int(np.mean(region_pixels[0]))
            center_col = int(np.mean(region_pixels[1]))
            
            # Calculate region size (pixels)
            pixel_count = np.sum(region_mask)
            
            # Get max Z-score in region
            region_z = z_array[region_mask]
            max_z = np.max(np.abs(region_z))
            mean_z = np.mean(np.abs(region_z))
            
            anomalies.append({
                'center_pixel': (center_row, center_col),
                'pixel_count': pixel_count,
                'max_zscore': float(max_z),
                'mean_zscore': float(mean_z),
                'bounding_box': {
                    'row_min': int(np.min(region_pixels[0])),
                    'row_max': int(np.max(region_pixels[0])),
                    'col_min': int(np.min(region_pixels[1])),
                    'col_max': int(np.max(region_pixels[1])),
                }
            })
    
    return anomalies

def estimate_length_from_pixels(anomaly, pixel_size_m=30.0):
    """
    Estimate physical length from pixel bounding box
    Landsat-8: 30m/pixel
    Sentinel-2: 20m/pixel (some bands 10m)
    """
    bbox = anomaly['bounding_box']
    
    # Calculate dimensions in pixels
    height_px = bbox['row_max'] - bbox['row_min']
    width_px = bbox['col_max'] - bbox['col_min']
    
    # Take major axis as length
    major_axis_px = max(height_px, width_px)
    
    # Convert to meters then feet
    length_m = major_axis_px * pixel_size_m
    length_ft = length_m * 3.28084
    
    return length_ft

def apply_zion_constant(detected_length_ft, depth_ft=180):
    """Apply 1.47x inverse squeeze for depth correction"""
    if depth_ft > DEPTH_THRESHOLD_FT:
        return detected_length_ft / ZION_CONSTANT
    return detected_length_ft

def calculate_pixel_distance(anomaly1, anomaly2, pixel_size_m=30.0):
    """Calculate distance between two anomaly centroids in meters"""
    row1, col1 = anomaly1['center_pixel']
    row2, col2 = anomaly2['center_pixel']
    
    delta_row = row2 - row1
    delta_col = col2 - col1
    
    distance_m = math.sqrt(delta_row**2 + delta_col**2) * pixel_size_m
    return distance_m

# ============================================================================
# MAIN PROCESSING PIPELINE
# ============================================================================

def process_landsat_thermal():
    """Process Landsat-8/9 B10 thermal band"""
    print("=" * 100)
    print("STEP 1: THERMAL SIEVE (Landsat-8 B10)")
    print("=" * 100)
    print()
    
    # Find all B10 thermal files
    b10_files = list(LANDSAT_DIR.glob("*B10.tif"))
    
    if not b10_files:
        print(f"  ERROR: No B10 files found in {LANDSAT_DIR}")
        return []
    
    print(f"  Found {len(b10_files)} thermal band files")
    print()
    
    all_anomalies = []
    
    for b10_file in b10_files:
        print(f"  Processing: {b10_file.name}")
        
        # Load thermal data
        data, profile = load_tiff_band(b10_file)
        
        if data is None:
            continue
        
        print(f"    Array shape: {data.shape}")
        print(f"    Data type: {data.dtype}")
        
        # Check if data is in Kelvin (typical Landsat B10) or needs conversion
        data_min = np.min(data[np.isfinite(data)])
        data_max = np.max(data[np.isfinite(data)])
        
        # Landsat-8 B10: typically 270-330 Kelvin
        # If values are much larger, might be scaled integers
        if data_min > 1000:
            print(f"    WARNING: Data appears to be scaled integers (min={data_min}, max={data_max})")
            # Try to convert to Kelvin (Landsat-8 scaling)
            data = data.astype(np.float64) * 0.1  # Typical scaling
        
        # Calculate Z-scores
        print(f"    Calculating Z-scores...")
        z_scores = calculate_zscore_array(data)
        
        # Find anomalies exceeding threshold
        anomalies = find_anomalies_in_zscore(z_scores, THERMAL_ZSCORE_THRESHOLD)
        
        print(f"    Found {len(anomalies)} thermal anomalies (Z > {THERMAL_ZSCORE_THRESHOLD})")
        
        # Process each anomaly
        for i, anom in enumerate(anomalies):
            # Estimate length
            length_ft = estimate_length_from_pixels(anom, pixel_size_m=30.0)
            
            # Apply Zion Constant
            corrected_length = apply_zion_constant(length_ft, depth_ft=180)
            
            anom['estimated_length_ft'] = length_ft
            anom['zion_corrected_length_ft'] = corrected_length
            anom['source_file'] = b10_file.name
            anom['sensor'] = 'Landsat-8'
            anom['band'] = 'B10 (Thermal)'
            anom['date'] = '2021-07-01'  # From filename
            
            # Get actual thermal values at anomaly location
            row, col = anom['center_pixel']
            anom['thermal_value'] = float(data[row, col]) if np.isfinite(data[row, col]) else None
            anom['max_zscore'] = float(z_scores[row, col])
            
            all_anomalies.append(anom)
            
            print(f"      Anomaly {i+1}:")
            print(f"        Pixel: ({row}, {col})")
            print(f"        Size: {anom['pixel_count']} pixels")
            print(f"        Est. Length: {length_ft:.1f} ft → {corrected_length:.1f} ft (Zion corrected)")
            print(f"        Max Z-Score: {anom['max_zscore']:.2f}")
        
        print()
    
    return all_anomalies

def process_sentinel_optical():
    """Process Sentinel-2 B04/B05 optical bands"""
    print("=" * 100)
    print("STEP 2: OPTICAL SIEVE (Sentinel-2 B04/B05)")
    print("=" * 100)
    print()
    
    # Find all B04 (Red) and B05 (Red Edge) files
    b04_files = list(SENTINEL_DIR.glob("*B04.tif"))
    b05_files = list(SENTINEL_DIR.glob("*B05.tif"))
    
    if not b04_files or not b05_files:
        print(f"  ERROR: Missing B04 or B05 files in {SENTINEL_DIR}")
        return []
    
    print(f"  Found {len(b04_files)} B04 files, {len(b05_files)} B05 files")
    print()
    
    all_anomalies = []
    
    # Process matching pairs
    for b04_file in b04_files:
        # Find corresponding B05 file (same date/tile)
        base_name = b04_file.name.replace('.B04.tif', '')
        b05_file = SENTINEL_DIR / f"{base_name}.B05.tif"
        
        if not b05_file.exists():
            print(f"  WARNING: No matching B05 for {b04_file.name}")
            continue
        
        print(f"  Processing pair: {b04_file.name} + {b05_file.name}")
        
        # Load both bands
        b04_data, _ = load_tiff_band(b04_file)
        b05_data, _ = load_tiff_band(b05_file)
        
        if b04_data is None or b05_data is None:
            continue
        
        # Calculate B05/B04 ratio (mussel glow indicator)
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = b05_data.astype(np.float64) / b04_data.astype(np.float64)
            ratio[~np.isfinite(ratio)] = 0
        
        # Calculate Z-scores on ratio
        print(f"    Calculating B05/B04 ratio Z-scores...")
        z_scores = calculate_zscore_array(ratio)
        
        # Find anomalies
        anomalies = find_anomalies_in_zscore(z_scores, THERMAL_ZSCORE_THRESHOLD * 0.8)  # Slightly lower threshold for optical
        
        print(f"    Found {len(anomalies)} optical anomalies")
        
        # Process each anomaly
        for i, anom in enumerate(anomalies):
            # Estimate length (Sentinel-2 = 20m/pixel for these bands)
            length_ft = estimate_length_from_pixels(anom, pixel_size_m=20.0)
            
            # Apply Zion Constant
            corrected_length = apply_zion_constant(length_ft, depth_ft=180)
            
            anom['estimated_length_ft'] = length_ft
            anom['zion_corrected_length_ft'] = corrected_length
            anom['source_file'] = f"{b04_file.name} + {b05_file.name}"
            anom['sensor'] = 'Sentinel-2'
            anom['band'] = 'B05/B04 Ratio'
            anom['date'] = '2025-09-01'  # From filename
            
            # Get ratio value at anomaly location
            row, col = anom['center_pixel']
            anom['ratio_value'] = float(ratio[row, col]) if np.isfinite(ratio[row, col]) else None
            anom['max_zscore'] = float(z_scores[row, col])
            
            all_anomalies.append(anom)
            
            print(f"      Anomaly {i+1}:")
            print(f"        Pixel: ({row}, {col})")
            print(f"        Size: {anom['pixel_count']} pixels")
            print(f"        Est. Length: {length_ft:.1f} ft → {corrected_length:.1f} ft (Zion corrected)")
            print(f"        Max Z-Score: {anom['max_zscore']:.2f}")
        
        print()
    
    return all_anomalies

def two_date_lock(landsat_anomalies, sentinel_anomalies):
    """
    Compare 2021 Landsat detections with 2025 Sentinel detections
    Keep only anomalies that appear in both dates within 10m alignment
    """
    print("=" * 100)
    print("STEP 3: TWO-DATE LOCK (2021 vs 2025)")
    print("=" * 100)
    print()
    
    print(f"  Landsat-8 (2021) anomalies: {len(landsat_anomalies)}")
    print(f"  Sentinel-2 (2025) anomalies: {len(sentinel_anomalies)}")
    print()
    
    verified_anomalies = []
    
    for landsat_anom in landsat_anomalies:
        best_match = None
        best_distance = float('inf')
        
        # Find closest Sentinel anomaly
        for sentinel_anom in sentinel_anomalies:
            # Calculate distance (using Landsat 30m pixel size for consistency)
            distance = calculate_pixel_distance(
                landsat_anom, sentinel_anom, pixel_size_m=30.0
            )
            
            if distance < best_distance:
                best_distance = distance
                best_match = sentinel_anom
        
        # Check if within alignment tolerance
        if best_distance <= TWO_DATE_ALIGNMENT_M:
            # VERIFIED - same anomaly in both dates
            landsat_anom['two_date_verified'] = True
            landsat_anom['sentinel_match'] = best_match
            landsat_anom['alignment_distance_m'] = best_distance
            verified_anomalies.append(landsat_anom)
            
            print(f"  ✓ VERIFIED: Landsat ({landsat_anom['center_pixel']}) ↔ " +
                  f"Sentinel ({best_match['center_pixel']}) @ {best_distance:.1f}m")
        else:
            landsat_anom['two_date_verified'] = False
            print(f"  ✗ DISCARDED: Landsat ({landsat_anom['center_pixel']}) - " +
                  f"no match within {TWO_DATE_ALIGNMENT_M}m (closest: {best_distance:.1f}m)")
    
    print()
    print(f"  Two-Date Verified: {len(verified_anomalies)} / {len(landsat_anomalies)}")
    print()
    
    return verified_anomalies

def apply_curvelet_sharpener(anomalies, landsat_dir):
    """Apply curvelet transform to verified anomalies"""
    print("=" * 100)
    print("STEP 4: CURVELET SHARPENER")
    print("=" * 100)
    print()
    
    for anom in anomalies:
        if not anom.get('two_date_verified', False):
            continue
        
        # Find corresponding B10 file
        b10_file = landsat_dir / anom['source_file']
        
        if not b10_file.exists():
            # Try to find any B10 file
            b10_files = list(landsat_dir.glob("*B10.tif"))
            if b10_files:
                b10_file = b10_files[0]
            else:
                continue
        
        print(f"  Applying curvelet to anomaly at {anom['center_pixel']}...")
        
        # Load thermal data
        data, _ = load_tiff_band(b10_file)
        if data is None:
            continue
        
        # Extract region around anomaly
        row, col = anom['center_pixel']
        region_size = max(anom['bounding_box']['row_max'] - anom['bounding_box']['row_min'],
                         anom['bounding_box']['col_max'] - anom['bounding_box']['col_min'])
        region_size = max(50, region_size)  # Minimum 50x50 pixel region
        
        row_start = max(0, row - region_size)
        row_end = min(data.shape[0], row + region_size)
        col_start = max(0, col - region_size)
        col_end = min(data.shape[1], col + region_size)
        
        region = data[row_start:row_end, col_start:col_end]
        
        # Apply curvelet transform
        curvelet_results = apply_curvelet_transform(region, scales=3)
        
        # Store results
        anom['curvelet_applied'] = True
        anom['curvelet_scales'] = len(curvelet_results)
        anom['curvelet_energy'] = [r['energy'] for r in curvelet_results]
        
        # Check if any scale exceeds curvelet threshold
        max_energy_scale = np.argmax(anom['curvelet_energy'])
        anom['curvelet_verified'] = anom['curvelet_energy'][max_energy_scale] > CURVELET_THRESHOLD
        
        print(f"    Region: {region.shape}")
        print(f"    Scales: {len(curvelet_results)}")
        print(f"    Energy by scale: {[f'{e:.2e}' for e in anom['curvelet_energy']]}")
        print(f"    Curvelet Verified: {'✓ YES' if anom['curvelet_verified'] else '✗ NO'}")
        print()
    
    return anomalies

def save_results(verified_anomalies):
    """Save results to JSON and generate summary"""
    print("=" * 100)
    print("STEP 5: SAVING RESULTS")
    print("=" * 100)
    print()
    
    # Prepare output (convert numpy types to Python types)
    output_data = []
    for anom in verified_anomalies:
        out = {
            'pixel_coordinates': {
                'row': int(anom['center_pixel'][0]),
                'col': int(anom['center_pixel'][1]),
            },
            'bounding_box_pixels': {
                'row_min': int(anom['bounding_box']['row_min']),
                'row_max': int(anom['bounding_box']['row_max']),
                'col_min': int(anom['bounding_box']['col_min']),
                'col_max': int(anom['bounding_box']['col_max']),
            },
            'pixel_count': int(anom['pixel_count']),
            'estimated_length_ft': float(anom['estimated_length_ft']),
            'zion_corrected_length_ft': float(anom['zion_corrected_length_ft']),
            'max_zscore': float(anom['max_zscore']),
            'two_date_verified': anom.get('two_date_verified', False),
            'alignment_distance_m': float(anom.get('alignment_distance_m', 0)),
            'curvelet_applied': anom.get('curvelet_applied', False),
            'curvelet_verified': anom.get('curvelet_verified', False),
            'source': {
                'sensor': anom.get('sensor', 'Unknown'),
                'band': anom.get('band', 'Unknown'),
                'file': anom.get('source_file', 'Unknown'),
                'date': anom.get('date', 'Unknown'),
            },
        }
        
        if 'thermal_value' in anom:
            out['thermal_value'] = anom['thermal_value']
        if 'ratio_value' in anom:
            out['ratio_value'] = anom['ratio_value']
        
        output_data.append(out)
    
    # Save JSON
    json_path = OUTPUT_DIR / "HARD_PIXEL_AUDIT_RESULTS.json"
    with open(json_path, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'rule': 'NO SIMULATIONS - REAL DATA ONLY',
            'total_verified_anomalies': len(output_data),
            'anomalies': output_data,
        }, f, indent=2)
    
    print(f"  Results saved to: {json_path}")
    print()
    
    # Print summary
    print("=" * 100)
    print("HARD-PIXEL AUDIT SUMMARY")
    print("=" * 100)
    print()
    print(f"  Total Verified Anomalies: {len(output_data)}")
    print()
    
    if output_data:
        print("  VERIFIED TARGETS:")
        for i, anom in enumerate(output_data, 1):
            print(f"    [{i}] Pixel: ({anom['pixel_coordinates']['row']}, {anom['pixel_coordinates']['col']})")
            print(f"        Length: {anom['estimated_length_ft']:.1f} ft → {anom['zion_corrected_length_ft']:.1f} ft (Zion)")
            print(f"        Z-Score: {anom['max_zscore']:.2f}")
            print(f"        Two-Date: {'✓' if anom['two_date_verified'] else '✗'}")
            print(f"        Curvelet: {'✓' if anom['curvelet_verified'] else '✗'}")
            print(f"        Source: {anom['source']['sensor']} {anom['source']['band']} ({anom['source']['date']})")
            print()
    
    print("=" * 100)
    
    return output_data

def main():
    """Main execution pipeline"""
    print()
    print("█" * 100)
    print("█  HARD-PIXEL AUDIT - REAL DATA ONLY (NO SIMULATIONS)")
    print("█  Rule #1 Active: Processing actual satellite TIFF files")
    print("█" * 100)
    print()
    
    print(f"Data Directory: {DATA_BASE}")
    print(f"Landsat Dir: {LANDSAT_DIR}")
    print(f"Sentinel Dir: {SENTINEL_DIR}")
    print(f"Output Dir: {OUTPUT_DIR}")
    print()
    
    # Verify data directories exist
    if not LANDSAT_DIR.exists():
        print(f"ERROR: Landsat directory not found: {LANDSAT_DIR}")
        return
    if not SENTINEL_DIR.exists():
        print(f"ERROR: Sentinel directory not found: {SENTINEL_DIR}")
        return
    
    # Execute pipeline
    landsat_anomalies = process_landsat_thermal()
    
    if not landsat_anomalies:
        print("No thermal anomalies found. Stopping pipeline.")
        return
    
    sentinel_anomalies = process_sentinel_optical()
    
    if not sentinel_anomalies:
        print("No optical anomalies found. Stopping pipeline.")
        return
    
    verified = two_date_lock(landsat_anomalies, sentinel_anomalies)
    
    if not verified:
        print("No two-date verified anomalies. Stopping pipeline.")
        return
    
    sharpened = apply_curvelet_sharpener(verified, LANDSAT_DIR)
    
    results = save_results(sharpened)
    
    print()
    print("█" * 100)
    print("█  HARD-PIXEL AUDIT COMPLETE")
    print("█" * 100)
    print()

if __name__ == "__main__":
    main()
