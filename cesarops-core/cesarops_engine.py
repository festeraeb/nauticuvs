#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CESAROPS Engine - Local Test Version
GPU (M2200) + TPU (remote Xenon) processing engine

This runs on your laptop. Same code will run on Xenon (with local TPU).
"""

import sys
import sqlite3
import time
import requests
import json
from pathlib import Path
from datetime import datetime
from PIL import Image
import numpy as np

# Fix Windows console encoding for Unicode characters
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Import our TPU client
from tpu_client import TPUClient

# ============================================================================
# GPU BACKEND SELECTION
# ============================================================================
# Current: PURE CUDA (direct CuPy for M2200/P1000)
# Future: Uncomment wgpu for AMD/Intel support
#
# For AMD GPU (ROCm) or Intel ARC:
#   import wgpu
#   Use wgpu compute shaders instead of CuPy
#
# For now: CUDA only (NVIDIA GPUs)
USE_CUDA = True
USE_WGPU = False  # Uncomment when AMD/Intel support needed
# ============================================================================

# ============================================================================
# CONFIGURATION
# ============================================================================

# Portable drive OR local wreckhunter2000 folder
# Check for portable drive first, fall back to local
DRIVE_PATH = Path(r"c:\Users\thomf\programming\cesarops-wreckhunter build\wreckhunter2000")
DB_PATH = DRIVE_PATH / "LAKE_MICHIGAN_CENSUS_2026.db"

# If portable drive is plugged in, use that instead
for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
    portable_db = Path(f"{letter}:\\LAKE_MICHIGAN_CENSUS_2026.db")
    if portable_db.exists():
        print(f"✓ Found portable drive: {portable_db}")
        DB_PATH = portable_db
        DRIVE_PATH = portable_db.parent
        break

# TPU Server
# Laptop: points to Xenon (10.0.0.55)
# Xenon: points to localhost
TPU_SERVER_URL = "http://localhost:5001"  # Change to "http://10.0.0.55:5001" for remote

# Cloudflare API (command queue)
CLOUDFLARE_API = "https://your-worker.your-subdomain.workers.dev"

# GPU processing (use existing CUDA test code)
USE_GPU = True

# ============================================================================
# DATABASE OPERATIONS
# ============================================================================

def init_db():
    """Initialize database if needed"""
    if not DB_PATH.exists():
        print(f"📁 Creating database: {DB_PATH}")
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Minimal schema
        cursor.executescript('''
            CREATE TABLE IF NOT EXISTS stationary_anchors (
                id INTEGER PRIMARY KEY,
                lat REAL,
                lon REAL,
                triple_lock_status TEXT,
                combined_score REAL
            );
            
            CREATE TABLE IF NOT EXISTS new_arrivals (
                id INTEGER PRIMARY KEY,
                lat REAL,
                lon REAL,
                triple_lock_status TEXT,
                score REAL
            );
            
            CREATE TABLE IF NOT EXISTS anomaly_hits (
                id INTEGER PRIMARY KEY,
                epoch_date TEXT,
                lat REAL,
                lon REAL,
                concept TEXT,
                score REAL,
                classification TEXT,
                scene_id TEXT,
                thermal_zscore REAL,
                ingested_at TEXT
            );
        ''')
        
        conn.commit()
        conn.close()
        print("✓ Database created")
    else:
        print(f"✓ Database found: {DB_PATH}")

def write_detection(lat, lon, score, classification, scene_id, thermal_zscore):
    """Write detection to database"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO anomaly_hits (
            epoch_date, lat, lon, concept, score, classification,
            scene_id, thermal_zscore, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().strftime('%Y-%m-%d'),
        lat,
        lon,
        'engine_test',
        score,
        classification,
        scene_id,
        thermal_zscore,
        datetime.now().isoformat()
    ))
    
    conn.commit()
    conn.close()

# ============================================================================
# TPU PROCESSING
# ============================================================================

def tpu_glint_check(tile_image, tile_id):
    """Check tile for glint/jitter using TPU"""
    tpu = TPUClient(TPU_SERVER_URL)
    
    result = tpu.check_glint_jitter(
        tile_image,
        meta={'tile_id': tile_id}
    )
    
    return result

# ============================================================================
# GPU PROCESSING
# ============================================================================

def gpu_process_tile(tile_image, tile_id):
    """
    Process tile with PURE CUDA (no wgpu abstraction)
    
    Direct CuPy implementation for maximum GPU performance
    Works on both M2200 (laptop) and P1000 (Xenon)
    """
    try:
        import cupy as cp
        
        # Convert to numpy, then to CuPy array (uploads to GPU VRAM)
        arr = np.array(tile_image.convert('L'), dtype=np.float32)
        arr_gpu = cp.asarray(arr)
        
        # Calculate statistics on GPU
        mean_val = cp.mean(arr_gpu)
        std_val = cp.std(arr_gpu)
        
        # Z-score normalization (core anomaly detection math)
        zscore = (arr_gpu - mean_val) / (std_val + 1e-6)
        
        # Find anomalies (|Z| > 2.5 = significant deviation)
        anomalies = cp.abs(zscore) > 2.5
        anomaly_count = int(cp.sum(anomalies))
        
        # Get top anomaly locations (for detailed reporting)
        if anomaly_count > 0:
            top_anomalies = cp.unravel_index(cp.argsort(cp.abs(zscore).ravel())[-10:], zscore.shape)
            max_zscore = float(cp.max(cp.abs(zscore)))
        else:
            max_zscore = 0.0
        
        # Copy results back to CPU (only small data, not full image)
        mean_val = float(mean_val)
        std_val = float(std_val)
        
        # Free GPU memory
        cp.get_default_memory_pool().free_all_blocks()
        
        return {
            'tile_id': tile_id,
            'anomaly_count': anomaly_count,
            'mean': mean_val,
            'std': std_val,
            'max_zscore': max_zscore,
            'gpu_used': True,
            'gpu_backend': 'cupy_cuda'
        }
        
    except ImportError:
        print("⚠ CuPy not installed, falling back to NumPy (CPU)")
        # CPU fallback
        arr = np.array(tile_image.convert('L'), dtype=np.float32)
        mean_val = np.mean(arr)
        std_val = np.std(arr)
        zscore = (arr - mean_val) / (std_val + 1e-6)
        anomalies = np.abs(zscore) > 2.5
        anomaly_count = int(np.sum(anomalies))
        
        return {
            'tile_id': tile_id,
            'anomaly_count': anomaly_count,
            'mean': float(mean_val),
            'std': float(std_val),
            'max_zscore': float(np.max(np.abs(zscore))),
            'gpu_used': False,
            'gpu_backend': 'cpu_numpy'
        }
        
    except Exception as e:
        print(f"⚠ CUDA processing failed: {e}")
        print("  Falling back to CPU")
        return gpu_process_tile(tile_image, tile_id)

