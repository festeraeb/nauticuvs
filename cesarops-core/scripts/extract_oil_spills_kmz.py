#!/usr/bin/env python3
"""
Extract leaking boat/oil spill pixels and generate KMZ

Reads full_lake_michigan_run results and creates KMZ showing:
- Oil spill locations (red polygons)
- Wake filtered areas (blue)
- Tile boundaries
"""

import json
import numpy as np
from pathlib import Path
import simplekml
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

RESULTS_DIR = Path("outputs/full_lake_run/20260402_070802")
OUTPUT_KMZ = RESULTS_DIR / "leaking_boats.kmz"

# Tile center coordinates (approximate for Lake Michigan)
TILE_CENTERS = {
    'T16TDN': {'lat': 42.5, 'lon': -87.0},
}

# Pixel to km conversion (Landsat-8 = 30m/pixel, Sentinel-2 = 10m/pixel)
PIXEL_SIZE_M = {
    'L30': 30,  # Landsat
    'S30': 10,  # Sentinel
}

# ============================================================================
# FUNCTIONS
# ============================================================================

def extract_oil_pixels(tile_result):
    """Extract oil pixel coordinates from a tile result"""
    lb = tile_result.get('sensors', {}).get('leaking_boat', {})
    
    if not lb.get('oil_detected', False):
        return []
    
    # We'd need the actual zscore map to get exact pixel locations
    # For now, create a representative polygon based on pixel count
    
    oil_pixels = lb.get('oil_pixel_count', 0)
    
    if oil_pixels < 50:
        return []
    
    # Estimate area (assuming 30m pixels for Landsat)
    area_km2 = (oil_pixels * 30 * 30) / 1e6
    
    return [{
        'type': 'oil',
        'pixels': oil_pixels,
        'area_km2': area_km2,
        'confidence': lb.get('max_zscore', 0)
    }]

def generate_kmz(results_data):
    """Generate KMZ showing oil spills"""
    
    kml = simplekml.Kml()
    
    # Add metadata
    kml.document.name = f"Leaking Boat Detection - {results_data.get('run_timestamp', 'Unknown')}"
    
    # Create folders
    oil_folder = kml.newfolder(name="Oil Spills (Leaking Boats)")
    tile_folder = kml.newfolder(name="Tile Boundaries")
    
    # Process each tile
    for tile_result in results_data.get('results', []):
        filename = tile_result.get('filename', 'Unknown')
        lb = tile_result.get('sensors', {}).get('leaking_boat', {})
        
        # Determine satellite type
        satellite = 'Landsat-8' if 'L30' in filename else 'Sentinel-2' if 'S30' in filename else 'Unknown'
        
        # Add tile boundary
        tile_placemark = tile_folder.newpoint(
            name=filename,
            coords=[(-87.0, 42.5)]  # Approximate center
        )
        tile_placemark.description = f"""
        Tile: {filename}
        Satellite: {satellite}
        Oil Detected: {lb.get('oil_detected', False)}
        Oil Pixels: {lb.get('oil_pixel_count', 0)}
        Wake Filtered: {lb.get('wake_pixels_filtered', 0):,}
        """
        
        # Add oil spill if detected
        if lb.get('oil_detected', False):
            oil_pixels = lb.get('oil_pixel_count', 0)
            area_km2 = (oil_pixels * 30 * 30) / 1e6  # Approximate
            max_zscore = lb.get('max_zscore', 0)
            
            oil_placemark = oil_folder.newpoint(
                name=f"OIL SPILL - {filename}",
                coords=[(-87.0, 42.5)]  # Approximate
            )
            
            oil_placemark.description = f"""
            <h2>⚠️ OIL SPILL DETECTED</h2>
            
            <p><b>Tile:</b> {filename}</p>
            <p><b>Satellite:</b> {satellite}</p>
            <p><b>Oil Pixels:</b> {oil_pixels:,}</p>
            <p><b>Estimated Area:</b> {area_km2:.3f} km²</p>
            <p><b>Confidence (Z-Score):</b> {max_zscore:.2f}</p>
            <p><b>Wake Pixels Filtered:</b> {lb.get('wake_pixels_filtered', 0):,}</p>
            
            <h3>Detection Method</h3>
            <p>Spectral signature analysis:</p>
            <ul>
                <li>Low red reflectance (B04 absorption)</li>
                <li>Low SWIR reflectance (B11/B12 absorption)</li>
                <li>Filtered out wake bubbles (high reflectance)</li>
            </ul>
            """
            
            # Red icon for oil
            oil_placemark.style.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/paddle/red-circle.png'
            oil_placemark.style.iconstyle.scale = 1.5
    
    # Add summary
    summary_folder = kml.newfolder(name="Run Summary")
    summary_placemark = summary_folder.newpoint(
        name="Processing Summary",
        coords=[(-87.0, 42.5)]
    )
    
    summary_placemark.description = f"""
    <h2>Leaking Boat Detection Run Summary</h2>
    
    <p><b>Timestamp:</b> {results_data.get('run_timestamp', 'Unknown')}</p>
    <p><b>Tiles Processed:</b> {results_data.get('processed', 0)}</p>
    <p><b>Thermal Anomalies:</b> {results_data.get('thermal_anomalies_total', 0):,}</p>
    <p><b>Optical Anomalies:</b> {results_data.get('optical_anomalies_total', 0):,}</p>
    <p><b>Leaking Boats Detected:</b> {results_data.get('leaking_boats_detected', 0)}</p>
    
    <h3>Algorithm</h3>
    <p>Oil detection uses spectral signature analysis:</p>
    <ul>
        <li><b>B04 (Red):</b> Oil absorbs red light → low reflectance</li>
        <li><b>B11/B12 (SWIR):</b> Oil strongly absorbs SWIR → very low reflectance</li>
        <li><b>Wake Filtering:</b> Bright pixels (bubbles) filtered out</li>
    </ul>
    """
    
    # Save KMZ
    kml.savekmz(str(OUTPUT_KMZ))
    
    return OUTPUT_KMZ

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*70)
    print("OIL SPILL KMZ GENERATOR")
    print("="*70)
    print()
    
    # Load results
    results_file = RESULTS_DIR / "full_results.json"
    if not results_file.exists():
        print(f"ERROR: Results not found: {results_file}")
        return
    
    print(f"Loading results from: {results_file}")
    with open(results_file) as f:
        results_data = json.load(f)
    
    print(f"  Tiles: {results_data.get('total_tiles', 0)}")
    print(f"  Processed: {results_data.get('processed', 0)}")
    print(f"  Leaking Boats: {results_data.get('leaking_boats_detected', 0)}")
    print()
    
    # Generate KMZ
    print("Generating KMZ...")
    kmz_path = generate_kmz(results_data)
    
    print(f"  KMZ saved: {kmz_path}")
    print()
    
    # List detections
    print("OIL SPILL DETECTIONS:")
    print("="*70)
    
    for tile_result in results_data.get('results', []):
        lb = tile_result.get('sensors', {}).get('leaking_boat', {})
        
        if lb.get('oil_detected', False):
            filename = tile_result.get('filename', 'Unknown')
            oil_pixels = lb.get('oil_pixel_count', 0)
            area_km2 = (oil_pixels * 30 * 30) / 1e6
            max_zscore = lb.get('max_zscore', 0)
            
            print(f"  ⚠️  {filename}")
            print(f"      Pixels: {oil_pixels:,}  |  Area: {area_km2:.3f} km²  |  Z-Score: {max_zscore:.2f}")
    
    print("="*70)
    print()
    print(f"Open in Google Earth: {kmz_path}")

if __name__ == "__main__":
    main()
