#!/usr/bin/env python3
"""
CESAROPS Portable Drive Identity System

Plug in drive → Run this script → Drive authenticates via webpage → Start working

The drive contains:
  - drive_id.json (unique identity)
  - LAKE_MICHIGAN_CENSUS_2026.db (main database)
  - kmz/ (export cache)
  - config/ (settings)
"""

import json
import sqlite3
import sys
import hashlib
import platform
from pathlib import Path
from datetime import datetime
import requests
import uuid

# ============================================================================
# CONFIGURATION
# ============================================================================

# Webpage API (Cloudflare Worker)
WEBPAGE_API = "https://your-worker.your-subdomain.workers.dev"

# Drive structure
DRIVE_ID_FILE = "drive_id.json"
DATABASE_FILE = "LAKE_MICHIGAN_CENSUS_2026.db"
KMZ_FOLDER = "kmz"
CONFIG_FOLDER = "config"

# ============================================================================
# DRIVE IDENTITY
# ============================================================================

def get_or_create_drive_id(drive_path: Path):
    """Get existing drive ID or create new one"""
    
    id_file = drive_path / DRIVE_ID_FILE
    
    if id_file.exists():
        # Load existing identity
        with open(id_file, 'r') as f:
            drive_data = json.load(f)
        
        print(f"✓ Found existing drive identity:")
        print(f"  Drive ID: {drive_data['drive_id']}")
        print(f"  Owner: {drive_data['owner']}")
        print(f"  Created: {drive_data['created_at']}")
        
        return drive_data
    
    else:
        # Create new identity
        print("🆕 Creating new drive identity...")
        
        drive_id = f"DRIVE-{uuid.uuid4().hex[:12].upper()}"
        owner = input("Enter owner name (e.g., your name): ").strip()
        if not owner:
            owner = "CESAROPS-ADMIN"
        
        drive_data = {
            "drive_id": drive_id,
            "owner": owner,
            "created_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "hostname": platform.node(),
            "tier": "ADMIN",  # Default tier
            "permissions": {
                "draw_bbox": True,
                "preprocess_level": "full_cuda",
                "upload_anomalies": True,
                "generate_kmz": True,
                "push_to_github": True,
                "approve_others": True
            }
        }
        
        # Save to drive
        with open(id_file, 'w') as f:
            json.dump(drive_data, f, indent=2)
        
        print(f"✓ Created drive identity: {drive_id}")
        
        return drive_data

