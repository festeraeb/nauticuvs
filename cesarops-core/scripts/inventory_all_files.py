#!/usr/bin/env python3
"""
INVENTORY ALL FILES

Categorize:
1. Core scripts we use
2. Data files (GeoTIFFs)
3. Archive candidates
4. BAG files (confusion source)
"""

import os
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(".")

# Categories
CORE_SCRIPTS = [
    "database_connector.py",
    "cesarops_engine.py",
    "cuda_test_kmz.py",
    "tpu_server.py",
    "live_feed_server.py",
    "three_tile_offset_analysis.py",
    "validate_detection.py",
    "deep_wreck_validation.py",
    "smart_daily_scan.py",
    "find_swot_dates.py",
    "prioritized_pull_v2.py",
]

SCRIPTS_FOLDER = [
    "scripts/wipe_database.py",
    "scripts/inventory_geotiffs.py",
    "scripts/process_tiles.py",
    "scripts/check_xenon_cuda.py",
    "scripts/compare_runs.py",
    "scripts/populate_database.py",
    "scripts/validate_database.py",
]

DOCUMENTATION = [
    "FRESH_START_PLAN.md",
    "TODO_RECOVERY.md",
    "DATABASE_STATUS.md",
    "TOOL_INVENTORY.md",
    "CUDA_READY_TOOLS_INVENTORY.md",
    "MASTER_FORENSIC_LEDGER.md",
    "ASSUMPTIONS_REGISTRY.md",
    "ALTIMETRY_CONSTELLATION.md",
    "FULL_SPECTRUM_STRATEGY.md",
    "PRIORITIZED_PULL_GUIDE.md",
    "FILE_INVENTORY.md",
]

def inventory_files():
    print("="*70)
    print("FILE INVENTORY")
    print("="*70)
    print()
    
    inventory = {
        'timestamp': datetime.now().isoformat(),
        'core_scripts': [],
        'scripts_folder': [],
        'documentation': [],
        'geotiffs': [],
        'bag_files': [],
        'large_dirs': [],
        'unknown_py': [],
        'archive_candidates': [],
    }
    
    # Check core scripts
    print("Core Scripts:")
    for script in CORE_SCRIPTS:
        path = ROOT / script
        if path.exists():
            size_kb = path.stat().st_size / 1024
            inventory['core_scripts'].append({'path': str(path), 'size_kb': round(size_kb, 1)})
            print(f"  [OK] {script} ({size_kb:.1f} KB)")
        else:
            print(f"  [MISSING] {script}")
    print()
    
    # Check scripts folder
    print("Scripts Folder:")
    scripts_dir = ROOT / "scripts"
    if scripts_dir.exists():
        for script in scripts_dir.glob("*.py"):
            size_kb = script.stat().st_size / 1024
            inventory['scripts_folder'].append({'path': str(script), 'size_kb': round(size_kb, 1)})
            print(f"  [OK] {script.name} ({size_kb:.1f} KB)")
    print()
    
    # Check documentation
    print("Documentation:")
    for doc in DOCUMENTATION:
        path = ROOT / doc
        if path.exists():
            size_kb = path.stat().st_size / 1024
            inventory['documentation'].append({'path': str(path), 'size_kb': round(size_kb, 1)})
            print(f"  [OK] {doc}")
    print()
    
    # Find GeoTIFFs
    print("GeoTIFFs:")
    geotiff_count = 0
    geotiff_size = 0
    for tif in ROOT.rglob("*.tif"):
        # Skip geojson
        if '.tif.geojson' in str(tif):
            continue
        # Only count reasonably sized files (>100KB)
        if tif.stat().st_size > 100000:
            geotiff_count += 1
            geotiff_size += tif.stat().st_size
            inventory['geotiffs'].append({
                'path': str(tif),
                'size_mb': round(tif.stat().st_size / (1024*1024), 2)
            })
    print(f"  Found: {geotiff_count} GeoTIFFs ({geotiff_size / (1024*1024):.1f} MB total)")
    print()
    
    # Find BAG files (confusion source)
    print("BAG Files (ROS bags - NOT satellite data):")
    bag_count = 0
    for bag in ROOT.rglob("*.bag"):
        bag_count += 1
        inventory['bag_files'].append({
            'path': str(bag),
            'size_mb': round(bag.stat().st_size / (1024*1024), 2)
        })
    print(f"  Found: {bag_count} .bag files")
    print()
    
    # Find large directories (build artifacts)
    print("Large Directories (likely build artifacts):")
    for dir_name in ['target', 'tauri-app', 'outputs', 'wreckhunter2000/cesarops-search']:
        dir_path = ROOT / dir_name
        if dir_path.exists():
            # Calculate directory size
            total_size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
            if total_size > 10*1024*1024:  # >10MB
                inventory['large_dirs'].append({
                    'path': str(dir_path),
                    'size_mb': round(total_size / (1024*1024), 1)
                })
                print(f"  {dir_name}: {total_size / (1024*1024):.1f} MB")
    print()
    
    # Find unknown Python files
    print("Unknown Python Files (review before archiving):")
    known_py = set(CORE_SCRIPTS) | set(s['path'] for s in inventory['scripts_folder'])
    for py_file in ROOT.glob("*.py"):
        if str(py_file) not in known_py and not py_file.name.startswith('.'):
            inventory['unknown_py'].append({
                'path': str(py_file),
                'size_kb': round(py_file.stat().st_size / 1024, 1)
            })
            print(f"  {py_file.name}")
    print()
    
    # Save inventory
    output_file = ROOT / "outputs" / "file_inventory.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump(inventory, f, indent=2)
    
    print("="*70)
    print(f"INVENTORY SAVED: {output_file}")
    print("="*70)
    
    return inventory

if __name__ == "__main__":
    inventory_files()
