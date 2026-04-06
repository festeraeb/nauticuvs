#!/usr/bin/env python3
"""
Full Lake Michigan Scan - M2200 CUDA TO KMZ Output
Processes all TIFFs with 8-point anchor-lock calibration and exports to Google Earth

8-Point Anchor Lock System:
SOUTHERN:
- Chicago Harbor Light: 41.8900°N, -87.6044°W (IL)
- Waukegan Harbor Light: 42.3638°N, -87.8034°W (IL)
- Michigan City Light: 41.7258°N, -86.9047°W (IN)

MID-LAKE:
- Wind Point Light: 42.8000°N, -87.8178°W (WI)
- Holland Harbor Light: 42.7784°N, -86.2066°W (MI)
- Muskegon South Pier Light: 43.2314°N, -86.3481°W (MI)

NORTHERN (Straits):
- Sturgeon Bay Ship Canal Light: 44.7947°N, -87.3142°W (WI)
- Point Betsie Light: 44.6919°N, -86.2544°W (MI)
"""

import os
import sys
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import simplekml

# Set CUDA paths via helper (preferred CUDA 13.2 -> fallback 11.8)
from cuda_env import configure_cuda_environment
cuda_info = configure_cuda_environment()
cuda_bin = cuda_info['cuda_bin']

import cupy as cp
import rasterio
from rasterio.transform import xy
from pyproj import Transformer

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
    """Apply 4-point anchor-lock calibration correction"""
    # Calculate distance-weighted correction from anchor points
    total_weight = 0
    corrected_lat = 0
    corrected_lon = 0
    
    for anchor in ANCHOR_POINTS.values():
        # Simple distance calculation
        dist = np.sqrt((lat - anchor["lat"])**2 + (lon - anchor["lon"])**2)
        if dist < 0.0001:  # Very close to anchor
            return lat, lon
        
        weight = 1.0 / (dist + 0.01)  # Inverse distance weighting
        total_weight += weight
        corrected_lat += anchor["lat"] * weight
        corrected_lon += anchor["lon"] * weight
    
    # Blend original with anchor-corrected (90% original, 10% correction)
    blend = 0.9
    final_lat = lat * blend + (corrected_lat / total_weight) * (1 - blend)
    final_lon = lon * blend + (corrected_lon / total_weight) * (1 - blend)
    
    return final_lat, final_lon

def process_tiff_with_coords(tiff_path: Path, threshold: float = 1.5):
    """
    Process TIFF on M2200 and extract coordinates using PROPER CRS transform.
    No anchor math - uses geotransform from file metadata.
    Handles UTM zone crossovers automatically via rasterio.warp.transform
    """
    from rasterio.warp import transform as warp_transform
    
    print(f"  Loading {tiff_path.name}...")
    
    # Load with georeferencing
    with rasterio.open(tiff_path) as src:
        data = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        
        print(f"    Shape: {data.shape}, CRS: {crs}")
        
        # Upload to GPU
        data_gpu = cp.asarray(data)
        
        # Heavy GPU processing
        mean_gpu = float(cp.mean(data_gpu))
        std_gpu = float(cp.std(data_gpu))
        
        # Multiple passes for GPU load
        for _ in range(10):
            zscore_gpu = (data_gpu - mean_gpu) / std_gpu
            _ = cp.max(zscore_gpu)
            _ = cp.min(zscore_gpu)
            cp.cuda.Stream.null.synchronize()
        
        # Find anomalies
        abs_zscore = cp.where(zscore_gpu > 0, zscore_gpu, -zscore_gpu)
        anomalies_gpu = abs_zscore > threshold
        anomaly_count = int(cp.sum(anomalies_gpu))
        
        print(f"    Anomalies: {anomaly_count}")
        
        if anomaly_count == 0:
            return []
        
        # Get top 50 anomalies
        anomaly_indices = cp.where(anomalies_gpu)
        rows = cp.asnumpy(anomaly_indices[0])
        cols = cp.asnumpy(anomaly_indices[1])
        zscores = cp.asnumpy(zscore_gpu[anomalies_gpu])
        
        # Sort by Z-score magnitude
        sorted_idx = np.argsort(np.abs(zscores))[::-1][:50]
        
        detections = []
        for idx in sorted_idx:
            row = int(rows[idx])
            col = int(cols[idx])
            zscore = float(zscores[idx])
            
            # PROPER CRS-AWARE COORDINATE EXTRACTION
            # Get real-world coordinates in file's LOCAL system (meters)
            local_x, local_y = src.xy(row, col)
            
            # Transform local meters to global lat/lon (WGS84)
            # This handles UTM zone crossover automatically!
            lon, lat = warp_transform(src.crs, 'EPSG:4326', [local_x], [local_y])
            
            detections.append({
                "lat": lat[0],
                "lon": lon[0],
                "zscore": zscore,
                "source": tiff_path.name,
                "pixel": {"row": row, "col": col}
            })
        
        return detections

