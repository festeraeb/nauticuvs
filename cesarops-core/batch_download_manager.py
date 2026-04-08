#!/usr/bin/env python3
"""
CESAROPS Batch Download Manager — Swarm Mode
Distributes downloads across multiple nodes. Each node grabs a unique "chunk"
to maximize bandwidth usage on your 1Gbps connection.
"""

import argparse
import subprocess
import sys
import time
import os
from pathlib import Path
from datetime import datetime

# Lake Bounding Boxes
LAKES = {
    'superior': {'bbox': [46.5, -92.0, 48.0, -84.5], 'label': 'Lake Superior'},
    'michigan': {'bbox': [41.5, -88.0, 46.0, -85.5], 'label': 'Lake Michigan'},
    'huron':    {'bbox': [42.5, -84.0, 46.0, -81.0], 'label': 'Lake Huron'},
    'erie':     {'bbox': [41.3, -83.5, 42.5, -78.8], 'label': 'Lake Erie'},
    'ontario':  {'bbox': [43.2, -79.5, 44.2, -76.0], 'label': 'Lake Ontario'},
}

def run_download(lake_key, year, sensors='hls,sar', chunk_id=None, dry_run=False):
    """Execute the universal downloader for a specific slice of data."""
    lake = LAKES.get(lake_key)
    if not lake: return

    b = lake['bbox']
    # Summer/Fall only (June-Oct) to avoid ice and heavy cloud cover
    dates = [f"{year}-06-01", f"{year}-10-31"]
    
    # Output directory structure: downloads/lake/year/
    out_dir = Path('downloads') / lake_key / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, 'universal_downloader.py',
        '--bbox', f"{b[0]},{b[1]},{b[2]},{b[3]}",
        '--dates', dates[0], dates[1],
        '--sensors', sensors,
        '--max-results', '15',
        '--output', str(out_dir)
    ]

    if dry_run:
        print(f"[DRY RUN] {lake['label']} ({year}) - {' '.join(cmd)}")
        return

    print(f"\n📥 Starting: {lake['label']} | {year} | Chunk: {chunk_id}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            print(f"  ✅ Success. Data saved to: {out_dir}")
        else:
            print(f"  ⚠️ API Error or No Data: {result.stderr[:150]}")
    except Exception as e:
        print(f"  ❌ Crashed: {e}")

def main():
    parser = argparse.ArgumentParser(description='Batch Download Manager')
    parser.add_argument('--lakes', default='all', help='Comma-separated lakes or "all"')
    parser.add_argument('--start', type=int, default=2013)
    parser.add_argument('--end', type=int, default=2025)
    parser.add_argument('--sensors', default='hls,sar')
    parser.add_argument('--chunk-id', type=str, help='Node ID for parallel processing')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    lakes = list(LAKES.keys()) if args.lakes == 'all' else args.lakes.split(',')
    chunks = []

    # Build the "Master List" of all tasks
    for lake in lakes:
        for year in range(args.start, args.end + 1):
            chunks.append({'lake': lake, 'year': year, 'id': f"{lake}-{year}"})

    # Filter for this specific node if --chunk-id is used (Simulated swarm logic)
    if args.chunk_id:
        # Simple hash distribution: Node 1 gets even years, Node 2 gets odd, etc.
        # In a real cluster, you'd use a shared manifest file.
        node_idx = int(args.chunk_id)
        chunks = [c for i, c in enumerate(chunks) if i % 2 == node_idx]

    print(f"🚀 BATCH START: {len(chunks)} tasks for Node {args.chunk_id or 'Single'}")
    for task in chunks:
        run_download(task['lake'], task['year'], args.sensors, args.chunk_id, args.dry_run)
        time.sleep(2) # Rate limit courtesy of the API

if __name__ == '__main__':
    main()