#!/usr/bin/env python3
"""
Full Lake Michigan Scan — CPU/GPU Hybrid Z-Score to KMZ Output
Processes all TIFFs with 8-point anchor-lock calibration and exports to Google Earth

GPU: CuPy on M2200 (Quadro M2200, Maxwell sm_52, 4GB)
     Falls back to NumPy CPU if CuPy unavailable or nvcc-dependent.
CPU: NumPy with rasterio warp for CRS transforms.
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

import rasterio
from rasterio.transform import xy
from pyproj import Transformer

# Try to import CuPy for GPU acceleration
# We test in a subprocess with timeout to avoid hanging on nvcc
HAS_GPU = False
cp = None

def _test_cupy():
    """Test if CuPy works without nvcc dependency (runs in subprocess)."""
    try:
        import cupy as _cp
        _test = _cp.array([1.0, 2.0, 3.0])
        _s = float(_cp.sum(_test))
        return abs(_s - 6.0) < 0.001
    except Exception:
        return False

if __name__ != '__main__':
    # When imported as module, test CuPy via subprocess with timeout
    import subprocess
    result = subprocess.run(
        [sys.executable, '-c',
         'import cupy; t=cupy.array([1,2,3]); print(float(cupy.sum(t)))'],
        capture_output=True, text=True, timeout=8
    )
    if result.returncode == 0 and '6.0' in result.stdout:
        import cupy as cp
        HAS_GPU = True
        print("  GPU: CuPy enabled (M2200 Quadro)")
    else:
        print(f"  GPU: CuPy unavailable — using NumPy CPU fallback")

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
    """
    DEPRECATED: This function blends detections toward anchor centroids,
    which systematically biases results toward the geometric center of the lake.

    The process_tiff_with_coords() function uses proper CRS-aware geotransform
    via rasterio.warp.transform — no anchor math needed.

    This function is kept as a no-op for backward compatibility.
    Do NOT use it — it returns coordinates unchanged.
    """
    # No-op: return coordinates unchanged
    return lat, lon

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

        # Stats on CPU (fast single pass)
        mean_val = float(np.mean(data))
        std_val = float(np.std(data))

        if HAS_GPU:
            # GPU path: upload to M2200, compute z-score on GPU
            data_gpu = cp.asarray(data)
            zscore_gpu = (data_gpu - mean_val) / std_val
            cp.cuda.Stream.null.synchronize()
            anomaly_mask = cp.abs(zscore_gpu) > threshold
            anomaly_count = int(cp.count_nonzero(anomaly_mask))
            # Move mask to CPU for index extraction
            mask_cpu = cp.asnumpy(anomaly_mask)
            zscores_all = cp.asnumpy(zscore_gpu[mask_cpu])
        else:
            # CPU path: pure NumPy
            zscore = (data - mean_val) / std_val
            anomaly_mask = np.abs(zscore) > threshold
            anomaly_count = int(np.count_nonzero(anomaly_mask))
            mask_cpu = anomaly_mask
            zscores_all = zscore[mask_cpu]
        
        print(f"    Anomalies: {anomaly_count}")

        if anomaly_count == 0:
            return []

        # Get top 50 anomalies — index extraction on CPU
        rows, cols = np.where(mask_cpu)
        # Sort by Z-score magnitude on CPU
        sorted_idx = np.argsort(np.abs(zscores_all))[::-1][:50]
        
        detections = []
        for idx in sorted_idx:
            row = int(rows[idx])
            col = int(cols[idx])
            zscore = float(zscores_all[idx])
            
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
    # Cross-platform data paths — use env var, Syncthing folder, or current dir
    data_base = Path(os.environ.get('CESAROPS_DATA_DIR', Path(__file__).parent / 'data'))
    search_paths = [
        data_base / 'rossa_forensic_cache',
        data_base / 'sentinel_hunt_cache',
        data_base / 'bagrecovery' / 'outputs' / 'rossa_forensic_cache',
        data_base / 'bagrecovery' / 'sentinel_hunt' / 'cache',
        # Fallback: any 'data' or 'outputs' subdirectory in parent dirs
        Path(__file__).parent.parent / 'cesarops-data' / 'tiffs',
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