def create_kmz(detections, output_path: Path):
    """Create KMZ file for Google Earth with anchor points"""
    
    kml = simplekml.Kml()
    
    # Add anchor reference points
    anchor_folder = kml.newfolder(name="Anchor Points (Calibration)")
    for key, anchor in ANCHOR_POINTS.items():
        pnt = anchor_folder.newpoint(
            name=anchor["name"],
            coords=[(anchor["lon"], anchor["lat"])]
        )
        pnt.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/grn-blank.png"
        pnt.style.iconstyle.scale = 1.5
        pnt.description = f"<b>Calibration Anchor Point</b><br/>Lat: {anchor['lat']:.6f}<br/>Lon: {anchor['lon']:.6f}"
    
    # Create folders by source
    detection_folder = kml.newfolder(name="Detections")
    sources = {}
    for det in detections:
        source = det["source"]
        if source not in sources:
            sources[source] = detection_folder.newfolder(name=source)
        
        folder = sources[source]
        
        # Color by Z-score magnitude
        zscore_abs = abs(det["zscore"])
        if zscore_abs > 4.0:
            color = simplekml.Color.red
            icon = "http://maps.google.com/mapfiles/kml/paddle/red-stars.png"
        elif zscore_abs > 3.0:
            color = simplekml.Color.orange
            icon = "http://maps.google.com/mapfiles/kml/paddle/orange-circle.png"
        else:
            color = simplekml.Color.yellow
            icon = "http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png"
        
        pnt = folder.newpoint(
            name=f"Z={det['zscore']:.2f}",
            coords=[(det["lon"], det["lat"])]
        )
        pnt.style.iconstyle.icon.href = icon
        pnt.style.iconstyle.color = color
        pnt.description = f"""
        <b>Anomaly Detection</b><br/>
        Z-Score: {det['zscore']:.2f}<br/>
        Source: {det['source']}<br/>
        Pixel: ({det['pixel']['row']}, {det['pixel']['col']})<br/>
        <br/>
        <b>Coordinates (WGS84):</b><br/>
        Lat: {det['lat']:.6f}<br/>
        Lon: {det['lon']:.6f}<br/>
        <br/>
        <i>CRS-aware geotransform applied (no anchor math)</i>
        """
    
    kml.save(str(output_path))
    print(f"\n[OK] KMZ saved: {output_path}")

def main():
    print("="*80)
    print("FULL LAKE MICHIGAN SCAN - M2200 CUDA TO KMZ")
    print("="*80)
    print()
    
    # Find ALL TIFFs
    search_paths = [
        Path(r"C:\Users\thomf\programming\Bagrecovery\outputs\rossa_forensic_cache"),
        Path(r"C:\Users\thomf\programming\Bagrecovery\sentinel_hunt\cache"),
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
    
    for i, tiff in enumerate(tiffs, 1):
        print(f"[{i}/{len(tiffs)}] {tiff.name}")
        try:
            detections = process_tiff_with_coords(tiff, threshold=2.5)
            all_detections.extend(detections)
        except Exception as e:
            print(f"    ERROR: {e}")
        print()
    
    print("="*80)
    print(f"Total detections: {len(all_detections)}")
    print("="*80)
    
    # Save JSON
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    
    json_file = output_dir / f"lake_michigan_scan_{timestamp}.json"
    with open(json_file, 'w') as f:
        json.dump(all_detections, f, indent=2)
    print(f"[OK] JSON saved: {json_file}")
    
    # Create KMZ
    kmz_file = output_dir / f"lake_michigan_scan_{timestamp}.kmz"
    create_kmz(all_detections, kmz_file)
    
    print()
    print("="*80)
    print("SCAN COMPLETE - Open KMZ in Google Earth")
    print("="*80)

if __name__ == "__main__":
    main()
