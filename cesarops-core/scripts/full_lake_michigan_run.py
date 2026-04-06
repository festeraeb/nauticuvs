#!/usr/bin/env python3
"""
FULL LAKE MICHIGAN + SUPERIOR PROCESSING RUN

Includes:
1. Thermal anomaly detection (B10/B11)
2. Optical analysis (B04/B05)
3. LEAKING BOAT DETECTION (B04/B05 ratio)
4. Multi-sensor fusion

Processes ALL tiles in:
- wreckhunter2000/data/cache/census_raw/2021_low_water/
- wreckhunter2000/data/cache/census_raw/2025_rossa/
- Any other Lake Michigan tiles

Outputs to:
- outputs/full_lake_run_[timestamp]/
"""

import numpy as np
from pathlib import Path
from PIL import Image
import json
from datetime import datetime
import sys

# ============================================================================
# CONFIGURATION
# ============================================================================

SEARCH_DIRS = [
    Path("wreckhunter2000/data/cache/census_raw/2021_low_water"),
    Path("wreckhunter2000/data/cache/census_raw/2025_rossa"),
]

OUTPUT_DIR = Path("outputs/full_lake_run")
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_DIR = OUTPUT_DIR / TIMESTAMP
RUN_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# PROCESSING FUNCTIONS
# ============================================================================

def load_tile(tif_path):
    """Load a GeoTIFF file"""
    if not tif_path.exists():
        return None
    
    img = Image.open(tif_path)
    return np.array(img, dtype=np.float32)

def process_thermal(b10_data, b11_data):
    """Process thermal bands for anomalies"""
    # Combine B10 and B11
    thermal = (b10_data + b11_data) / 2
    
    # Calculate statistics
    mean_val = float(np.mean(thermal))
    std_val = float(np.std(thermal))
    
    # Z-score
    zscore = (thermal - mean_val) / (std_val + 1e-6)
    
    # Find anomalies
    anomaly_mask = np.abs(zscore) > 2.5
    anomaly_count = int(np.sum(anomaly_mask))
    
    return {
        'type': 'thermal',
        'mean': mean_val,
        'std': std_val,
        'max_zscore': float(np.max(np.abs(zscore))),
        'anomaly_count': anomaly_count
        # Don't save zscore_map to JSON (numpy array)
    }

def process_optical(b04_data, b05_data):
    """Process optical/NIR bands"""
    # NIR/Red ratio (vegetation index, but also detects oil)
    nir_red_ratio = b05_data / (b04_data + 1e-6)
    
    mean_val = float(np.mean(nir_red_ratio))
    std_val = float(np.std(nir_red_ratio))
    
    zscore = (nir_red_ratio - mean_val) / (std_val + 1e-6)
    
    anomaly_mask = np.abs(zscore) > 2.5
    anomaly_count = int(np.sum(anomaly_mask))
    
    return {
        'type': 'optical',
        'nir_red_mean': mean_val,
        'nir_red_std': std_val,
        'max_zscore': float(np.max(np.abs(zscore))),
        'anomaly_count': anomaly_count
    }

def detect_leaking_boat(b04_data, b05_data, b11_data=None, b12_data=None):
    """
    Detect oil/fuel leaks from boats using spectral signature
    
    Oil signature:
    - Absorbs red light (low B04)
    - Absorbs SWIR strongly (very low B11, B12)
    - B04/B05 ratio indicates oil vs water vs wake
    
    Wake signature:
    - High reflectance in ALL bands (white water)
    - B04/B05 ratio ~1.0 (neutral)
    
    Strategy:
    - Look for LOW B04 + LOW B11 + specific ratio
    - Filter out HIGH reflectance (wake bubbles)
    """
    # Normalize bands (0-1 scale)
    b04_norm = b04_data / (np.max(b04_data) + 1e-6)
    b05_norm = b05_data / (np.max(b05_data) + 1e-6)
    
    # Oil index: Low red + low SWIR = oil
    # High values indicate potential oil
    if b11_data is not None:
        b11_norm = b11_data / (np.max(b11_data) + 1e-6)
        # Oil absorbs both red and SWIR
        oil_index = (1.0 - b04_norm) * (1.0 - b11_norm)
    else:
        # Use B04/B05 ratio
        ratio = b04_norm / (b05_norm + 1e-6)
        # Oil has low ratio (absorbs red more than NIR)
        oil_index = 1.0 - ratio
    
    # Calculate statistics
    mean_oil = float(np.mean(oil_index))
    std_oil = float(np.std(oil_index))
    
    # Find areas with HIGH oil index (potential oil)
    zscore = (oil_index - mean_oil) / (std_oil + 1e-6)
    
    # Positive Z-scores indicate high oil index
    oil_mask = zscore > 2.0  # More than 2 std above mean
    
    # ALSO filter out wake (high reflectance in all bands)
    wake_mask = (b04_norm > 0.7) & (b05_norm > 0.7)  # Bright in both = wake
    oil_mask = oil_mask & (~wake_mask)  # Remove wake areas
    
    oil_count = int(np.sum(oil_mask))
    
    return {
        'type': 'leaking_boat',
        'mean_oil_index': mean_oil,
        'std_oil_index': std_oil,
        'max_zscore': float(np.max(zscore)),
        'oil_pixel_count': oil_count,
        'oil_detected': oil_count > 50,  # At least 50 pixels
        'wake_pixels_filtered': int(np.sum(wake_mask))
    }

