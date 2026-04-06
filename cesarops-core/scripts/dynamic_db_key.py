#!/usr/bin/env python3
"""
DYNAMIC DATABASE KEY SYSTEM

Uses external hard drive as physical identity token.
Works anywhere: online, offline, intranet, internet.

Generates dynamic DB key based on:
1. External HD serial number (who you are)
2. Network location (where you are)
3. Connectivity status (what access you have)
"""

import os
import json
import hashlib
import socket
import subprocess
from pathlib import Path
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

# External HD mount points (common defaults, but auto-detection is used)
HD_MOUNT_POINTS = {
    'windows': ['D:/', 'E:/', 'F:/', 'G:/', 'H:/'],  # Typical USB drive letters
    'linux': ['/media/usb', '/mnt/usb', '/media/external', '/mnt/external'],
    'mac': ['/Volumes/EXTERNAL', '/Volumes/DRIVE']
}

# Identity file on external HD
IDENTITY_FILE = "cesarops_identity.json"

# Output for dynamic key
KEY_OUTPUT = "outputs/dynamic_db_key.json"

# ============================================================================
# DETECTION FUNCTIONS
# ============================================================================

def get_platform():
    """Detect current platform"""
    import sys
    if sys.platform == 'win32':
        return 'windows'
    elif sys.platform == 'linux':
        return 'linux'
    elif sys.platform == 'darwin':
        return 'mac'
    return 'unknown'

def find_external_hd():
    """Find external HD and get its serial number"""
    platform = get_platform()

    # First, check configured mount points
    for mount_point in HD_MOUNT_POINTS.get(platform, []):
        mount_path = Path(mount_point)
        if mount_path.exists():
            # Check for identity file
            identity_path = mount_path / IDENTITY_FILE
            if identity_path.exists():
                print(f"  ✓ Found external HD at: {mount_point}")

                # Get disk serial number
                serial = get_disk_serial(mount_point)

                return {
                    'found': True,
                    'mount_point': mount_point,
                    'serial': serial,
                    'identity_file': str(identity_path)
                }

    # Auto-detect: On Windows, scan all drive letters if not found
    if platform == 'windows':
        print("  🔍 Auto-detecting drives...")
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            mount_point = f"{letter}:/"
            mount_path = Path(mount_point)
            if mount_path.exists():
                identity_path = mount_path / IDENTITY_FILE
                if identity_path.exists():
                    print(f"  ✓ Found external HD at: {mount_point}")
                    serial = get_disk_serial(mount_point)
                    return {
                        'found': True,
                        'mount_point': mount_point,
                        'serial': serial,
                        'identity_file': str(identity_path)
                    }

    print("  ✗ External HD not found")
    print(f"     Make sure the drive contains: {IDENTITY_FILE}")
    return {'found': False}

def get_disk_serial(mount_point):
    """Get disk serial number"""
    platform = get_platform()
    
    try:
        if platform == 'windows':
            # Windows: Use vol command
            result = subprocess.run(
                f'vol {mount_point}',
                shell=True,
                capture_output=True,
                text=True
            )
            # Parse serial from output
            for line in result.stdout.split('\n'):
                if 'Volume Serial Number' in line:
                    serial = line.split(':')[-1].strip()
                    return serial
        
        elif platform == 'linux':
            # Linux: Use lsblk
            result = subprocess.run(
                f'lsblk -o SERIAL --noheadings {mount_point}',
                shell=True,
                capture_output=True,
                text=True
            )
            serial = result.stdout.strip()
            if serial:
                return serial
        
        elif platform == 'mac':
            # Mac: Use diskutil
            result = subprocess.run(
                f'diskutil info {mount_point} | grep "Volume Serial Number"',
                shell=True,
                capture_output=True,
                text=True
            )
            for line in result.stdout.split('\n'):
                if 'Volume Serial Number' in line:
                    serial = line.split(':')[-1].strip()
                    return serial
    
    except Exception as e:
        print(f"  Warning: Could not get disk serial: {e}")
    
    # Fallback: Use mount point hash
    return hashlib.md5(mount_point.encode()).hexdigest()[:12]

