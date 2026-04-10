#!/usr/bin/env python3
"""
Erie Multi-Year Download
Strategy:
  - Skip January, February, March (ice + heavy cloud)
  - 4 best days per month for all lake-year-month combos
  - Exception: 2015 October → all available days (Secchi baseline capture)
  - Years: 2013-2025 (Landsat 8 launch = 2013, Sentinel-2 = 2015)
"""

import subprocess
import sys
import time
import calendar
from pathlib import Path

ERIE_BBOX = [41.3, -83.5, 42.5, -78.8]
SKIP_MONTHS = {1, 2, 3}
ACTIVE_MONTHS = [m for m in range(1, 13) if m not in SKIP_MONTHS]  # Apr-Dec
YEARS = range(2013, 2026)

def last_day(year, month):
    return calendar.monthrange(year, month)[1]

def run_month(year, month, max_results, dry_run=False):
    b = ERIE_BBOX
    date_start = f"{year}-{month:02d}-01"
    date_end   = f"{year}-{month:02d}-{last_day(year, month):02d}"
    out_dir    = Path('downloads') / 'erie' / str(year) / f"{month:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    tag = "ALL" if max_results >= 50 else f"top{max_results}"
    label = f"Erie | {year}-{month:02d} | {tag}"

    cmd = [
        sys.executable, 'universal_downloader.py',
        '--bbox', f"{b[0]},{b[1]},{b[2]},{b[3]}",
        '--dates', date_start, date_end,
        '--sensors', 'hls',
        '--max-results', str(max_results),
        '--output', str(out_dir),
    ]

    if dry_run:
        print(f"[DRY RUN] {label}  →  {' '.join(cmd)}")
        return

    print(f"\n📥 {label}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            print(f"  ✅ Saved → {out_dir}")
        else:
            print(f"  ⚠️  {result.stderr[:200].strip()}")
    except Exception as e:
        print(f"  ❌ {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    tasks = []
    for year in YEARS:
        for month in ACTIVE_MONTHS:
            # 2015-October: all available granules; everything else: 4 best days
            max_r = 50 if (year == 2015 and month == 10) else 4
            tasks.append((year, month, max_r))

    total = len(tasks)
    print(f"🚀 Erie multi-year download: {total} tasks")
    print(f"   Years {min(YEARS)}-{max(YEARS)}, skip Jan/Feb/Mar")
    print(f"   2015-October → ALL granules (max 50)")
    print(f"   All others   → 4 best days (lowest cloud cover)\n")

    for i, (year, month, max_r) in enumerate(tasks, 1):
        print(f"[{i}/{total}] ", end='', flush=True)
        run_month(year, month, max_r, dry_run=args.dry_run)
        if not args.dry_run:
            time.sleep(1)  # polite rate limiting

    print("\n\n✅ Erie multi-year download complete.")

if __name__ == '__main__':
    main()