def register_drive_with_webpage(drive_data, drive_path: Path):
    """Register drive identity with Cloudflare Worker"""
    
    print("\n📡 Registering with webpage...")
    
    # Create registration payload
    payload = {
        "drive_id": drive_data['drive_id'],
        "owner": drive_data['owner'],
        "hostname": platform.node(),
        "platform": platform.system(),
        "registered_at": datetime.now().isoformat(),
        "drive_path": str(drive_path.absolute()),
        "database_exists": (drive_path / DATABASE_FILE).exists()
    }
    
    try:
        # Send to webpage API
        response = requests.post(
            f"{WEBPAGE_API}/api/drive/register",
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"✓ Drive registered successfully!")
            print(f"  App ID: {result.get('app_id')}")
            print(f"  Tier: {result.get('tier')}")
            
            # Save App ID to drive
            drive_data['app_id'] = result.get('app_id')
            drive_data['webpage_registered'] = True
            drive_data['last_registered'] = datetime.now().isoformat()
            
            with open(drive_path / DRIVE_ID_FILE, 'w') as f:
                json.dump(drive_data, f, indent=2)
            
            return True
        else:
            print(f"✗ Registration failed: {response.status_code}")
            print(f"  {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"⚠ Could not reach webpage (offline?)")
        print(f"  Will retry next time you're online")
        
        # Still save local identity
        drive_data['webpage_registered'] = False
        with open(drive_path / DRIVE_ID_FILE, 'w') as f:
            json.dump(drive_data, f, indent=2)
        
        return True  # Continue anyway

def verify_database(drive_path: Path):
    """Verify database exists and is valid"""
    
    db_path = drive_path / DATABASE_FILE
    
    if not db_path.exists():
        print(f"⚠ Database not found: {db_path}")
        print("  Creating new database...")
        
        # Create database with schema
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Read schema file if available
        schema_path = Path(__file__).parent / "cesarops_comprehensive_schema.sql"
        if schema_path.exists():
            with open(schema_path, 'r') as f:
                cursor.executescript(f.read())
        else:
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
        
        print(f"✓ Database created: {db_path}")
        return True
    
    else:
        # Verify database is valid
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM stationary_anchors")
            count = cursor.fetchone()[0]
            conn.close()
            
            print(f"✓ Database verified: {count} stationary anchors")
            return True
            
        except Exception as e:
            print(f"✗ Database error: {e}")
            return False

def setup_drive_folders(drive_path: Path):
    """Create required folders on drive"""
    
    folders = [
        drive_path / KMZ_FOLDER,
        drive_path / CONFIG_FOLDER,
        drive_path / "reports",
        drive_path / "cache",
    ]
    
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    
    print(f"✓ Drive folders created")

def show_drive_status(drive_data, drive_path: Path):
    """Show drive status summary"""
    
    print("\n" + "="*80)
    print("DRIVE STATUS")
    print("="*80)
    print(f"  Drive ID:     {drive_data['drive_id']}")
    print(f"  Owner:        {drive_data['owner']}")
    print(f"  App ID:       {drive_data.get('app_id', 'Not registered')}")
    print(f"  Tier:         {drive_data['tier']}")
    print(f"  Location:     {drive_path.absolute()}")
    print(f"  Database:     {'✓' if (drive_path / DATABASE_FILE).exists() else '✗'}")
    print(f"  Webpage:      {'✓ Connected' if drive_data.get('webpage_registered') else '✗ Offline'}")
    print("="*80)
    
    print("\n📁 Drive Structure:")
    print(f"  {DATABASE_FILE}")
    print(f"  {DRIVE_ID_FILE}")
    print(f"  {KMZ_FOLDER}/")
    print(f"  {CONFIG_FOLDER}/")
    print(f"  reports/")
    print(f"  cache/")
    
    print("\n🚀 Ready to use!")
    print("\nNext steps:")
    print("  1. Open Tauri Admin App")
    print("  2. Database path: " + str(drive_path / DATABASE_FILE))
    print("  3. App ID: " + str(drive_data.get('app_id', 'Register first')))
    print("="*80)

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*80)
    print("CESAROPS PORTABLE DRIVE IDENTITY")
    print("="*80)
    print()
    
    # Find drive path (current directory or specified)
    if len(sys.argv) > 1:
        drive_path = Path(sys.argv[1])
    else:
        drive_path = Path(__file__).parent
    
    if not drive_path.exists():
        print(f"✗ Drive not found: {drive_path}")
        sys.exit(1)
    
    print(f"📍 Drive location: {drive_path.absolute()}")
    print()
    
    # Step 1: Get or create drive identity
    drive_data = get_or_create_drive_id(drive_path)
    print()
    
    # Step 2: Setup folders
    setup_drive_folders(drive_path)
    print()
    
    # Step 3: Verify database
    db_ok = verify_database(drive_path)
    print()
    
    # Step 4: Register with webpage (if online)
    register_drive_with_webpage(drive_data, drive_path)
    print()
    
    # Step 5: Show status
    show_drive_status(drive_data, drive_path)
    
    # Save session info for apps to use
    session_file = drive_path / CONFIG_FOLDER / "current_session.json"
    with open(session_file, 'w') as f:
        json.dump({
            "drive_id": drive_data['drive_id'],
            "app_id": drive_data.get('app_id'),
            "database_path": str(drive_path / DATABASE_FILE),
            "kmz_path": str(drive_path / KMZ_FOLDER),
            "session_started": datetime.now().isoformat()
        }, f, indent=2)
    
    print(f"\n💾 Session file saved: {session_file}")
    print("   Tauri app can read this for configuration")

if __name__ == "__main__":
    main()
