#!/usr/bin/env python3
"""
Deploy code to remote nodes and run a comprehensive multi-sensor scan.
Uses paramiko for SSH with password authentication.
"""

import paramiko
import time
import os
import json
from pathlib import Path

# Node configurations
NODES = {
    'pi': {
        'host': '10.0.0.226',
        'user': 'pi',
        'password': 'admin',
        'work_dir': '/home/pi/cesarops',
    },
    'xenon': {
        'host': '10.0.0.40',
        'user': 'cesarops',
        'password': 'cesarops',
        'work_dir': '/home/cesarops/cesarops/cesarops-core',
    }
}

# Area definition for the scan
SCAN_AREA = {
    'name': 'Northern Great Lakes Comprehensive Scan',
    'description': 'Northern Lake Huron + Michigan + Straits + 45th parallel to UP + North Channel + Georgian Bay + Green Bay',
    'bbox': {
        'lat_min': 44.5,
        'lon_min': -92.0,  # Western Green Bay
        'lat_max': 47.0,   # UP boundary
        'lon_max': -80.0,  # Eastern Georgian Bay/North Channel
    },
    'dates': {
        'start': '2024-06-01',  # Post ice-melt
        'end': '2025-09-30',    # Pre freeze-up
    },
    'sensors': ['thermal', 'optical', 'sar', 'swot'],
}