def process_tile_full(tile_path):
    """Process a single tile with all sensors"""
    results = {
        'tile': str(tile_path),
        'filename': tile_path.name,
        'processed_at': datetime.now().isoformat(),
        'sensors': {},
        'summary': {}
    }
    
    # Load all bands
    base_name = tile_path.name.replace('.B04.tif', '').replace('.B10.tif', '')
    tile_dir = tile_path.parent
    
    bands = {}
    for band in ['B04', 'B05', 'B10', 'B11']:
        band_path = tile_dir / f"{base_name}.{band}.tif"
        if band_path.exists():
            bands[band] = load_tile(band_path)
    
    if not bands:
        results['error'] = 'No bands found'
        return results
    
    # Process thermal
    if 'B10' in bands and 'B11' in bands:
        results['sensors']['thermal'] = process_thermal(bands['B10'], bands['B11'])
    
    # Process optical
    if 'B04' in bands and 'B05' in bands:
        results['sensors']['optical'] = process_optical(bands['B04'], bands['B05'])
        
        # Leaking boat detection (use SWIR if available)
        b11 = bands.get('B11')
        b12 = bands.get('B12')
        results['sensors']['leaking_boat'] = detect_leaking_boat(
            bands['B04'], 
            bands['B05'],
            b11_data=b11,
            b12_data=b12
        )
    
    # Summary
    total_anomalies = sum(
        s.get('anomaly_count', 0) 
        for s in results['sensors'].values() 
        if 'anomaly_count' in s
    )
    
    oil_detected = results['sensors'].get('leaking_boat', {}).get('oil_detected', False)
    
    results['summary'] = {
        'total_anomalies': total_anomalies,
        'thermal_anomalies': results['sensors'].get('thermal', {}).get('anomaly_count', 0),
        'optical_anomalies': results['sensors'].get('optical', {}).get('anomaly_count', 0),
        'leaking_boat_detected': oil_detected,
        'bands_processed': list(bands.keys())
    }
    
    return results

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*70)
    print("FULL LAKE MICHIGAN + SUPERIOR PROCESSING RUN")
    print("="*70)
    print()
    print(f"Output directory: {RUN_DIR}")
    print()
    
    # Find all tiles
    print("[1/4] Finding tiles...")
    all_tiles = []
    
    for search_dir in SEARCH_DIRS:
        if not search_dir.exists():
            continue
        
        print(f"  Scanning: {search_dir}")
        for tif in search_dir.glob("*.tif"):
            # Skip small files and geojson
            if tif.stat().st_size < 100000 or '.geojson' in str(tif):
                continue
            
            # Only process B04 or B10 (we'll load other bands automatically)
            if '.B04.tif' in tif.name or '.B10.tif' in tif.name:
                all_tiles.append(tif)
    
    # Remove duplicates (same tile, different bands)
    unique_bases = set()
    unique_tiles = []
    for tile in all_tiles:
        base = tile.name.replace('.B04.tif', '').replace('.B10.tif', '')
        if base not in unique_bases:
            unique_bases.add(base)
            unique_tiles.append(tile)
    
    print(f"  Found: {len(unique_tiles)} unique tiles")
    print()
    
    # Process each tile
    print("[2/4] Processing tiles...")
    all_results = {
        'run_timestamp': TIMESTAMP,
        'total_tiles': len(unique_tiles),
        'processed': 0,
        'errors': 0,
        'thermal_anomalies_total': 0,
        'optical_anomalies_total': 0,
        'leaking_boats_detected': 0,
        'results': []
    }
    
    for i, tile in enumerate(unique_tiles):
        print(f"  [{i+1}/{len(unique_tiles)}] {tile.name}")
        
        result = process_tile_full(tile)
        all_results['results'].append(result)
        
        if 'error' in result:
            all_results['errors'] += 1
            print(f"    ✗ Error: {result.get('error', 'Unknown')}")
        else:
            all_results['processed'] += 1
            summary = result['summary']
            all_results['thermal_anomalies_total'] += summary['thermal_anomalies']
            all_results['optical_anomalies_total'] += summary['optical_anomalies']
            if summary['leaking_boat_detected']:
                all_results['leaking_boats_detected'] += 1
                print(f"    [WARNING] LEAKING BOAT DETECTED!")
            
            print(f"    Thermal: {summary['thermal_anomalies']}, Optical: {summary['optical_anomalies']}")
        
        # Save progress every 10 tiles
        if (i + 1) % 10 == 0:
            progress_file = RUN_DIR / f"progress_{i+1}.json"
            with open(progress_file, 'w') as f:
                json.dump(all_results, f, indent=2)
    
    # Save final results
    print()
    print("[3/4] Saving results...")
    
    all_results['completed_at'] = datetime.now().isoformat()
    
    # Full results
    output_file = RUN_DIR / "full_results.json"
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"  Full results: {output_file}")
    
    # Summary only
    summary_file = RUN_DIR / "summary.json"
    summary = {
        'run_timestamp': all_results['run_timestamp'],
        'total_tiles': all_results['total_tiles'],
        'processed': all_results['processed'],
        'errors': all_results['errors'],
        'thermal_anomalies_total': all_results['thermal_anomalies_total'],
        'optical_anomalies_total': all_results['optical_anomalies_total'],
        'leaking_boats_detected': all_results['leaking_boats_detected'],
        'completed_at': all_results['completed_at']
    }
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"  Summary: {summary_file}")
    
    # Print summary
    print()
    print("[4/4] RUN SUMMARY")
    print("="*70)
    print(f"  Tiles Processed: {all_results['processed']}")
    print(f"  Errors: {all_results['errors']}")
    print(f"  Thermal Anomalies: {all_results['thermal_anomalies_total']:,}")
    print(f"  Optical Anomalies: {all_results['optical_anomalies_total']:,}")
    print(f"  Leaking Boats Detected: {all_results['leaking_boats_detected']}")
    print("="*70)
    print()
    print(f"Results saved to: {RUN_DIR}")
    
    return all_results

if __name__ == "__main__":
    main()
