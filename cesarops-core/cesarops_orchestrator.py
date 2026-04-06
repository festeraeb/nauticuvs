#!/usr/bin/env python3
"""
CESAROPS FULL SENSOR PROBE ORCHESTRATOR
Designed for Agent Execution & Parameter Tuning
Fires multiple sensors, fuses results, outputs GeoJSON map.

Modes:
  Local:    runs sensors on this machine
  Remote:   dispatches Pi (slice) → Xenon (process) via SSH
  Hybrid:   local sensors + remote processing

Qwen LLM integration:
    --llm-plan "<request>"     Ask Qwen to design the sensor plan from natural language
    --llm-interpret             Send results to Qwen for interpretation after run

Remote dispatch:
    --remote                    Use Pi→Xenon pipeline instead of local execution
    --status                    Ping all nodes and show connectivity
    --pi-slice                  Run VRT stack + slice on Pi only
    --xenon-process             Run TPU/GPU processing on Xenon only
"""

import argparse
import json
import subprocess
import sys
import os
import requests
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
        "cpu_fallback": True,
        "timeout_min": 30,
        "fail_fast": False
    }
}
# ============================================================================

# ── Qwen LLM helpers ─────────────────────────────────────────────────────────

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


def call_qwen(messages: list) -> str:
    """Send messages to Qwen via DashScope compatible API."""
    if not QWEN_API_KEY:
        raise RuntimeError("QWEN_API_KEY not set")
    url = f"{QWEN_BASE_URL}/chat/completions"
    resp = requests.post(url, headers={
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }, json={"model": QWEN_MODEL, "messages": messages, "temperature": 0.3}, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def llm_generate_sensor_plan(user_request: str) -> dict:
    """Ask Qwen to design a sensor plan from a natural language request."""
    areas_json = json.dumps({k: v["label"] for k, v in AGENT_CONFIG["areas"].items()})
    system_msg = (
        f"You are the CESAROPS sensor orchestrator. "
        f"Given a user request, choose the right sensors, area, and thresholds.\n\n"
        f"IMPORTANT: You are ONLY allowed to tune existing parameters — area, sensor list, "
        f"and threshold values. Do NOT write new code or create new tools.\n\n"
        f"Available areas: {areas_json}\n"
        f"Available sensors: thermal, nir_swir, sar, swot\n\n"
        f"Respond with ONLY valid JSON matching this schema:\n"
        '{{"area": "<area_key>", "sensors": ["sensor", ...], "thresholds": {{"thermal_zscore": N, ...}}, "reasoning": "..."}}'
    )
    raw = call_qwen([
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_request},
    ])
    # Strip markdown code blocks
    raw = raw.strip()
    if raw.startswith("```"):
        nl = raw.index("\n")
        raw = raw[nl + 1:]
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    return json.loads(raw)


