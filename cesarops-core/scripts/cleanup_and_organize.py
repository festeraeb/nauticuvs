#!/usr/bin/env python3
"""
CLEANUP SCRIPT

1. Create cesarops-clean/ folder with only what we need
2. Archive everything else to zip
3. Move .bag files to separate folder
"""

import os
import shutil
import zipfile
from pathlib import Path
from datetime import datetime

ROOT = Path(".")
CLEAN_DIR = ROOT / "cesarops-clean"
ARCHIVE_DIR = ROOT / "cesarops-archive"

# What to KEEP (clean branch)
KEEP_CORE = [
    "database_connector.py",
    "cesarops_engine.py",
    "cuda_test_kmz.py",
    "tpu_server.py",
    "live_feed_server.py",
    "three_tile_offset_analysis.py",
    "validate_detection.py",
    "deep_wreck_validation.py",
]

KEEP_SCRIPTS = [
    "scripts/",
]

KEEP_DOCS = [
    "FRESH_START_PLAN.md",
    "TODO_RECOVERY.md",
    "DATABASE_STATUS.md",
    "FILE_INVENTORY.md",
    "README.md",
]

KEEP_DATA = [
    "wreckhunter2000/LAKE_MICHIGAN_CENSUS_2026.db",
    "wreckhunter2000/data/cache/census_raw/2021_low_water/*.tif",
    "wreckhunter2000/data/cache/census_raw/2025_rossa/*.tif",
]

def cleanup():
    print("="*70)
    print("CLEANUP - ORGANIZING CODEBASE")
    print("="*70)
    print()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create clean directory
    print("[1/4] Creating cesarops-clean/...")
    if CLEAN_DIR.exists():
        shutil.rmtree(CLEAN_DIR)
    CLEAN_DIR.mkdir(parents=True)
    (CLEAN_DIR / "scripts").mkdir()
    (CLEAN_DIR / "wreckhunter2000").mkdir()
    (CLEAN_DIR / "wreckhunter2000" / "LAKE_MICHIGAN_CENSUS_2026.db").touch()  # Placeholder
    print(f"  Created: {CLEAN_DIR}")
    print()
    
    # Copy core scripts
    print("[2/4] Copying core scripts...")
    for script in KEEP_CORE:
        src = ROOT / script
        dst = CLEAN_DIR / script
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  Copied: {script}")
    print()
    
    # Copy scripts folder
    print("[3/4] Copying scripts folder...")
    scripts_src = ROOT / "scripts"
    scripts_dst = CLEAN_DIR / "scripts"
    if scripts_src.exists():
        for py_file in scripts_src.glob("*.py"):
            shutil.copy2(py_file, scripts_dst / py_file.name)
            print(f"  Copied: scripts/{py_file.name}")
    print()
    
    # Copy documentation
    print("[4/4] Copying documentation...")
    for doc in KEEP_DOCS:
        src = ROOT / doc
        dst = CLEAN_DIR / doc
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  Copied: {doc}")
    print()
    
    # Create archive of everything else
    print("[5/6] Creating archive of remaining files...")
    archive_zip = ROOT / f"cesarops-archive-{timestamp}.zip"
    
    with zipfile.ZipFile(archive_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for item in ROOT.iterdir():
            # Skip what we're keeping clean
            if item.name in ['cesarops-clean', 'cesarops-archive', archive_zip.name]:
                continue
            
            # Skip .git
            if item.name == '.git':
                continue
            
            # Add to archive
            if item.is_file():
                zipf.write(item, item.name)
            elif item.is_dir():
                # Add directory contents
                for file in item.rglob("*"):
                    if file.is_file():
                        arcname = str(item / file.relative_to(item))
                        zipf.write(file, arcname)
    
    print(f"  Archive created: {archive_zip}")
    print()
    
    # Move BAG files
    print("[6/6] Moving BAG files to separate folder...")
    bag_folder = ROOT / "archive_bags"
    bag_folder.mkdir(exist_ok=True)
    
    bag_count = 0
    for bag in ROOT.rglob("*.bag"):
        if 'archive_bags' not in str(bag):
            dest = bag_folder / bag.name
            try:
                shutil.move(str(bag), str(dest))
                bag_count += 1
            except Exception as e:
                print(f"  Error moving {bag}: {e}")
    
    print(f"  Moved {bag_count} .bag files to {bag_folder}")
    print()
    
    print("="*70)
    print("CLEANUP COMPLETE")
    print("="*70)
    print()
    print("Next steps:")
    print(f"  1. Review cesarops-clean/ folder")
    print(f"  2. Create new git branch: git checkout -b cesarops-clean")
    print(f"  3. Add clean files: git add cesarops-clean/")
    print(f"  4. Commit: git commit -m 'Clean CESAROPS codebase'")
    print(f"  5. Archive zip: {archive_zip.name}")
    print()

if __name__ == "__main__":
    cleanup()
