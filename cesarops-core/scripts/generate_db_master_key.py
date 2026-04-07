#!/usr/bin/env python3
"""
DYNAMIC DATABASE MASTER KEY

The external HD IS the master key.
DB key is generated dynamically from:
1. External HD serial number (permanent identity)
2. Current location/context (changes per session)

This key:
- Encrypts/decrypts the database
- Works anywhere (online/offline/intranet/internet)
- Changes per session (dynamic)
- But always derives from same HD (consistent)
"""

import os
import json
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

# ============================================================================
# CONFIGURATION
# ============================================================================

# External HD mount points (common defaults, but auto-detection is used)
HD_MOUNT_POINTS = {
    'windows': ['D:/', 'E:/', 'F:/', 'G:/', 'H:/'],
    'linux': ['/media/usb', '/mnt/usb', '/media/external', '/mnt/external'],
    'mac': ['/Volumes/EXTERNAL', '/Volumes/DRIVE']
}

# Salt file (stored on HD, makes key unique per HD)
SALT_FILE = "cesarops_salt.bin"

# Output
KEY_OUTPUT = "outputs/db_master_key.json"
DB_PATH = Path("wreckhunter2000/LAKE_MICHIGAN_CENSUS_2026.db")

# ============================================================================
# FUNCTIONS
# ============================================================================

def get_platform():
    import sys
    if sys.platform == 'win32':
        return 'windows'
    elif sys.platform == 'linux':
        return 'linux'
    elif sys.platform == 'darwin':
        return 'mac'
    return 'unknown'

def find_external_hd():
    """Find external HD by checking configured mount points and auto-detecting drives"""
    platform = get_platform()

    # First, check configured mount points
    for mount_point in HD_MOUNT_POINTS.get(platform, []):
        mount_path = Path(mount_point)
        if mount_path.exists():
            salt_path = mount_path / SALT_FILE
            if salt_path.exists():
                print(f"  ✓ Found external HD at: {mount_point}")
                return {
                    'found': True,
                    'mount_point': mount_point,
                    'salt_file': str(salt_path),
                    'serial': get_disk_serial(mount_point)
                }

    # Auto-detect: On Windows, scan all drive letters if not found
    if platform == 'windows':
        print("  🔍 Auto-detecting drives...")
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            mount_point = f"{letter}:/"
            mount_path = Path(mount_point)
            if mount_path.exists():
                salt_path = mount_path / SALT_FILE
                if salt_path.exists():
                    print(f"  ✓ Found external HD at: {mount_point}")
                    return {
                        'found': True,
                        'mount_point': mount_point,
                        'salt_file': str(salt_path),
                        'serial': get_disk_serial(mount_point)
                    }

    print("  ✗ External HD not found")
    print(f"     Make sure the drive contains: {SALT_FILE}")
    return {'found': False}