# ============================================================================
# MAIN ENGINE LOOP
# ============================================================================

def process_tile(tile_path):
    """Process single tile through full pipeline"""
    print(f"\n📍 Processing: {tile_path.name}")
    
    # Load tile
    tile = Image.open(tile_path)
    
    # Step 1: TPU glint/jitter check
    print("  [1/3] TPU glint/jitter check...")
    tpu_result = tpu_glint_check(tile, tile_path.name)
    print(f"    Glint: {tpu_result['glint_score']:.3f}")
    print(f"    Jitter: {tpu_result['jitter_score']:.3f}")
    print(f"    PASS: {tpu_result['pass']}")
    print(f"    TPU Used: {tpu_result['used_tpu']}")
    print(f"    Took: {tpu_result['took_ms']:.2f}ms")
    
    if not tpu_result['pass']:
        print("  ✗ Tile failed quality check, skipping")
        return None
    
    # Step 2: GPU processing
    print("  [2/3] GPU processing...")
    gpu_result = gpu_process_tile(tile, tile_path.name)
    print(f"    Anomalies: {gpu_result['anomaly_count']}")
    print(f"    GPU Used: {gpu_result['gpu_used']}")
    
    # Step 3: Write to DB (mock detection)
    print("  [3/3] Writing to database...")
    if gpu_result['anomaly_count'] > 0:
        write_detection(
            lat=43.0,  # Mock coordinates
            lon=-86.0,
            score=0.8,
            classification='thermal_anomaly',
            scene_id=tile_path.name,
            thermal_zscore=float(gpu_result['anomaly_count'])
        )
        print(f"    ✓ Detection logged to DB")
    
    return {
        'tile': tile_path.name,
        'tpu': tpu_result,
        'gpu': gpu_result
    }

def run_test_pipeline():
    """Run test on sample tiles"""
    print("\n" + "="*70)
    print("CESAROPS ENGINE - TEST RUN")
    print("="*70)
    
    # Find sample tiles
    search_paths = [
        DRIVE_PATH / "wreckhunter2000" / "data",
        DRIVE_PATH / "outputs",
        Path(r"C:\Users\thomf\programming\Bagrecovery\outputs\rossa_forensic_cache"),
    ]
    
    tiles = []
    for search_path in search_paths:
        if search_path.exists():
            tiles.extend(list(search_path.glob("*.tif"))[:5])
    
    if not tiles:
        print("\n⚠ No tiles found!")
        print("  Place some .tif files in the data folder")
        return
    
    tiles = tiles[:3]  # Test with 3 tiles
    
    print(f"\nFound {len(tiles)} tiles to process")
    
    # Process each tile
    results = []
    for tile_path in tiles:
        result = process_tile(tile_path)
        if result:
            results.append(result)
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"  Tiles Processed: {len(results)}")
    total_anomalies = sum(r['gpu']['anomaly_count'] for r in results)
    print(f"  Total Anomalies: {total_anomalies}")
    tpu_used = sum(1 for r in results if r['tpu']['used_tpu'])
    print(f"  TPU Used: {tpu_used}/{len(results)} tiles")
    gpu_used = sum(1 for r in results if r['gpu']['gpu_used'])
    print(f"  GPU Used: {gpu_used}/{len(results)} tiles")
    print("="*70)

def main():
    print("\n" + "="*70)
    print("CESAROPS ENGINE")
    print("="*70)
    print(f"  Drive: {DRIVE_PATH.absolute()}")
    print(f"  Database: {DB_PATH.name}")
    print(f"  TPU Server: {TPU_SERVER_URL}")
    print(f"  GPU Enabled: {USE_GPU}")
    print("="*70)
    
    # Initialize database
    init_db()
    
    # Check TPU server
    print("\n📡 Checking TPU server...")
    tpu = TPUClient(TPU_SERVER_URL)
    health = tpu.health_check()
    print(f"  Status: {health.get('status', 'unknown')}")
    if health.get('status') == 'healthy':
        print(f"  ✓ TPU server online")
        print(f"  TPU Available: {health.get('tpu_available', False)}")
        print(f"  Model Loaded: {health.get('model_loaded', False)}")
    else:
        print(f"  ⚠ TPU server offline (will use stub inference)")
    
    print()
    
    # Run test pipeline
    run_test_pipeline()
    
    print("\n✅ Engine test complete!")
    print("\nNext steps:")
    print("  1. Check database: python database_connector.py")
    print("  2. Start TPU server: python tpu_server.py")
    print("  3. Push to GitHub, pull on Xenon")
    print("="*70)

if __name__ == "__main__":
    main()
