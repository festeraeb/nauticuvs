#!/usr/bin/env python3
"""
STEP 3 & 4: PROCESS TILES

Run on BOTH laptop and Xenon with IDENTICAL logic.
"""

import numpy as np
from pathlib import Path
from PIL import Image
import json
from datetime import datetime
import sys

# Configuration
INVENTORY_FILE = Path("outputs/geotiff_inventory.json")
OUTPUT_DIR = Path("outputs/machine_run")  # Will be renamed per machine

def process_tile(tile_path):
    """Process a single tile, return results"""
    results = {
        'tile': str(tile_path),
        'filename': tile_path.name,
        'processed_at': datetime.now().isoformat(),
        'sensors': {},
        'anomalies': []
    }
    
    # Load tile
    if not tile_path.exists():
        results['error'] = 'File not found'
        return results
    
    try:
        img = Image.open(tile_path)
        data = np.array(img, dtype=np.float32)
    except Exception as e:
        results['error'] = f'Load error: {e}'
        return results
    
    # Basic statistics
    mean_val = float(np.mean(data))
    std_val = float(np.std(data))
    
    # Z-score
    zscore = (data - mean_val) / (std_val + 1e-6)
    
    # Find anomalies
    anomaly_mask = np.abs(zscore) > 2.5
    anomaly_count = int(np.sum(anomaly_mask))
    
    # Top anomalies
    anomalies = []
    if anomaly_count > 0:
        zscore_abs = np.abs(zscore)
        top_indices = np.unravel_index(np.argsort(zscore_abs.ravel())[-10:], zscore.shape)
        top_zscores = zscore_abs[top_indices]
        
        for i in range(min(len(top_indices[0]), 10)):
            anomalies.append({
                'pixel_y': int(top_indices[0][i]),
                'pixel_x': int(top_indices[1][i]),
                'zscore': float(top_zscores[i])
            })
    
    results['sensors']['single_band'] = {
        'mean': mean_val,
        'std': std_val,
        'max_zscore': float(np.max(np.abs(zscore))),
        'anomaly_count': anomaly_count,
        'anomalies': anomalies[:5]
    }
    results['anomalies'] = anomalies
    
    return results

def process_all_tiles(machine_name):
    """Process all tiles from inventory"""
    
    # Set output directory per machine
    output_dir = OUTPUT_DIR.parent / f"{machine_name}_run"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load inventory
    if not INVENTORY_FILE.exists():
        print("ERROR: Run inventory_geotiffs.py first")
        return
    
    with open(INVENTORY_FILE) as f:
        inventory = json.load(f)
    
    tiles = inventory.get('tiles', [])
    print(f"Processing {len(tiles)} tiles on {machine_name}...")
    print()
    
    all_results = {
        'machine': machine_name,
        'started_at': datetime.now().isoformat(),
        'total_tiles': len(tiles),
        'processed': 0,
        'errors': 0,
        'results': []
    }
    
    for i, tile_info in enumerate(tiles):
        tile_path = Path(tile_info['path'])
        
        print(f"[{i+1}/{len(tiles)}] {tile_info['filename']}")
        
        result = process_tile(tile_path)
        all_results['results'].append(result)
        
        if 'error' in result:
            all_results['errors'] += 1
            print(f"  ✗ Error: {result['error']}")
        else:
            all_results['processed'] += 1
            anomaly_count = result['sensors']['single_band']['anomaly_count']
            max_z = result['sensors']['single_band']['max_zscore']
            print(f"  ✓ Anomalies: {anomaly_count}, Max Z: {max_z:.2f}")
        
        # Save progress every 10 tiles
        if (i + 1) % 10 == 0:
            progress_file = output_dir / f"progress_{i+1}.json"
            with open(progress_file, 'w') as f:
                json.dump(all_results, f, indent=2)
    
    # Final save
    all_results['completed_at'] = datetime.now().isoformat()
    output_file = output_dir / f"results_{machine_name}.json"
    
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print()
    print(f"Processing complete:")
    print(f"  Processed: {all_results['processed']}")
    print(f"  Errors: {all_results['errors']}")
    print(f"  Output: {output_file}")
    
    return all_results

if __name__ == "__main__":
    # Get machine name from command line
    machine_name = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    
    print("="*70)
    print(f"PROCESSING ON: {machine_name.upper()}")
    print("="*70)
    print()
    
    process_all_tiles(machine_name)
