#!/usr/bin/env python3
"""
TRIPLE LOCK FUSION - Multi-Sensor Anomaly Verification

Implements the CESAROPS "Triple Lock" theory:
1. Thermal (B10/B11) - Cold-sink detection (steel mass underwater)
2. SAR (VV/VH) - Heavy metal density ratio
3. Optical (B08/B04) - Aluminum specular glint

When all 3 sensors agree at same location = HIGH CONFIDENCE target

Usage: python triple_lock_fusion.py --sensitivity 1.5 --output outputs/triple_lock.kmz
"""

import os
import sys
import json
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import math

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

# Try to import CuPy for GPU
try:
    import cupy as cp
    HAS_CUPY = True
    print("[+] CuPy available - GPU processing enabled")
except ImportError:
    HAS_CUPY = False
    print("[!] CuPy not available - using CPU fallback")

# Try to import rasterio for georeferencing
try:
    import rasterio
    from rasterio.warp import transform as warp_transform
    HAS_RASTERIO = True
    print("[+] Rasterio available - georeferencing enabled")
except ImportError:
    HAS_RASTERIO = False
    print("[!] Rasterio not available - no georeferencing")

try:
    import simplekml
    HAS_SIMPLEKML = True
except ImportError:
    HAS_SIMPLEKML = False
    print("[!] simplekml not available")


# =============================================================================
# SENSOR PROCESSING
# =============================================================================

def process_thermal_for_coldsink(tiff_path: Path, threshold: float = 2.5):
    """
    Process thermal band (Landsat B10/B11) for cold-sink anomalies.
    Steel masses underwater create cold thermal signatures.
    """
    print(f"  Thermal: {tiff_path.name}")
    
    if not HAS_RASTERIO:
        return []
    
    with rasterio.open(tiff_path) as src:
        data = src.read(1).astype(np.float32)
        
        # Mask nodata (-9999)
        nodata_mask = data == -9999
        data[nodata_mask] = np.nan
        
        # Apply HLS B10 conversion: DN * 0.1 + 133 = Kelvin
        if np.nanmin(data) > 1000:  # Scaled integers
            data = data * 0.1 + 133.0
        
        # Calculate stats (mask nodata and invalid)
        valid_mask = np.isfinite(data) & (data > 200) & (data < 400)
        valid_data = data[valid_mask]
        
        if len(valid_data) == 0:
            return []
        
        mean = float(np.mean(valid_data))
        std = float(np.std(valid_data))
        
        # Calculate Z-scores
        zscore = np.zeros_like(data)
        zscore[valid_mask] = (data[valid_mask] - mean) / std
        
        # Cold-sink = NEGATIVE Z-scores (colder than surroundings)
        coldsink_mask = zscore < -threshold
        coldsink_count = int(np.sum(coldsink_mask))
        
        print(f"    Cold-sink anomalies (Z < -{threshold}): {coldsink_count}")
        
        if coldsink_count == 0:
            return []
        
        # Extract anomalies
        coords = np.where(coldsink_mask)
        anomalies = []
        
        for row, col in zip(coords[0], coords[1]):
            local_x, local_y = src.xy(row, col)
            lon, lat = warp_transform(src.crs, 'EPSG:4326', [local_x], [local_y])
            
            anomalies.append({
                'lat': lat[0],
                'lon': lon[0],
                'zscore': float(zscore[row, col]),
                'sensor': 'thermal',
                'source': tiff_path.name,
                'pixel': {'row': int(row), 'col': int(col)},
            })
        
        return anomalies


