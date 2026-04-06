#!/usr/bin/env python3
"""
STEP 2: INVENTORY ALL GEOTIFFS

Find all .tif files, catalog by location/satellite/band.
"""

import json
from pathlib import Path
from datetime import datetime

# Directories to search
SEARCH_DIRS = [
    Path("wreckhunter2000/data/cache/census_raw/2021_low_water"),
    Path("wreckhunter2000/data/cache/census_raw/2025_rossa"),
    Path("wreckhunter2000/data/cache"),
]

OUTPUT_FILE = Path("outputs/geotiff_inventory.json")

def inventory_geotiffs():
    print("Inventorying GeoTIFF files...")
    print()
    
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    inventory = {
        'timestamp': datetime.now().isoformat(),
        'total_tiles': 0,
        'by_location': {},
        'by_satellite': {},
        'by_band': {},
        'tiles': []
    }
    
    for search_dir in SEARCH_DIRS:
        if not search_dir.exists():
            continue
        
        print(f"Scanning: {search_dir}")
        
        location_name = search_dir.name
        if location_name not in inventory['by_location']:
            inventory['by_location'][location_name] = 0
        
        for tif in search_dir.glob("*.tif"):
            # Skip geojson and small files
            if tif.stat().st_size < 100000:  # < 100KB, probably not real data
                continue
            
            # Parse filename
            name = tif.name
            parts = name.split('.')
            
            satellite = 'Unknown'
            date = 'Unknown'
            band = 'Unknown'
            
            if len(parts) >= 5:
                # HLS.L30.T16TDN.2021182T162824.v2.0.B10.tif
                satellite_code = parts[1]  # L30 or S30
                date_code = parts[3][:8]  # 2021182
                band = parts[5]  # B10
                
                satellite = 'Landsat-8' if satellite_code == 'L30' else 'Sentinel-2' if satellite_code == 'S30' else satellite_code
                
                # Format date
                try:
                    year = date_code[:4]
                    day_of_year = date_code[4:]
                    date = f"{year}-DOY{day_of_year}"
                except:
                    pass
            
            # Add to inventory
            tile_info = {
                'path': str(tif),
                'filename': name,
                'satellite': satellite,
                'date': date,
                'band': band,
                'size_mb': round(tif.stat().st_size / (1024*1024), 2)
            }
            
            inventory['tiles'].append(tile_info)
            inventory['total_tiles'] += 1
            inventory['by_location'][location_name] += 1
            
            # Count by satellite
            if satellite not in inventory['by_satellite']:
                inventory['by_satellite'][satellite] = 0
            inventory['by_satellite'][satellite] += 1
            
            # Count by band
            if band not in inventory['by_band']:
                inventory['by_band'][band] = 0
            inventory['by_band'][band] += 1
        
        print(f"  Found: {inventory['by_location'][location_name]} tiles")
    
    print()
    print(f"Total tiles: {inventory['total_tiles']}")
    print(f"By satellite: {inventory['by_satellite']}")
    print(f"By band: {inventory['by_band']}")
    print()
    
    # Save inventory
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(inventory, f, indent=2)
    
    print(f"Inventory saved to: {OUTPUT_FILE}")
    
    return inventory

if __name__ == "__main__":
    inventory_geotiffs()
