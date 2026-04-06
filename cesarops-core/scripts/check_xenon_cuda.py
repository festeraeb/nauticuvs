#!/usr/bin/env python3
"""
Check Xenon's CUDA/CuPy setup via SSH
"""

import subprocess
import sys

XENON_HOST = "10.0.0.55"
XENON_USER = "cesarops"

def check_xenon():
    print("="*70)
    print("CHECKING XENON CUDA SETUP")
    print("="*70)
    print()
    
    # SSH commands to run
    commands = [
        "python3 -c \"import cupy as cp; print('CuPy Version:', cp.__version__)\"",
        "python3 -c \"import cupy as cp; print('CUDA Devices:', cp.cuda.runtime.getDeviceCount())\"",
        "python3 -c \"import cupy as cp; print('Compute Capability:', cp.cuda.Device(0).compute_capability if cp.cuda.runtime.getDeviceCount() > 0 else 'None')\"",
        "nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv",
    ]
    
    for cmd in commands:
        print(f"Running: {cmd}")
        
        try:
            result = subprocess.run(
                f"ssh {XENON_USER}@{XENON_HOST} \"{cmd}\"",
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print(f"  ✓ {result.stdout.strip()}")
            else:
                print(f"  ✗ Error: {result.stderr.strip()}")
        
        except subprocess.TimeoutExpired:
            print(f"  ✗ Timeout (30s)")
        except Exception as e:
            print(f"  ✗ {e}")
        
        print()

if __name__ == "__main__":
    check_xenon()