def process_sar_for_steel(tiff_path: Path, threshold: float = 2.0):
    """
    Process SAR VV/VH ratio for heavy steel detection.
    Steel masses have high VV/VH ratio (vertical polarization preferred).
    """
    print(f"  SAR: {tiff_path.name}")
    
    if not HAS_RASTERIO:
        return []
    
    with rasterio.open(tiff_path) as src:
        data = src.read(1).astype(np.float32)
        
        # Mask nodata
        nodata_mask = data == -9999
        data[nodata_mask] = np.nan
        
        # Calculate stats
        valid_mask = np.isfinite(data) & (data > 0)
        valid_data = data[valid_mask]
        
        if len(valid_data) == 0:
            return []
        
        mean = float(np.mean(valid_data))
        std = float(np.std(valid_data))
        
        # Calculate Z-scores
        zscore = np.zeros_like(data)
        zscore[valid_mask] = (data[valid_mask] - mean) / std
        
        # Steel mass = HIGH positive Z-scores (strong VV return)
        steel_mask = zscore > threshold
        steel_count = int(np.sum(steel_mask))
        
        print(f"    Steel anomalies (Z > {threshold}): {steel_count}")
        
        if steel_count == 0:
            return []
        
        # Extract anomalies
        coords = np.where(steel_mask)
        anomalies = []
        
        for row, col in zip(coords[0], coords[1]):
            local_x, local_y = src.xy(row, col)
            lon, lat = warp_transform(src.crs, 'EPSG:4326', [local_x], [local_y])
            
            anomalies.append({
                'lat': lat[0],
                'lon': lon[0],
                'zscore': float(zscore[row, col]),
                'sensor': 'sar',
                'source': tiff_path.name,
                'pixel': {'row': int(row), 'col': int(col)},
            })
        
        return anomalies


def process_optical_for_aluminum(tiff_path: Path, threshold: float = 2.0):
    """
    Process optical band (Sentinel-2 B08/B04) for aluminum glint.
    Aluminum creates specular reflection (high B08/B04 ratio).
    """
    print(f"  Optical: {tiff_path.name}")
    
    if not HAS_RASTERIO:
        return []
    
    with rasterio.open(tiff_path) as src:
        data = src.read(1).astype(np.float32)
        
        # Mask nodata
        nodata_mask = data == -9999
        data[nodata_mask] = np.nan
        
        # Calculate stats
        valid_mask = np.isfinite(data) & (data > 0)
        valid_data = data[valid_mask]
        
        if len(valid_data) == 0:
            return []
        
        mean = float(np.mean(valid_data))
        std = float(np.std(valid_data))
        
        # Calculate Z-scores
        zscore = np.zeros_like(data)
        zscore[valid_mask] = (data[valid_mask] - mean) / std
        
        # Aluminum glint = HIGH positive Z-scores (bright specular reflection)
        glint_mask = zscore > threshold
        glint_count = int(np.sum(glint_mask))
        
        print(f"    Aluminum glint anomalies (Z > {threshold}): {glint_count}")
        
        if glint_count == 0:
            return []
        
        # Extract anomalies
        coords = np.where(glint_mask)
        anomalies = []
        
        for row, col in zip(coords[0], coords[1]):
            local_x, local_y = src.xy(row, col)
            lon, lat = warp_transform(src.crs, 'EPSG:4326', [local_x], [local_y])
            
            anomalies.append({
                'lat': lat[0],
                'lon': lon[0],
                'zscore': float(zscore[row, col]),
                'sensor': 'optical',
                'source': tiff_path.name,
                'pixel': {'row': int(row), 'col': int(col)},
            })
        
        return anomalies


# =============================================================================
# TRIPLE LOCK FUSION
# =============================================================================