def llm_interpret_results(results: list, config: dict) -> str:
    """Send results to Qwen for interpretation and next-step recommendations."""
    summary = []
    for r in results:
        status = "OK" if r["status"] == "success" else r.get("status", "unknown")
        summary.append(f"  {r['sensor']}: {status}")
        if r.get("error"):
            summary.append(f"    Error: {r['error'][:200]}")

    return call_qwen([
        {"role": "system", "content": (
            "You are a Great Lakes wreck detection analyst. "
            "Review these CESAROPS sensor probe results and provide:\n"
            "1. Executive summary\n"
            "2. Key anomalies to investigate\n"
            "3. Recommended next steps — ONLY parameter adjustments on existing tools "
            "(e.g., 'raise thermal_zscore to 3.0', 'add sar sensor', 'narrow bbox'). "
            "Do NOT suggest writing new code or new tools without explicit human approval.\n"
            "Be concise, use bullet points."
        )},
        {"role": "user", "content": (
            f"Area: {config.get('area', 'unknown')}\n"
            f"Thresholds: {json.dumps(config.get('thresholds', {}))}\n\n"
            f"Results:\n" + "\n".join(summary)
        )},
    ])

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
        "sar": ("lake_michigan_scan.py", [ # Adjust script name as needed
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
            # In production, parse actual KMZ/JSON from output_dir.
            # For now, we log the sensor hit as a map point.
            pass
            
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
    parser.add_argument("--llm-plan", type=str, help='Ask Qwen to design sensor plan from natural language')
    parser.add_argument("--llm-interpret", action="store_true", help="Send results to Qwen after run")

    # Remote dispatch
    parser.add_argument("--remote", action="store_true", help="Use Pi→Xenon pipeline via SSH")
    parser.add_argument("--status", action="store_true", help="Ping all nodes and show connectivity")
    parser.add_argument("--pi-slice", action="store_true", help="Run VRT stack + slice on Pi only")
    parser.add_argument("--xenon-process", action="store_true", help="Run TPU/GPU processing on Xenon only")
    parser.add_argument("--tile-size", type=int, default=1024, help="Tile size for slicing")
    parser.add_argument("--target-res", type=float, default=10.0, help="Target resolution in meters")
    args = parser.parse_args()

    # ── Node status ────────────────────────────────────────────────────────
    if args.status:
        from remote_dispatch import TaskDispatcher
        d = TaskDispatcher()
        status = d.status()
        print(json.dumps(status, indent=2))
        d.close()
        return

    # ── Pi-only: slice ─────────────────────────────────────────────────────
    if args.pi_slice:
        from remote_dispatch import TaskDispatcher, build_pi_slice_task
        d = TaskDispatcher()

        area_name = args.area
        area_cfg = AGENT_CONFIG["areas"][area_name]
        bbox = area_cfg["bbox"]

        # Build source list from sensor config
        sources = [
            f"sentinel_b2.tif:B2_Blue:sentinel-2a",
            f"sentinel_b4.tif:B4_Red:sentinel-2a",
            f"landsat_b10.tif:B10_Thermal:landsat-9",
        ]

        result = d.task_pi_slice(
            area_name=area_name,
            bbox=bbox,
            sources=sources,
            tile_size=args.tile_size,
            target_resolution=args.target_res,
        )
        print(json.dumps(result, indent=2))
        d.close()
        return

    # ── Xenon-only: process ────────────────────────────────────────────────
    if args.xenon_process:
        from remote_dispatch import TaskDispatcher
        d = TaskDispatcher()
        result = d.task_xenon_process()
        print(json.dumps(result, indent=2))
        d.close()
        return

    # ── Full remote pipeline: Pi slices → Xenon processes ──────────────────
    if args.remote:
        from remote_dispatch import TaskDispatcher
        d = TaskDispatcher()

        area_name = args.area
        area_cfg = AGENT_CONFIG["areas"][area_name]
        bbox = area_cfg["bbox"]

        log(f"=" * 60)
        log(f"🛰️  REMOTE PIPELINE: {area_cfg['label']}")
        log(f"  Step 1: Pi → VRT stack + slice + delegate routing")
        log(f"  Step 2: Xenon → TPU/GPU processing on staged tiles")
        log(f"=" * 60)

        # Step 1: Pi slices
        sources = [
            f"sentinel_b2.tif:B2_Blue:sentinel-2a",
            f"sentinel_b4.tif:B4_Red:sentinel-2a",
            f"landsat_b10.tif:B10_Thermal:landsat-9",
        ]
        pi_result = d.task_pi_slice(
            area_name=area_name,
            bbox=bbox,
            sources=sources,
            tile_size=args.tile_size,
            target_resolution=args.target_res,
        )

        if pi_result.get("exit_code") != 0:
            log("❌ Pi slicing failed — aborting pipeline", "ERROR")
            d.close()
            return

        # Step 2: Xenon processes
        xenon_result = d.task_xenon_process()

        results = [
            {"sensor": "pi_slicer", "status": "success" if pi_result["exit_code"] == 0 else "failed",
             "output": pi_result.get("stdout", "")},
            {"sensor": "xenon_processor", "status": "success" if xenon_result["exit_code"] == 0 else "failed",
             "output": xenon_result.get("stdout", "")},
        ]

        out_file = f"outputs/probes/remote_{area_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.geojson"
        fuse_to_geojson(results, out_file)

        # LLM interpretation
        if args.llm_interpret:
            if QWEN_API_KEY:
                log("🤖 Sending results to Qwen for interpretation...")
                try:
                    briefing = llm_interpret_results(results, {
                        "area": area_name,
                        "thresholds": AGENT_CONFIG["thresholds"],
                    })
                    log("=" * 60)
                    log("QWEN BRIEFING")
                    log("=" * 60)
                    print(briefing)
                except Exception as e:
                    log(f"Interpretation failed: {e}", "ERROR")
            else:
                log("QWEN_API_KEY not set — skipping interpretation", "WARN")

        successes = [r for r in results if r["status"] == "success"]
        log("=" * 60)
        log(f"📊 REMOTE PIPELINE COMPLETE: {len(successes)}/2 steps succeeded")
        log(f"📁 Output: {out_file}")
        log("=" * 60)
        d.close()
        return

    # ── LLM plan mode ──────────────────────────────────────────────────────
    if args.llm_plan:
        if not QWEN_API_KEY:
            log("QWEN_API_KEY not set — cannot use --llm-plan", "ERROR")
            return
        log(f"🤖 Asking Qwen to design sensor plan: {args.llm_plan}")
        plan = llm_generate_sensor_plan(args.llm_plan)
        log(f"💡 Qwen reasoning: {plan.get('reasoning', '')}")

        area_name = plan.get("area", args.area)
        sensors = plan.get("sensors", AGENT_CONFIG["sensors"])
        if plan.get("thresholds"):
            AGENT_CONFIG["thresholds"].update(plan["thresholds"])
        log(f"   Area: {area_name}")
        log(f"   Sensors: {sensors}")
        log(f"   Thresholds: {AGENT_CONFIG['thresholds']}")
    else:
        area_name = args.area
        sensors = args.sensors or AGENT_CONFIG["sensors"]

    area_cfg = AGENT_CONFIG["areas"][area_name]

    log("=" * 60)
    log(f"🛰️  STARTING PROBE: {area_cfg['label']}")
    log(f"📡 SENSORS: {', '.join(sensors)}")
    log(f"🤖 AGENT CONFIG LOADED")
    log("=" * 60)

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

    # ── LLM interpretation ─────────────────────────────────────────────────
    if args.llm_interpret:
        if QWEN_API_KEY:
            log("🤖 Sending results to Qwen for interpretation...")
            try:
                briefing = llm_interpret_results(results, {
                    "area": area_name,
                    "thresholds": AGENT_CONFIG["thresholds"],
                })
                log("=" * 60)
                log("QWEN BRIEFING")
                log("=" * 60)
                print(briefing)
            except Exception as e:
                log(f"Interpretation failed: {e}", "ERROR")
        else:
            log("QWEN_API_KEY not set — skipping interpretation", "WARN")

    # Print Agent Summary
    successes = [r for r in results if r["status"] == "success"]
    log("=" * 60)
    log(f"📊 PROBE COMPLETE: {len(successes)}/{len(sensors)} sensors succeeded")
    log(f"📁 Output: {out_file}")
    log("🤖 Agent can now review logs, adjust thresholds, and re-run.")
    log("=" * 60)

if __name__ == "__main__":
    main()