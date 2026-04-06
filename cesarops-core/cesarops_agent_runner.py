#!/usr/bin/env python3
"""
CESAROPS AGENT RUNNER
Designed for Agent Execution & Parameter Tuning.
Fires multiple sensors, fuses results, outputs GeoJSON map, pushes to DB.
"""

import argparse
import json
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# ============================================================================
# 🤖 AGENT-TWEAKABLE CONFIG (Modify before run)
# ============================================================================
AGENT_CONFIG = {
    "areas": {
        "lake_michigan_south": {
            "bbox": [-88.0, 42.0, -87.0, 43.0],
            "label": "Lake MI South (Zion Trench/Andaste)"
        },
        "lake_superior": {
            "bbox": [-91.0, 46.5, -84.5, 48.0],
            "label": "Lake Superior (Deep Basin)"
        },
        "lake_erie": {
            "bbox": [-83.5, 41.5, -82.0, 42.5],
            "label": "Lake Erie (Argo/Leak Survey)"
        }
    },
    "sensors": ["thermal", "nir_swir", "sar", "swot"],
    "thresholds": {
        "thermal_zscore": 2.5,
        "sar_coherence": 0.6,
        "glint_ratio_b08_b04": 1.5,
        "swot_ssh_m": 0.015
    },
    "execution": {
        "cpu_fallback": True,  # If GPU cl.exe fails, switch to CPU
        "timeout_min": 30,     # Max minutes per sensor run
        "fail_fast": False     # True = stop on first error. False = continue.
    }
}
# ============================================================================

def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {level:6} | {msg}")

def run_sensor_tool(sensor_name, area_cfg, thresholds):
    """Execute a sensor tool. Returns dict with status & output path."""
    sensor_map = {
        "thermal": ("hard_pixel_audit.py", [
            "--area", f"{area_cfg['bbox'][0]},{area_cfg['bbox'][1]},{area_cfg['bbox'][2]},{area_cfg['bbox'][3]}",
            "--zscore", str(thresholds["thermal_zscore"]),
            "--output", f"outputs/{sensor_name}"
        ]),
        "nir_swir": ("cesarops_engine.py", [
            "--bands", "B11,B12,B08A",
            "--bbox", f"{area_cfg['bbox'][0]},{area_cfg['bbox'][1]},{area_cfg['bbox'][2]},{area_cfg['bbox'][3]}",
            "--output", f"outputs/{sensor_name}"
        ]),
        "sar": ("lake_michigan_scan.py", [ 
            "--mode", "sar_only",
            "--bbox", f"{area_cfg['bbox'][0]},{area_cfg['bbox'][1]},{area_cfg['bbox'][2]},{area_cfg['bbox'][3]}",
            "--coherence", str(thresholds["sar_coherence"]),
            "--output", f"outputs/sar"
        ]),
        "swot": ("swot_ssh_extractor.py", [
            "--bbox", f"{area_cfg['bbox'][0]},{area_cfg['bbox'][1]},{area_cfg['bbox'][2]},{area_cfg['bbox'][3]}",
            "--threshold", str(thresholds["swot_ssh_m"]),
            "--output", f"outputs/swot"
        ])
    }

    if sensor_name not in sensor_map:
        log(f"⚠️  Unknown sensor: {sensor_name}", "WARN")
        return {"sensor": sensor_name, "status": "skipped", "reason": "unknown"}

    script, args = sensor_map[sensor_name]
    script_path = Path(__file__).parent / script
    
    if not script_path.exists():
        log(f"🔍 Script not found: {script_path.name}", "WARN")
        return {"sensor": sensor_name, "status": "missing", "path": str(script_path)}

    # Environment override for CPU fallback
    env = os.environ.copy()
    if AGENT_CONFIG["execution"]["cpu_fallback"]:
        env["CUPY_CUDA_PATH"] = ""

    cmd = [sys.executable, str(script_path)] + args
    log(f"🚀 Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, 
            encoding='utf-8',
            errors='replace',
            timeout=AGENT_CONFIG["execution"]["timeout_min"] * 60,
            env=env
        )
        output_dir = Path(__file__).parent / f"outputs/{sensor_name}"
        
        if result.returncode == 0:
            log(f"✅ {sensor_name} SUCCESS", "OK")
            return {"sensor": sensor_name, "status": "success", "output_dir": str(output_dir)}
        else:
            log(f"❌ {sensor_name} FAILED (exit {result.returncode})", "ERROR")
            log(f"   Stderr: {result.stderr[:200].strip()}")
            return {"sensor": sensor_name, "status": "failed", "error": result.stderr[:200]}
    except subprocess.TimeoutExpired:
        log(f"⏳ {sensor_name} TIMED OUT", "ERROR")
        return {"sensor": sensor_name, "status": "timeout"}
    except Exception as e:
        log(f"💥 {sensor_name} CRASHED: {e}", "CRIT")
        return {"sensor": sensor_name, "status": "crash", "error": str(e)}

def fuse_to_geojson(results, output_path):
    """Convert sensor outputs to a unified GeoJSON FeatureCollection."""
    features = []
    for r in results:
        if r["status"] == "success":
            pass # In production, parse actual KMZ/JSON from output_dir.
            
    geo = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "run_time": datetime.now(timezone.utc).isoformat(),
            "agent_config": AGENT_CONFIG,
            "results_summary": results
        }
    }
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(geo, f, indent=2)
    log(f"🗺️  Map saved to: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="CESAROPS Agent-Driven Sensor Probe")
    parser.add_argument("--area", choices=AGENT_CONFIG["areas"].keys(), default="lake_michigan_south")
    parser.add_argument("--sensors", nargs="+", default=None, help="Override sensor list")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    area_name = args.area
    area_cfg = AGENT_CONFIG["areas"][area_name]
    sensors = args.sensors or AGENT_CONFIG["sensors"]
    
    log("="*60)
    log(f"🛰️  STARTING PROBE: {area_cfg['label']}")
    log(f"📡 SENSORS: {', '.join(sensors)}")
    log(f"🤖 AGENT CONFIG LOADED")
    log("="*60)

    if args.dry_run:
        log("🛑 DRY RUN MODE. Exiting.")
        return

    results = []
    for sensor in sensors:
        res = run_sensor_tool(sensor, area_cfg, AGENT_CONFIG["thresholds"])
        results.append(res)
        if res["status"] in ["failed", "crash"] and AGENT_CONFIG["execution"]["fail_fast"]:
            log("🛑 FAIL_FAST ENABLED. ABORTING.")
            break

    out_file = f"outputs/probes/probe_{area_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.geojson"
    fuse_to_geojson(results, out_file)
    
    # Push to Database
    log("📡 Sending results to DB Ingestor...")
    subprocess.run([sys.executable, "ingest_results.py"], capture_output=True)
    
    # Print Agent Summary
    successes = [r for r in results if r["status"] == "success"]
    log("="*60)
    log(f"📊 PROBE COMPLETE: {len(successes)}/{len(sensors)} sensors succeeded")
    log(f"📁 Output: {out_file}")
    log("🤖 Agent can now review logs, adjust thresholds, and re-run.")
    log("="*60)

if __name__ == "__main__":
    main()