def fuse_triple_lock(thermal_anomalies, sar_anomalies, optical_anomalies, tolerance_m=50):
    """
    Fuse anomalies from 3 sensors using spatial proximity.
    Triple lock = all 3 sensors agree within tolerance.
    
    Args:
        thermal_anomalies: List of thermal cold-sink detections
        sar_anomalies: List of SAR steel detections
        optical_anomalies: List of optical aluminum detections
        tolerance_m: Maximum distance for "same location" (meters)
    
    Returns:
        List of fused detections with lock_level (1/2/3)
    """
    print(f"\nFusing triple lock (tolerance: {tolerance_m}m)...")
    
    # Combine all anomalies
    all_anomalies = []
    
    for a in thermal_anomalies:
        all_anomalies.append({**a, 'lock_sensors': ['thermal']})
    for a in sar_anomalies:
        all_anomalies.append({**a, 'lock_sensors': ['sar']})
    for a in optical_anomalies:
        all_anomalies.append({**a, 'lock_sensors': ['optical']})
    
    print(f"  Total single-sensor anomalies: {len(all_anomalies)}")
    
    # Cluster by proximity
    # Convert tolerance from meters to approximate degrees
    tol_deg = tolerance_m / 111320.0  # ~111km per degree
    
    clusters = []
    used = [False] * len(all_anomalies)
    
    for i, anom in enumerate(all_anomalies):
        if used[i]:
            continue
        
        # Start new cluster
        cluster = [anom]
        used[i] = True
        
        # Find nearby anomalies
        for j, other in enumerate(all_anomalies):
            if used[j]:
                continue
            
            # Calculate distance
            dlat = anom['lat'] - other['lat']
            dlon = anom['lon'] - other['lon']
            dist_deg = math.sqrt(dlat**2 + dlon**2)
            
            if dist_deg < tol_deg:
                cluster.append(other)
                used[j] = True
        
        clusters.append(cluster)
    
    # Create fused detections
    fused = []
    
    for cluster in clusters:
        sensors = set()
        for a in cluster:
            sensors.update(a['lock_sensors'])
        
        # Calculate centroid
        avg_lat = np.mean([a['lat'] for a in cluster])
        avg_lon = np.mean([a['lon'] for a in cluster])
        avg_zscore = np.mean([abs(a['zscore']) for a in cluster])
        max_zscore = max([abs(a['zscore']) for a in cluster])
        
        # Determine lock level
        lock_level = len(sensors)
        lock_type = f"{lock_level}-LOCK"
        
        # Get representative sources
        sources = list(set([a['source'] for a in cluster]))
        
        fused.append({
            'lat': float(avg_lat),
            'lon': float(avg_lon),
            'lock_level': lock_level,
            'lock_type': lock_type,
            'sensors': list(sensors),
            'sensor_count': len(sensors),
            'avg_zscore': float(avg_zscore),
            'max_zscore': float(max_zscore),
            'confidence': float(avg_zscore * lock_level),  # Weight by lock level
            'num_anomalies': len(cluster),
            'sources': sources,
        })
    
    # Sort by confidence (triple locks first, then by Z-score)
    fused.sort(key=lambda x: (x['sensor_count'], x['confidence']), reverse=True)
    
    print(f"  Fused clusters: {len(fused)}")
    print(f"  Triple locks (3 sensors): {len([f for f in fused if f['sensor_count'] == 3])}")
    print(f"  Double locks (2 sensors): {len([f for f in fused if f['sensor_count'] == 2])}")
    print(f"  Single sensor: {len([f for f in fused if f['sensor_count'] == 1])}")
    
    return fused


# =============================================================================
# KMZ EXPORT
# =============================================================================

