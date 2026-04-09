#!/bin/bash
# ═══════════════════════════════════════════════════════════
# CESAROPS Xenon Bootstrap & Scan Script
# Run this ONCE to fix all issues and run the scan
# ═══════════════════════════════════════════════════════════

set -e  # Exit on error

echo "═══════════════════════════════════════════════════════"
echo "CESAROPS XENON BOOTSTRAP & SCAN"
echo "═══════════════════════════════════════════════════════"
echo ""

WORK_DIR="$HOME/cesarops/cesarops-core"
VENV_DIR="$HOME/cesarops/venv"

# ── Step 1: Fix .env with all credentials ───────────────
echo "[1/6] Fixing .env configuration..."
cat > "$WORK_DIR/.env" << 'ENVEOF'
# CESAROPS Environment Configuration
QWEN_API_KEY=sk-13e7c2a0b0aa4ddba289b5bb53defa91
QWEN_MODEL=qwen-plus
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

EARTHDATA_USERNAME=cesarops.com.@
EARTHDATA_PASSWORD=Juliek01241973
EARTHDATA_TOKEN=

COPERNICUS_USERNAME=
COPERNICUS_PASSWORD=

PI_HOST=10.0.0.226
PI_USER=pi
PI_PASS=admin

XENON_HOST=10.0.0.40
XENON_USER=cesarops
XENON_PASS=cesarops

TPU_SERVER_URL=http://localhost:5001
CLOUDFLARE_API=https://your-worker.your-subdomain.workers.dev

CESAROPS_DATA_DIR=/home/cesarops/cesarops/Sync
ENVEOF
echo "  ✓ .env written with all credentials"

# ── Step 2: Fix cuda_env.py for Linux ────────────────────
echo "[2/6] Fixing cuda_env.py for Linux..."
python3 << 'PYEOF'
import pathlib
path = pathlib.Path("cuda_env.py")
code = path.read_text()
# Check if already fixed
if "'/usr/lib/nvidia-cuda-toolkit'" not in code:
    old = "'/usr/local/cuda-11.8',"
    new = "'/usr/local/cuda-11.8',\n            '/usr/lib/nvidia-cuda-toolkit',"
    code = code.replace(old, new)
    path.write_text(code)
    print("  ✓ cuda_env.py patched for Ubuntu system CUDA")
else:
    print("  ✓ cuda_env.py already correct")
PYEOF

# ── Step 3: Verify GPU ──────────────────────────────────
echo "[3/6] Verifying GPU..."
source "$VENV_DIR/bin/activate"
python3 << 'PYEOF'
import cupy
print(f"  GPU: {cupy.cuda.Device().name.decode() if isinstance(cupy.cuda.Device().name, bytes) else cupy.cuda.Device().name}")
props = cupy.cuda.runtime.getDeviceProperties(0)
print(f"  VRAM: {props['totalGlobalMem'] // 1024**2} MB")
print(f"  Compute Capability: {props['major']}.{props['minor']}")
PYEOF

# ── Step 4: Check for data files ────────────────────────
echo "[4/6] Checking for satellite data..."
DATA_DIR="/home/cesarops/cesarops/Sync"
TIF_COUNT=$(find "$DATA_DIR" -name "*.tif" -o -name "*.tiff" 2>/dev/null | wc -l)
if [ "$TIF_COUNT" -eq 0 ]; then
    echo "  ⚠ No TIFF files found in $DATA_DIR"
    echo "  Attempting to download data via universal_downloader.py..."
    echo ""
    echo "  NOTE: This requires Copernicus or Earthdata credentials."
    echo "  If download fails, manually copy TIFF files to $DATA_DIR via Syncthing."
    echo ""
    
    # Try downloading 15 Sentinel-2 optical tiles for the scan area
    source "$VENV_DIR/bin/activate"
    python3 universal_downloader.py \
        --bbox "44.5,-92.0,47.0,-80.0" \
        --dates "2024-06-01" "2025-09-30" \
        --sensors "optical,sar" \
        --max-results 15 \
        --output "$DATA_DIR/downloads" 2>&1 || echo "  Download skipped (no credentials or network issue)"
    
    # Re-check
    TIF_COUNT=$(find "$DATA_DIR" -name "*.tif" -o -name "*.tiff" 2>/dev/null | wc -l)
    if [ "$TIF_COUNT" -eq 0 ]; then
        echo "  ⚠ Still no data files. Creating test data for scan verification..."
        mkdir -p "$DATA_DIR/test_tiles"
        python3 << 'TESTDATA'
import numpy as np, rasterio
from rasterio.transform import from_bounds
from pathlib import Path

# Create a fake Sentinel-2 tile for testing
data = np.random.randn(1000, 1000).astype(np.float32) * 100 + 500
transform = from_bounds(-86.0, 44.5, -84.0, 46.0, 1000, 1000)
profile = {
    'driver': 'GTiff', 'dtype': 'float32', 'width': 1000, 'height': 1000,
    'count': 1, 'crs': 'EPSG:4326', 'transform': transform,
}
out = Path('/home/cesarops/cesarops/Sync/test_tiles/test_tile_B04.tif')
out.parent.mkdir(parents=True, exist_ok=True)
with rasterio.open(out, 'w', **profile) as dst:
    dst.write(data, 1)
print(f"  ✓ Created test tile: {out}")
TESTDATA
    fi
fi
echo "  Found $TIF_COUNT TIFF files"

# ── Step 5: Run comprehensive scan ──────────────────────
echo ""
echo "[5/6] Running comprehensive multi-sensor scan..."
source "$VENV_DIR/bin/activate"
cd "$WORK_DIR"

export CESAROPS_DATA_DIR="$DATA_DIR"

python3 ai_director.py \
    --bbox "44.5,-92.0,47.0,-80.0" \
    --tools "thermal,optical,sar,swot" \
    --sensitivity 1.0 \
    --execute \
    --no-llm \
    --output "outputs/probes/comprehensive_scan_$(date +%Y%m%d_%H%M%S).json" 2>&1

# ── Step 6: Show results ────────────────────────────────
echo ""
echo "[6/6] Results saved to: outputs/probes/"
ls -la outputs/probes/*.json 2>/dev/null | tail -5

echo ""
echo "═══════════════════════════════════════════════════════"
echo "BOOTSTRAP COMPLETE"
echo "═══════════════════════════════════════════════════════"