def detect_network_location():
    """Detect where we are on the network"""
    
    try:
        hostname = socket.gethostname()
        
        # Try to get IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        # Determine network type
        if local_ip.startswith('192.168.') or local_ip.startswith('10.'):
            network_type = 'intranet'
        elif local_ip.startswith('172.'):
            network_type = 'intranet'
        else:
            network_type = 'internet'
        
        return {
            'hostname': hostname,
            'ip': local_ip,
            'network_type': network_type
        }
    
    except Exception as e:
        return {
            'hostname': 'unknown',
            'ip': 'unknown',
            'network_type': 'offline'
        }

def check_connectivity():
    """Check what we have access to"""
    
    connectivity = {
        'internet': False,
        'intranet': False,
        'database': False,
        'xenon': False
    }
    
    # Check internet
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        connectivity['internet'] = True
    except:
        pass
    
    # Check database
    db_path = Path("wreckhunter2000/LAKE_MICHIGAN_CENSUS_2026.db")
    if db_path.exists():
        connectivity['database'] = True
    
    # Check Xenon (ping)
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', '10.0.0.55'],
            capture_output=True,
            timeout=3
        )
        if result.returncode == 0:
            connectivity['xenon'] = True
    except:
        pass
    
    return connectivity

# ============================================================================
# KEY GENERATION
# ============================================================================

def generate_dynamic_key(hd_info, network_info, connectivity):
    """Generate dynamic database key"""
    
    # Base key components
    key_data = {
        'hd_serial': hd_info.get('serial', 'unknown'),
        'network_type': network_info.get('network_type', 'unknown'),
        'ip': network_info.get('ip', 'unknown'),
        'timestamp': datetime.now().isoformat(),
        'connectivity': connectivity
    }
    
    # Generate key hash
    key_string = json.dumps(key_data, sort_keys=True)
    key_hash = hashlib.sha256(key_string.encode()).hexdigest()[:16]
    
    # Determine access level
    access_level = 'read-only'
    
    if hd_info['found'] and connectivity['database']:
        if connectivity['intranet'] or connectivity['xenon']:
            access_level = 'full'  # Full access on intranet with DB
        elif connectivity['internet']:
            access_level = 'write'  # Write access on internet
        else:
            access_level = 'cached'  # Cached access offline
    
    # Build final key
    dynamic_key = {
        'key': key_hash,
        'access_level': access_level,
        'user_type': 'agent' if hd_info['found'] else 'guest',
        'data': key_data,
        'signature': key_hash
    }
    
    return dynamic_key

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("="*70)
    print("DYNAMIC DATABASE KEY GENERATOR")
    print("="*70)
    print()
    
    # Find external HD
    print("[1/4] Finding external HD...")
    hd_info = find_external_hd()
    print()
    
    # Detect network
    print("[2/4] Detecting network location...")
    network_info = detect_network_location()
    print(f"  Hostname: {network_info['hostname']}")
    print(f"  IP: {network_info['ip']}")
    print(f"  Network: {network_info['network_type']}")
    print()
    
    # Check connectivity
    print("[3/4] Checking connectivity...")
    connectivity = check_connectivity()
    print(f"  Internet: {connectivity['internet']}")
    print(f"  Intranet: {connectivity['intranet']}")
    print(f"  Database: {connectivity['database']}")
    print(f"  Xenon: {connectivity['xenon']}")
    print()
    
    # Generate key
    print("[4/4] Generating dynamic key...")
    dynamic_key = generate_dynamic_key(hd_info, network_info, connectivity)
    
    print(f"  Key: {dynamic_key['key']}")
    print(f"  Access Level: {dynamic_key['access_level']}")
    print(f"  User Type: {dynamic_key['user_type']}")
    print()
    
    # Save key
    KEY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(KEY_OUTPUT, 'w') as f:
        json.dump(dynamic_key, f, indent=2)
    
    print(f"Key saved to: {KEY_OUTPUT}")
    print()
    
    print("="*70)
    print("DYNAMIC KEY GENERATED")
    print("="*70)
    print()
    print("Usage:")
    print("  - Pass this key to database operations")
    print("  - Key changes based on location/connectivity")
    print("  - External HD serial is your permanent identity")
    print()

if __name__ == "__main__":
    main()