def ssh_connect(node_config):
    """Connect to a node with password auth, trying multiple methods."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Try key-based auth first
        client.connect(
            node_config['host'],
            username=node_config['user'],
            password=node_config['password'],
            timeout=10,
            allow_agent=True,
            look_for_keys=True,
        )
        return client
    except Exception as e:
        print(f"  Key auth failed ({e}), trying password-only...")
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                node_config['host'],
                username=node_config['user'],
                password=node_config['password'],
                timeout=10,
                allow_agent=False,
                look_for_keys=False,
                disabled_algorithms={'pubkey_types': ['rsa-sha2-256', 'rsa-sha2-512']},
            )
            return client
        except Exception as e2:
            print(f"  Password auth also failed: {e2}")
            return None

def run_command(client, cmd, timeout=300):
    """Run a command on remote node and return output."""
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        return {
            'exit_code': exit_code,
            'stdout': out,
            'stderr': err,
        }
    except Exception as e:
        return {
            'exit_code': -1,
            'stdout': '',
            'stderr': str(e),
        }

def deploy_code():
    """Deploy cesarops-core code to remote nodes."""
    print("\n" + "="*60)
    print("DEPLOYING CODE TO REMOTE NODES")
    print("="*60)
    
    # Get list of Python files to deploy
    core_dir = Path(__file__).parent
    py_files = [
        'ai_director.py',
        'lake_michigan_scan.py',
        'cesarops_orchestrator.py',
        'hard_pixel_audit.py',
        'swot_ssh_extractor.py',
        'remote_dispatch.py',
        'background_probe.py',
        'cuda_env.py',
        'cesarops_engine.py',
        'database_connector.py',
        'init_database.py',
        'universal_downloader.py',
    ]
    
    config_files = [
        '.env',
        'known_wrecks.json',
        'satellite_data_sources.json',
    ]
    
    for node_name, node_cfg in NODES.items():
        print(f"\n--- Deploying to {node_name} ({node_cfg['host']}) ---")
        client = ssh_connect(node_cfg)
        if not client:
            print(f"  ✗ Could not connect to {node_name}")
            continue
        
        print(f"  ✓ Connected to {node_name}")
        
        # Create directories
        result = run_command(client, f"mkdir -p {node_cfg['work_dir']}/outputs/probes")
        print(f"  Created work directory: {'OK' if result['exit_code'] == 0 else 'FAILED'}")
        
        # Deploy each file using scp-like method (write via SSH)
        for fname in py_files + config_files:
            src = core_dir / fname
            if not src.exists():
                print(f"  Skipping {fname} (not found)")
                continue
            
            content = src.read_text(encoding='utf-8')
            dest = f"{node_cfg['work_dir']}/{fname}"
            
            # Write file via SSH
            sftp = client.open_sftp()
            try:
                sftp.put(str(src), dest)
                print(f"  ✓ Deployed {fname}")
            except Exception as e:
                print(f"  ✗ Failed to deploy {fname}: {e}")
            finally:
                sftp.close()
        
        # Set up Python venv on Xenon if needed
        if node_name == 'xenon':
            print(f"\n  Setting up venv on Xenon...")
            result = run_command(client, f"""
                cd {node_cfg['work_dir']}
                source ~/cesarops/venv/bin/activate 2>/dev/null || true
                python3 -c "import cupy; print('CuPy version:', cupy.__version__)" 2>&1
            """)
            print(f"  CuPy check: {result['stdout'].strip() if result['stdout'] else result['stderr'].strip()}")
            
            # Install any missing packages
            result = run_command(client, f"""
                source ~/cesarops/venv/bin/activate
                pip install --quiet cupy-cuda12x numpy rasterio simplekml pyproj scipy paramiko requests 2>&1 | tail -3
            """, timeout=600)
            print(f"  Package install: {result['stdout'].strip() if result['stdout'] else 'Already installed'}")
        
        client.close()
        print(f"  ✓ Deployment complete for {node_name}")

def run_comprehensive_scan():
    """Run the full multi-sensor scan on the defined area."""
    print("\n" + "="*60)
    print("RUNNING COMPREHENSIVE MULTI-SENSOR SCAN")
    print("="*60)
    print(f"Area: {SCAN_AREA['name']}")
    print(f"BBOX: {SCAN_AREA['bbox']}")
    print(f"Dates: {SCAN_AREA['dates']}")
    print(f"Sensors: {', '.join(SCAN_AREA['sensors'])}")
    
    # Connect to Xenon for processing
    xenon_cfg = NODES['xenon']
    client = ssh_connect(xenon_cfg)
    if not client:
        print("✗ Could not connect to Xenon for scan execution")
        return
    
    print(f"\n✓ Connected to Xenon ({xenon_cfg['host']})")
    
    # Set up environment variables for the scan
    bbox = SCAN_AREA['bbox']
    dates = SCAN_AREA['dates']
    
    # Run the comprehensive scan
    scan_cmd = f"""
    source ~/cesarops/venv/bin/activate
    cd {xenon_cfg['work_dir']}
    
    # Set data directory
    export CESAROPS_DATA_DIR=/home/cesarops/cesarops/Sync
    
    echo "Starting comprehensive scan..."
    echo "Area: {SCAN_AREA['name']}"
    echo "BBOX: {bbox['lat_min']},{bbox['lon_min']},{bbox['lat_max']},{bbox['lon_max']}"
    echo "Date range: {dates['start']} to {dates['end']}"
    echo "Sensors: {', '.join(SCAN_AREA['sensors'])}"
    echo ""
    
    # Run AI Director with all sensors
    python ai_director.py \\
        --bbox {bbox['lat_min']},{bbox['lon_min']},{bbox['lat_max']},{bbox['lon_max']} \\
        --tools thermal,optical,sar,swot \\
        --sensitivity 1.0 \\
        --execute \\
        --no-llm \\
        --output outputs/probes/comprehensive_scan_{dates['start'].replace('-', '')}.json 2>&1
    """
    
    print(f"\nExecuting scan command...")
    result = run_command(client, scan_cmd, timeout=7200)  # 2 hour timeout
    
    print(f"\n{'='*60}")
    print("SCAN RESULTS")
    print(f"{'='*60}")
    
    if result['stdout']:
        print(result['stdout'])
    
    if result['stderr']:
        print(f"\nSTDERR:\n{result['stderr']}")
    
    print(f"\nExit code: {result['exit_code']}")
    
    # Save results locally
    timestamp = dates['start'].replace('-', '')
    output_file = Path(__file__).parent / 'outputs' / 'probes' / f'comprehensive_scan_{timestamp}.json'
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    results_data = {
        'timestamp': time.time(),
        'area': SCAN_AREA['name'],
        'bbox': SCAN_AREA['bbox'],
        'dates': SCAN_AREA['dates'],
        'sensors': SCAN_AREA['sensors'],
        'remote_execution': {
            'host': xenon_cfg['host'],
            'exit_code': result['exit_code'],
            'stdout': result['stdout'],
            'stderr': result['stderr'],
        }
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Results saved to: {output_file}")
    
    client.close()

def main():
    print("CESAROPS Multi-Node Deployment & Scan System")
    print("="*60)
    
    # Step 1: Deploy code
    deploy_code()
    
    # Step 2: Run scan
    run_comprehensive_scan()
    
    print("\n" + "="*60)
    print("ALL TASKS COMPLETE")
    print("="*60)

if __name__ == '__main__':
    main()