def create_triple_lock_kmz(fused_detections, output_path: Path):
    """Create KMZ with color-coded lock levels"""
    
    if not HAS_SIMPLEKML:
        print("  simplekml not available, skipping KMZ")
        return
    
    print(f"\nCreating KMZ with {len(fused_detections)} fused detections...")
    
    kml = simplekml.Kml()
    
    # Create folders by lock level
    folders = {
        3: kml.newfolder(name="🔴 TRIPLE LOCK (High Confidence)"),
        2: kml.newfolder(name="🟡 DOUBLE LOCK (Medium Confidence)"),
        1: kml.newfolder(name="🟢 SINGLE SENSOR (Low Confidence)"),
    }
    
    # Colors
    colors = {
        3: simplekml.Color.red,
        2: simplekml.Color.yellow,
        1: simplekml.Color.green,
    }
    
    # Add each detection
    for det in fused_detections[:500]:  # Limit for KMZ size
        lock_level = det['lock_level']
        folder = folders[lock_level]
        color = colors[lock_level]
        
        # Icon size by confidence
        scale = min(2.0, 0.5 + det['confidence'] / 10)
        
        pnt = folder.newpoint(
            name=f"{det['lock_type']} Z={det['max_zscore']:.1f}",
            coords=[(det['lon'], det['lat'])]
        )
        
        pnt.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/red-circle.png"
        pnt.style.iconstyle.color = color
        pnt.style.iconstyle.scale = scale
        
        # Description
        sensors_str = ', '.join(det['sensors'])
        sources_str = '<br/>'.join(det['sources'][:3])
        
        pnt.description = f"""
        <![CDATA[
        <h3>{det['lock_type']} Detection</h3>
        <table>
            <tr><td><b>Lock Level:</b></td><td>{det['lock_level']} sensors</td></tr>
            <tr><td><b>Sensors:</b></td><td>{sensors_str}</td></tr>
            <tr><td><b>Max Z-Score:</b></td><td>{det['max_zscore']:.2f}</td></tr>
            <tr><td><b>Avg Z-Score:</b></td><td>{det['avg_zscore']:.2f}</td></tr>
            <tr><td><b>Confidence:</b></td><td>{det['confidence']:.2f}</td></tr>
            <tr><td><b>Latitude:</b></td><td>{det['lat']:.6f}</td></tr>
            <tr><td><b>Longitude:</b></td><td>{det['lon']:.6f}</td></tr>
        </table>
        <br/>
        <b>Sources:</b><br/>
        {sources_str}
        <br/><br/>
        <i>Triple Lock Theory: Thermal + SAR + Optical agreement</i>
        ]]>
        """
    
    # Save
    kml.save(str(output_path))
    print(f"[OK] KMZ saved: {output_path}")


# =============================================================================
# MAIN
# =============================================================================

