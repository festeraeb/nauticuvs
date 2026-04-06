#!/usr/bin/env python3
"""
CESAROPS Background Probe & Learning System

When no specific scan is running, continuously probes known wreck sites
with available sensors. Saves results for ML parameter tuning.

Usage:
    python background_probe.py --run         # Start probing loop
    python background_probe.py --once         # Run single probe pass
    python background_probe.py --list         # List known wreck sites
    python background_probe.py --add "name" --bbox ...  # Add new site
    python background_probe.py --report       # Show accumulated results
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ── Load Qwen API ────────────────────────────────────────────────────────────

def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip()
    return env

_dotenv = _load_env(Path(__file__).parent / ".env")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", _dotenv.get("QWEN_API_KEY", ""))
QWEN_MODEL = os.environ.get("QWEN_MODEL", _dotenv.get("QWEN_MODEL", "qwen-plus"))
QWEN_BASE_URL = os.environ.get("QWEN_BASE_URL", _dotenv.get("QWEN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1"))

# ── Known Wreck Sites Database ───────────────────────────────────────────────

KNOWN_WRECKS_DB = Path(__file__).parent / "known_wrecks.json"

DEFAULT_WRECKS = {
    "andaste": {
        "name": "Andaste / Monster Candidate",
        "lat_min": 42.80, "lat_max": 43.10,
        "lon_min": -86.60, "lon_max": -86.30,
        "depth_ft": 450,
        "type": "steel_freighter",
        "confidence": "high",
        "notes": "330ft hull, mussel filtration clear spot, thermal spine-lock candidate",
        "sensors_tested": [],
        "probe_results": [],
    },
    "gilcher": {
        "name": "Gilcher (Fox Islands)",
        "lat_min": 45.80, "lat_max": 46.00,
        "lon_min": -84.60, "lon_max": -84.40,
        "depth_ft": 220,
        "type": "wooden_freighter",
        "confidence": "medium",
        "notes": "Known wreck near Fox Islands, good for thermal baseline",
        "sensors_tested": [],
        "probe_results": [],
    },
    "parnell": {
        "name": "Parnell (Beaver Islands)",
        "lat_min": 45.60, "lat_max": 45.80,
        "lon_min": -85.60, "lon_max": -85.40,
        "depth_ft": 180,
        "type": "freighter",
        "confidence": "high",
        "notes": "Well-documented wreck, excellent for triple-lock calibration",
        "sensors_tested": [],
        "probe_results": [],
    },
    "bridge_builder_x": {
        "name": "Bridge Builder X Area",
        "lat_min": 45.70, "lat_max": 45.80,
        "lon_min": -84.70, "lon_max": -84.50,
        "depth_ft": 300,
        "type": "unknown",
        "confidence": "low",
        "notes": "Anomaly area near Straits of Mackinac",
        "sensors_tested": [],
        "probe_results": [],
    },
    "chicorah": {
        "name": "Chicorah",
        "lat_min": 42.30, "lat_max": 42.60,
        "lon_min": -87.40, "lon_max": -87.20,
        "depth_ft": 150,
        "type": "steamer",
        "confidence": "medium",
        "notes": "Lake Michigan South, good for optical glint baseline",
        "sensors_tested": [],
        "probe_results": [],
    },
}


def load_wrecks_db() -> dict:
    """Load known wrecks database, initializing with defaults if missing."""
    if KNOWN_WRECKS_DB.exists():
        return json.loads(KNOWN_WRECKS_DB.read_text())
    # Initialize with defaults
    KNOWN_WRECKS_DB.parent.mkdir(parents=True, exist_ok=True)
    KNOWN_WRECKS_DB.write_text(json.dumps(DEFAULT_WRECKS, indent=2))
    return DEFAULT_WRECKS


def save_wrecks_db(db: dict):
    KNOWN_WRECKS_DB.write_text(json.dumps(db, indent=2))


# ── Probe Execution ──────────────────────────────────────────────────────────

AVAILABLE_PROBES = [
    {"id": "thermal", "script": "hard_pixel_audit.py", "desc": "Thermal cold-sink Z-score"},
    {"id": "optical", "script": "lake_michigan_scan.py", "desc": "Optical glint detection"},
    {"id": "triple_lock", "script": "triple_lock_fusion.py", "desc": "Multi-sensor fusion"},
    {"id": "swot", "script": "swot_ssh_extractor.py", "desc": "SWOT SSH displacement"},
]


def run_probe(wreck_id: str, wreck: dict, probe: dict, zscore: float = 2.5) -> dict:
    """Run a single sensor probe on a known wreck site."""
    script_path = Path(__file__).parent / probe["script"]
    if not script_path.exists():
        return {
            "probe": probe["id"],
            "status": "missing",
            "error": f"script not found: {probe['script']}",
        }

    output_dir = Path(__file__).parent / "outputs" / "probes" / wreck_id / probe["id"]
    output_dir.mkdir(parents=True, exist_ok=True)

    bbox = f"{wreck['lat_min']},{wreck['lon_min']},{wreck['lat_max']},{wreck['lon_max']}"
    cmd = [sys.executable, str(script_path), "--area", bbox, "--zscore", str(zscore),
           "--output", str(output_dir)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        return {
            "probe": probe["id"],
            "status": "success" if result.returncode == 0 else "failed",
            "stdout_lines": result.stdout.strip().split('\n')[-10:] if result.stdout else [],
            "stderr": result.stderr[:500] if result.stderr else "",
            "duration_s": 0,
            "output_dir": str(output_dir),
        }
    except subprocess.TimeoutExpired:
        return {"probe": probe["id"], "status": "timeout"}
    except Exception as e:
        return {"probe": probe["id"], "status": "crash", "error": str(e)}


# ── Qwen-Assisted Parameter Tuning ───────────────────────────────────────────

def qwen_analyze_results(wreck_name: str, probe_results: list) -> dict:
    """Send probe results to Qwen for parameter tuning recommendations."""
    if not QWEN_API_KEY:
        return {"error": "QWEN_API_KEY not set"}

    summary = []
    for r in probe_results:
        summary.append(f"  {r['probe']}: {r['status']}")
        if r.get('stdout_lines'):
            for line in r['stdout_lines']:
                if any(kw in line.lower() for kw in ['detection', 'anomalies', 'lock', 'total', 'fused']):
                    summary.append(f"    → {line.strip()}")

    messages = [
        {"role": "system", "content": (
            f"You are a CESAROPS parameter tuning specialist. "
            f"Review probe results for known wreck site '{wreck_name}' and recommend "
            f"optimal parameter adjustments. This is a KNOWN wreck — we can validate "
            f"your recommendations against ground truth.\n\n"
            f"Only recommend parameter changes (zscore thresholds, sensitivity, bbox adjustments). "
            f"Do NOT suggest new code or new tools.\n"
            f"Return ONLY JSON: "
            f'{{"recommended_zscore": N, "recommended_sensitivity": N, '
            f'"notes": "...", "confidence_change": "up/down/same"}}'
        )},
        {"role": "user", "content": "\n".join(summary)},
    ]

    try:
        import requests
        url = f"{QWEN_BASE_URL}/chat/completions"
        resp = requests.post(url, headers={
            "Authorization": f"Bearer {QWEN_API_KEY}",
            "Content-Type": "application/json",
        }, json={"model": QWEN_MODEL, "messages": messages, "temperature": 0.3}, timeout=30)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            nl = raw.index("\n")
            raw = raw[nl + 1:]
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e)}


# ── Background Loop ──────────────────────────────────────────────────────────

def probe_all_wrecks(zscore: float = 2.5, probes: list = None) -> dict:
    """Probe all known wreck sites with available sensors."""
    db = load_wrecks_db()
    probes = probes or [p["id"] for p in AVAILABLE_PROBES]

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zscore": zscore,
        "wrecks": {},
    }

    for wreck_id, wreck in db.items():
        print(f"\n{'='*60}")
        print(f"PROBING: {wreck['name']}")
        print(f"  BBOX: {wreck['lat_min']:.2f} to {wreck['lat_max']:.2f} / "
              f"{wreck['lon_min']:.2f} to {wreck['lon_max']:.2f}")
        print(f"{'='*60}")

        wreck_results = []
        for probe_id in probes:
            probe_def = next((p for p in AVAILABLE_PROBES if p["id"] == probe_id), None)
            if not probe_def:
                continue
            print(f"\n  [{probe_def['id']}] {probe_def['desc']}...")
            result = run_probe(wreck_id, wreck, probe_def, zscore)
            wreck_results.append(result)
            status = "✓" if result["status"] == "success" else "✗"
            print(f"    {status} {result['status']}")

        report["wrecks"][wreck_id] = {
            "name": wreck["name"],
            "results": wreck_results,
        }

        # Save to DB
        if wreck_id in db:
            db[wreck_id]["sensors_tested"] = list(set(
                db[wreck_id].get("sensors_tested", []) +
                [r["probe"] for r in wreck_results if r["status"] == "success"]
            ))
            db[wreck_id]["probe_results"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "zscore": zscore,
                "results": wreck_results,
            })
            save_wrecks_db(db)

    return report


def background_loop(interval_minutes: int = 60, max_iterations: int = 0):
    """Run continuous background probing loop."""
    print(f"\n{'='*60}")
    print("CESAROPS BACKGROUND PROBE & LEARNING SYSTEM")
    print(f"  Interval: {interval_minutes}min")
    print(f"  Max iterations: {'unlimited' if max_iterations == 0 else max_iterations}")
    print(f"  Qwen tuning: {'enabled' if QWEN_API_KEY else 'disabled'}")
    print(f"{'='*60}")

    iteration = 0
    zscore = 2.5  # Starting zscore, Qwen will tune this

    while True:
        iteration += 1
        if max_iterations > 0 and iteration > max_iterations:
            break

        print(f"\n{'#'*60}")
        print(f"# PROBE ITERATION {iteration}")
        print(f"# Zscore: {zscore}")
        print(f"# Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*60}")

        report = probe_all_wrecks(zscore=zscore)

        # Qwen tuning
        if QWEN_API_KEY:
            print(f"\n  🤖 Qwen analyzing results for parameter tuning...")
            for wreck_id, wreck_report in report["wrecks"].items():
                results = wreck_report.get("results", [])
                if any(r["status"] == "success" for r in results):
                    tuning = qwen_analyze_results(wreck_report["name"], results)
                    if "error" not in tuning:
                        print(f"  💡 {wreck_report['name']}: "
                              f"zscore→{tuning.get('recommended_zscore', zscore)}, "
                              f"confidence→{tuning.get('confidence_change', '?')}")
                        if tuning.get("notes"):
                            print(f"     {tuning['notes']}")

        # Save full report
        report_path = Path(__file__).parent / "outputs" / "probes" / f"background_{iteration}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\n  📁 Report: {report_path}")

        if max_iterations == 0 or iteration < max_iterations:
            print(f"\n  ⏳ Next probe in {interval_minutes} minutes...")
            time.sleep(interval_minutes * 60)

    print(f"\n✅ Background probe complete: {iteration} iterations")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CESAROPS Background Probe & Learning")
    parser.add_argument("--run", action="store_true", help="Start background probe loop")
    parser.add_argument("--once", action="store_true", help="Run single probe pass")
    parser.add_argument("--list", action="store_true", help="List known wreck sites")
    parser.add_argument("--add", type=str, help="Add new wreck site (name)")
    parser.add_argument("--bbox", type=str, help="Bounding box: lat_min,lon_min,lat_max,lon_max")
    parser.add_argument("--zscore", type=float, default=2.5, help="Z-score threshold")
    parser.add_argument("--interval", type=int, default=60, help="Probe interval in minutes")
    parser.add_argument("--max", type=int, default=0, help="Max iterations (0=unlimited)")
    parser.add_argument("--report", action="store_true", help="Show accumulated results")
    parser.add_argument("--probes", type=str, help="Comma-separated probe list")
    args = parser.parse_args()

    if args.list:
        db = load_wrecks_db()
        print(f"\n{'='*80}")
        print("KNOWN WRECK SITES")
        print(f"{'='*80}")
        for wid, w in db.items():
            tested = ", ".join(w.get("sensors_tested", ["none"]))
            print(f"\n  {wid}: {w['name']}")
            print(f"    BBOX: {w['lat_min']:.2f} to {w['lat_max']:.2f} / "
                  f"{w['lon_min']:.2f} to {w['lon_max']:.2f}")
            print(f"    Depth: {w['depth_ft']}ft | Type: {w['type']}")
            print(f"    Confidence: {w['confidence']}")
            print(f"    Sensors tested: {tested}")
            print(f"    Notes: {w['notes']}")
        print()
        return

    if args.add:
        db = load_wrecks_db()
        if args.add in db:
            print(f"Wreck '{args.add}' already exists")
            return
        if not args.bbox:
            print("--bbox is required: lat_min,lon_min,lat_max,lon_max")
            return
        parts = [float(x) for x in args.bbox.split(',')]
        db[args.add] = {
            "name": args.add,
            "lat_min": parts[0], "lat_max": parts[2],
            "lon_min": parts[1], "lon_max": parts[3],
            "depth_ft": 0, "type": "unknown", "confidence": "low",
            "notes": "", "sensors_tested": [], "probe_results": [],
        }
        save_wrecks_db(db)
        print(f"Added wreck: {args.add}")
        return

    if args.report:
        db = load_wrecks_db()
        for wid, w in db.items():
            results = w.get("probe_results", [])
            print(f"\n{wid}: {len(results)} probe runs, "
                  f"{len(w.get('sensors_tested', []))} sensors tested")
        return

    if args.once:
        probes = args.probes.split(',') if args.probes else None
        report = probe_all_wrecks(zscore=args.zscore, probes=probes)
        print(json.dumps(report, indent=2)[:2000])
        return

    if args.run:
        probes = args.probes.split(',') if args.probes else None
        background_loop(interval_minutes=args.interval, max_iterations=args.max)
        return

    parser.print_help()


if __name__ == '__main__':
    main()