def get_disk_serial(mount_point):
    """Get disk serial number"""
    platform = get_platform()

    try:
        if platform == 'windows':
            # SECURITY: Use list form to prevent shell injection
            result = subprocess.run(['vol', mount_point], capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if 'Volume Serial Number' in line:
                    return line.split(':')[-1].strip()

        elif platform == 'linux':
            # SECURITY: Use list form to prevent shell injection
            result = subprocess.run(
                ['lsblk', '-o', 'SERIAL', '--noheadings', mount_point],
                capture_output=True, text=True
            )
            serial = result.stdout.strip()
            if serial:
                return serial

        elif platform == 'mac':
            # SECURITY: Use list form to prevent shell injection
            result = subprocess.run(
                ['diskutil', 'info', mount_point],
                capture_output=True, text=True
            )
            for line in result.stdout.split('\n'):
                if 'Volume Serial Number' in line:
                    return line.split(':')[-1].strip()
    
    except Exception as e:
        print(f"  Warning: {e}")
    
    return hashlib.md5(mount_point.encode()).hexdigest()[:12]

def get_or_create_salt(mount_point):
    """Get salt from HD, or create if doesn't exist"""
    salt_path = Path(mount_point) / SALT_FILE
    
    if salt_path.exists():
        with open(salt_path, 'rb') as f:
            salt = f.read()
        print(f"  ✓ Loaded salt from HD")
        return salt
    else:
        # Create new salt
        salt = os.urandom(16)
        salt_path.parent.mkdir(parents=True, exist_ok=True)
        with open(salt_path, 'wb') as f:
            f.write(salt)
        print(f"  ✓ Created new salt on HD")
        return salt

def generate_master_key(hd_serial, salt):
    """Generate master key from HD serial + salt"""
    
    # Combine serial + salt + timestamp (makes it dynamic)
    timestamp = datetime.now().strftime('%Y%m%d')  # Date-only for consistency within day
    key_material = f"{hd_serial}:{salt.hex()}:{timestamp}"
    
    # Derive key using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(key_material.encode()))
    
    return key.decode()

def encrypt_db(db_path, key):
    """Encrypt database with key"""
    f = Fernet(key.encode())
    
    with open(db_path, 'rb') as f_db:
        db_data = f_db.read()
    
    encrypted = f.encrypt(db_data)
    
    encrypted_path = db_path.with_suffix('.db.enc')
    with open(encrypted_path, 'wb') as f:
        f.write(encrypted)
    
    print(f"  ✓ Database encrypted: {encrypted_path}")
    return encrypted_path

def decrypt_db(encrypted_path, key):
    """Decrypt database with key"""
    f = Fernet(key.encode())
    
    with open(encrypted_path, 'rb') as f:
        encrypted_data = f.read()
    
    decrypted = f.decrypt(encrypted_data)
    
    db_path = encrypted_path.with_suffix('').with_suffix('.db')
    with open(db_path, 'wb') as f:
        f.write(decrypted)
    
    print(f"  ✓ Database decrypted: {db_path}")
    return db_path

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*70)
    print("DYNAMIC DATABASE MASTER KEY")
    print("="*70)
    print()
    
    # Find external HD
    print("[1/4] Finding external HD...")
    hd_info = find_external_hd()
    
    if not hd_info['found']:
        print()
        print("ERROR: External HD required for master key generation")
        print()
        print("Plug in your external HD and try again.")
        return
    
    print(f"  Serial: {hd_info['serial']}")
    print()
    
    # Get/create salt
    print("[2/4] Getting salt from HD...")
    salt = get_or_create_salt(hd_info['mount_point'])
    print(f"  Salt: {salt.hex()[:16]}...")
    print()
    
    # Generate master key
    print("[3/4] Generating master key...")
    master_key = generate_master_key(hd_info['serial'], salt)
    print(f"  Key: {master_key[:20]}...")
    print()
    
    # Save key (for current session)
    print("[4/4] Saving session key...")
    KEY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    
    key_data = {
        'master_key': master_key,
        'hd_serial': hd_info['serial'],
        'generated_at': datetime.now().isoformat(),
        'valid_until': datetime.now().replace(hour=23, minute=59, second=59).isoformat(),
        'note': 'This key is valid for today only. Regenerate tomorrow.'
    }
    
    with open(KEY_OUTPUT, 'w') as f:
        json.dump(key_data, f, indent=2)
    
    print(f"  Saved: {KEY_OUTPUT}")
    print()
    
    print("="*70)
    print("MASTER KEY GENERATED")
    print("="*70)
    print()
    print("This key:")
    print("  - Is unique to YOUR external HD")
    print("  - Changes daily (dynamic)")
    print("  - Works anywhere (online/offline)")
    print("  - Encrypts/decrypts the database")
    print()
    print("Usage:")
    print("  - Keep this key secure")
    print("  - It expires at midnight")
    print("  - Run this script again tomorrow for new key")
    print()

if __name__ == "__main__":
    main()