def run_triple_lock_scan():
    """Run triple lock fusion scan"""
    
    print("="*80)
    print("TRIPLE LOCK FUSION - Multi-Sensor Anomaly Verification")
    print("="*80)
    print()
    
    # Configuration - Focus on LOCATION overlap, not date
    # Wrecks haven't moved in 100+ years - different sensors work better on different days!
    thermal_threshold = 2.5  # Cold-sink threshold
    sar_threshold = 2.5      # Steel threshold  
    optical_threshold = 2.5  # Aluminum threshold
    fuse_tolerance_m = 300   # Meters for "same location" (increased for large vessels)
    
    print(f"Configuration:")
    print(f"  Thermal threshold: Z < -{thermal_threshold} (cold-sink)")
    print(f"  SAR threshold: Z > {sar_threshold} (steel mass)")
    print(f"  Optical threshold: Z > {optical_threshold} (aluminum glint)")
    print(f"  Fuse tolerance: {fuse_tolerance_m}m (for large vessel detection)")
    print()
    print(f"  NOTE: Different dates OK - wrecks haven't moved!")
    print(f"        Thermal (2021) + Optical (2025) + SAR (any) = Triple Lock")
    print()
    
    # Data directories
    data_dir = Path(r"wreckhunter2000\data\cache\census_raw")
    landsat_dir = data_dir / "2021_low_water"
    sentinel_dir = data_dir / "2025_rossa"
    output_dir = Path("outputs/triple_lock")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Data directory: {data_dir}")
    print(f"Output directory: {output_dir}")
    print()
    
    # Find files by sensor type
    thermal_files = []
    sar_files = []
    optical_files = []
    
    if landsat_dir.exists():
        thermal_files.extend(landsat_dir.glob("*B10.tif"))
        thermal_files.extend(landsat_dir.glob("*B11.tif"))
    
    if sentinel_dir.exists():
        optical_files.extend(sentinel_dir.glob("*B04.tif"))
        optical_files.extend(sentinel_dir.glob("*B08.tif"))
    
    # Look for SAR files
    for f in data_dir.rglob("*.tif"):
        if 'vv' in f.name.lower() or 'vh' in f.name.lower():
            sar_files.append(f)
    
    print(f"Thermal files: {len(thermal_files)}")
    print(f"SAR files: {len(sar_files)} (NOTE: Download SAR for full triple lock)")
    print(f"Optical files: {len(optical_files)}")
    print()
    
    # Process each sensor type
    all_thermal = []
    all_sar = []
    all_optical = []
    
    print("=== THERMAL PROCESSING (Cold-Sink) ===")
    for tiff in thermal_files[:10]:
        anomalies = process_thermal_for_coldsink(tiff, thermal_threshold)
        # Limit to top 2000 per file by Z-score magnitude
        anomalies.sort(key=lambda x: abs(x['zscore']), reverse=True)
        all_thermal.extend(anomalies[:2000])
    
    print(f"\nTotal thermal anomalies (limited): {len(all_thermal)}")
    
    print("\n=== SAR PROCESSING (Steel Mass) ===")
    if sar_files:
        for tiff in sar_files[:10]:
            anomalies = process_sar_for_steel(tiff, sar_threshold)
            anomalies.sort(key=lambda x: abs(x['zscore']), reverse=True)
            all_sar.extend(anomalies[:2000])
    else:
        print("  No SAR files found - skipping (download Sentinel-1 for full triple lock)")
    
    print(f"\nTotal SAR anomalies (limited): {len(all_sar)}")
    
    print("\n=== OPTICAL PROCESSING (Aluminum Glint) ===")
    for tiff in optical_files[:10]:
        anomalies = process_optical_for_aluminum(tiff, optical_threshold)
        # Limit to top 2000 per file by Z-score magnitude
        anomalies.sort(key=lambda x: abs(x['zscore']), reverse=True)
        all_optical.extend(anomalies[:2000])
    
    print(f"\nTotal optical anomalies (limited): {len(all_optical)}")
    
    # Fuse triple lock
    print("\n=== TRIPLE LOCK FUSION ===")
    fused = fuse_triple_lock(all_thermal, all_sar, all_optical, fuse_tolerance_m)
    
    # Save results
    print("\n=== SAVING RESULTS ===")
    
    # Save JSON
    json_path = output_dir / f"triple_lock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, 'w') as f:
        json.dump({
            'scan_date': datetime.now().isoformat(),
            'thresholds': {
                'thermal': thermal_threshold,
                'sar': sar_threshold,
                'optical': optical_threshold,
            },
            'fuse_tolerance_m': fuse_tolerance_m,
            'total_fused': len(fused),
            'triple_locks': len([f for f in fused if f['sensor_count'] == 3]),
            'double_locks': len([f for f in fused if f['sensor_count'] == 2]),
            'single_sensor': len([f for f in fused if f['sensor_count'] == 1]),
            'detections': fused,
        }, f, indent=2)
    
    print(f"  Saved: {json_path}")
    
    # Save KMZ
    kmz_path = output_dir / f"triple_lock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.kmz"
    create_triple_lock_kmz(fused, kmz_path)
    
    # Print top detections
    print("\n=== TOP 20 TRIPLE LOCK DETECTIONS ===")
    for i, det in enumerate(fused[:20], 1):
        sensors = ', '.join(det['sensors'])
        print(f"  [{i:2d}] {det['lock_type']:12s} | Z={det['max_zscore']:5.1f} | "
              f"{det['lat']:.6f}N, {abs(det['lon']):.6f}W | "
              f"Sensors: {sensors}")
    
    print("\n" + "="*80)
    print("TRIPLE LOCK SCAN COMPLETE")
    print("="*80)
    print(f"Total fused detections: {len(fused)}")
    print(f"Triple locks (3 sensors): {len([f for f in fused if f['sensor_count'] == 3])}")
    print(f"Outputs: {output_dir}")
    print("="*80)
    
    return fused, output_dir


if __name__ == '__main__':
    fused, output_dir = run_triple_lock_scan()
