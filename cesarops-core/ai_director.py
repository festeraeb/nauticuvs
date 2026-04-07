#!/usr/bin/env python3
"""
AI DIRECTOR — Qwen-Powered Tool Picker, Parameter Tuner & Data Interpreter

What it does:
1. Takes user requests (natural language)
2. Calls Qwen LLM to pick tools, set bounding boxes, tune parameters
3. Executes the tools
4. Sends results back to Qwen for interpretation & next-step recommendations

Usage:
    python ai_director.py --request "Search for Gilcher near Fox Islands"
    python ai_director.py --request "Find triple locks at Beaver Islands" --execute
    python ai_director.py --bbox 45.8,-84.6,46.0,-84.4 --tools thermal,optical --sensitivity 1.5
    python ai_director.py --interpret outputs/probes/*.json
"""

import argparse
import json
import os
import subprocess
import sys
import requests
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

# ── API Key Loading ───────────────────────────────────────────────────────────

def load_env(path: Path) -> Dict[str, str]:
    """Parse a simple .env file into a dict."""
    env = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            env[key.strip()] = val.strip()
    return env

_dotenv = load_env(Path(__file__).parent / ".env")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", _dotenv.get("QWEN_API_KEY", ""))
QWEN_MODEL = os.environ.get("QWEN_MODEL", _dotenv.get("QWEN_MODEL", "qwen-plus"))
QWEN_BASE_URL = os.environ.get("QWEN_BASE_URL", _dotenv.get("QWEN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1"))

# ── API Key Setup Helper ─────────────────────────────────────────────────────

def check_and_prompt_api_keys() -> Dict[str, str]:
    """
    Check for required API keys and prompt user interactively if missing.
    Returns a dict of found/entered keys. Creates .env file if it doesn't exist.
    """
    env_path = Path(__file__).parent / ".env"
    keys_found = {}
    keys_needed = {}

    # Check existing keys
    if QWEN_API_KEY:
        keys_found['QWEN_API_KEY'] = QWEN_API_KEY[:20] + "..."
    else:
        keys_needed['QWEN_API_KEY'] = {
            'prompt': "Qwen (DashScope) API key — for AI Director parsing & result interpretation",
            'url': "https://dashscope.console.aliyun.com/",
        }

    earthdata = os.environ.get("EARTHDATA_TOKEN", _dotenv.get("EARTHDATA_TOKEN", ""))
    if earthdata:
        keys_found['EARTHDATA_TOKEN'] = earthdata[:20] + "..."
    else:
        keys_needed['EARTHDATA_TOKEN'] = {
            'prompt': "NASA Earthdata token — for SWOT, ICESat-2, and HLS downloads",
            'url': "https://urs.earthdata.nasa.gov/",
        }

    # Report status
    if keys_found:
        print("\n✓ API Keys Found:")
        for key, val in keys_found.items():
            print(f"  {key}: {val}")

    if keys_needed:
        print("\n⚠ Missing API Keys:")
        for key, info in keys_needed.items():
            print(f"  ✗ {key}: {info['prompt']}")
            print(f"    Register at: {info['url']}")

        # Interactive prompt
        print("\n──────────────────────────────────────────────────────────────")
        print("You can enter API keys now, or skip and use --no-llm mode.")
        print("Keys will be saved to .env for future use.")
        print("──────────────────────────────────────────────────────────────\n")

        new_keys = {}
        for key, info in keys_needed.items():
            while True:
                val = input(f"Enter {key} (or press Enter to skip): ").strip()
                if val:
                    new_keys[key] = val
                    break
                elif input("  Skip this key? (y/n): ").strip().lower() == 'n':
                    continue  # Ask again
                else:
                    break  # Skip

        if new_keys:
            # Load existing .env or create new one
            existing = {}
            if env_path.exists():
                existing = load_env(env_path)

            existing.update(new_keys)

            # Write .env file
            lines = []
            for k, v in existing.items():
                lines.append(f"{k}={v}")
            env_path.write_text('\n'.join(lines) + '\n')

            print(f"\n✓ Saved {len(new_keys)} key(s) to {env_path}")

            # Update current session via module reload
            import importlib
            if 'QWEN_API_KEY' in new_keys:
                # Directly update the module-level variable
                import sys
                mod = sys.modules[__name__]
                mod.QWEN_API_KEY = new_keys['QWEN_API_KEY']

            return new_keys

    return {}


# ── Bounding box presets ─────────────────────────────────────────────────────

BOUNDING_BOXES = {
    'fox_islands': {
        'name': 'Fox Islands',
        'lat_min': 45.80, 'lat_max': 46.00,
        'lon_min': -84.60, 'lon_max': -84.40,
        'targets': ['Gilcher'],
    },
    'beaver_islands': {
        'name': 'Beaver Islands',
        'lat_min': 45.60, 'lat_max': 45.80,
        'lon_min': -85.60, 'lon_max': -85.40,
        'targets': ['Parnell'],
    },
    'bridge_builder_x': {
        'name': 'Bridge Builder X Area',
        'lat_min': 45.70, 'lat_max': 45.80,
        'lon_min': -84.70, 'lon_max': -84.50,
        'targets': ['Bridge Builder X'],
    },
    'lake_michigan_south': {
        'name': 'Lake Michigan South (Andaste)',
        'lat_min': 42.30, 'lat_max': 43.20,
        'lon_min': -88.50, 'lon_max': -87.40,
        'targets': ['Andaste', 'Chicorah'],
    },
    'lake_michigan_north': {
        'name': 'Lake Michigan North',
        'lat_min': 43.20, 'lat_max': 45.00,
        'lon_min': -87.50, 'lon_max': -86.00,
        'targets': [],
    },
    'straits_of_mackinac': {
        'name': 'Straits of Mackinac',
        'lat_min': 45.80, 'lat_max': 46.10,
        'lon_min': -84.80, 'lon_max': -84.40,
        'targets': [],
    },
}

# ── Tool metadata (sent to Qwen as reference) ────────────────────────────────

AVAILABLE_TOOLS = {
    'thermal': {
        'script': 'lake_michigan_scan.py',
        'description': 'Thermal cold-sink detection (Landsat B10/B11)',
        'default_threshold': 2.5,
        'best_for': ['steel masses', 'large vessels', 'engine blocks'],
        'resolution': '100m/pixel',
    },
    'optical': {
        'script': 'lake_michigan_scan.py',
        'description': 'Optical glint detection (Sentinel-2 B04/B08)',
        'default_threshold': 2.0,
        'best_for': ['aluminum', 'aircraft', 'surface debris'],
        'resolution': '10m/pixel',
    },
    'sar': {
        'script': 'lake_michigan_scan.py',
        'description': 'SAR VV/VH ratio (Sentinel-1)',
        'default_threshold': 2.0,
        'best_for': ['heavy steel', 'dense masses', 'submerged structures'],
        'resolution': '20m/pixel',
    },
    'triple_lock': {
        'script': 'triple_lock_fusion.py',
        'description': 'Multi-sensor fusion (thermal + optical + SAR)',
        'default_threshold': 2.5,
        'best_for': ['high confidence targets', 'verification'],
    },
    'vrt_slicer': {
        'script': 'cesarops-slicer (Rust)',
        'description': 'VRT Master Stack — multi-source GeoTIFF slicer with coordinate baking',
        'default_threshold': None,
        'best_for': ['multi-provider alignment', 'Sentinel+Landsat fusion'],
    },
    'swot': {
        'script': 'swot_ssh_extractor.py',
        'description': 'SWOT Ka-band sea surface height displacement',
        'default_threshold': 1.5,
        'best_for': ['large displacement', 'hull shapes'],
    },
    'atl13': {
        'script': 'swot_ssh_extractor.py',
        'description': 'ICESat-2 ATL13 bathymetry',
        'default_threshold': 2.0,
        'best_for': ['depth verification', 'seafloor mapping'],
    },
}


# ── Qwen LLM client ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the AI Director for CESAROPS — a multi-sensor wreck detection system for the Great Lakes.

IMPORTANT: Your job is to TUNE existing tool parameters. You are NOT allowed to write new code,
create new scripts, or build new tools. Only adjust the knobs on existing tools:
bounding boxes, sensitivity values, z-score thresholds, tile sizes, delegate assignments,
resampling methods, and which tools to run. If you think new code is needed, say so
explicitly and WAIT for human approval before generating any code.

The user gives you a natural language request. You must respond with ONLY a JSON object
(no markdown, no explanation) in this exact schema:

{
  "bbox_name": "<one of the preset names below, or null for custom>",
  "bbox": [lat_min, lon_min, lat_max, lon_max],   // use null if bbox_name is set
  "tools": ["tool_id", ...],                        // list of tool IDs from AVAILABLE_TOOLS
  "sensitivity": <1.0-3.0>,                         // 1.0=aggressive, 2.0=balanced, 3.0=conservative
  "thresholds": {"thermal_zscore": 2.5, ...},       // optional per-sensor threshold overrides
  "reasoning": "<brief explanation of your choices>"
}

Preset bounding boxes:
"""

def _build_system_prompt() -> str:
    prompt = SYSTEM_PROMPT
    for name, bbox in BOUNDING_BOXES.items():
        prompt += (
            f"  {name}: "
            f"[{bbox['lat_min']}, {bbox['lon_min']}, "
            f"{bbox['lat_max']}, {bbox['lon_max']}] "
            f"→ {bbox['name']}\n"
        )
    prompt += "\nAvailable tools:\n"
    for tid, tinfo in AVAILABLE_TOOLS.items():
        prompt += (
            f"  {tid}: {tinfo['description']} "
            f"(best for: {', '.join(tinfo['best_for'])})\n"
        )
    prompt += (
        "\nRules:\n"
        "- If the user mentions a known wreck name, pick the matching bbox.\n"
        "- If unclear, default to 'lake_michigan_south'.\n"
        "- Always include at least one tool.\n"
        "- Use sensitivity 2.0 unless the user says 'aggressive' (1.0) or 'conservative/strict' (3.0).\n"
        "- Respond with ONLY valid JSON. No markdown. No extra text.\n"
    )
    return prompt


def call_qwen(messages: List[Dict]) -> str:
    """Send messages to Qwen via DashScope compatible API."""
    if not QWEN_API_KEY:
        raise RuntimeError(
            "QWEN_API_KEY not set. Add it to .env or export QWEN_API_KEY=sk-..."
        )

    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": QWEN_MODEL,
        "messages": messages,
        "temperature": 0.3,
    }

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def parse_with_qwen(user_request: str) -> Dict:
    """Use Qwen to parse a natural language request into tool config."""
    system = _build_system_prompt()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_request},
    ]

    raw = call_qwen(messages)

    # Extract JSON from response (strip markdown code blocks if present)
    raw = raw.strip()
    if raw.startswith("```"):
        # strip ```json or ```
        first_nl = raw.index("\n")
        raw = raw[first_nl + 1:]
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    parsed = json.loads(raw)
    return parsed


def interpret_results_with_qwen(results: List[Dict], config: Dict) -> str:
    """Send tool execution results to Qwen for interpretation and next steps."""
    # Build a concise summary of results
    summary_parts = []
    for r in results:
        tool = r.get('tool', 'unknown')
        status = "SUCCESS" if r.get('success') else f"FAILED: {r.get('error', r.get('stderr', '')[:200])}"
        summary_parts.append(f"{tool}: {status}")

        # Extract key lines from stdout
        if r.get('stdout'):
            for line in r['stdout'].split('\n'):
                if any(kw in line.lower() for kw in ['detection', 'anomalies', 'lock', 'complete', 'total', 'fused']):
                    summary_parts.append(f"  → {line.strip()}")

    summary = "\n".join(summary_parts)

    messages = [
        {"role": "system", "content": (
            "You are a Great Lakes wreck detection analyst AI. "
            "Review the CESAROPS sensor probe results below and provide:\n"
            "1. A brief executive summary of what was found\n"
            "2. Key anomalies worth investigating\n"
            "3. Recommended next steps — ONLY parameter adjustments on existing tools "
            "(e.g., 'raise thermal_zscore to 3.0', 'narrow bbox to ...', 'add SAR sensor'). "
            "Do NOT suggest writing new code or new tools without explicit human approval.\n"
            "4. Confidence assessment of any detected targets\n"
            "Be concise. Use bullet points."
        )},
        {"role": "user", "content": (
            f"Search config:\n"
            f"  Area: {config.get('bbox', {}).get('name', 'Unknown')}\n"
            f"  Tools: {config.get('tools', [])}\n"
            f"  Sensitivity: {config.get('sensitivity', 2.0)}\n\n"
            f"Results:\n{summary}"
        )},
    ]

    return call_qwen(messages)


# ── AI Director class ────────────────────────────────────────────────────────

class AIDirector:
    """AI Director — Qwen-powered tool picker, executor, and interpreter."""

    def __init__(self, use_llm: bool = True):
        self.results = []
        self.use_llm = use_llm
        self.config = {
            'bbox': None,
            'tools': [],
            'sensitivity': 2.0,
            'thresholds': {},
        }

    def parse_request(self, request: str) -> Dict:
        """Parse natural language request into tool config."""
        if self.use_llm and QWEN_API_KEY:
            try:
                print("  🤖 Calling Qwen LLM for tool selection...")
                parsed = parse_with_qwen(request)
                reasoning = parsed.pop('reasoning', '')
                if reasoning:
                    print(f"  💡 Qwen reasoning: {reasoning}")
                return parsed
            except Exception as e:
                print(f"  ⚠ Qwen LLM failed ({e}), falling back to keyword matching")

        # Keyword matching fallback
        request_lower = request.lower()
        bbox_name = None
        for name, bbox in BOUNDING_BOXES.items():
            if any(kw in request_lower for kw in name.lower().split('_')):
                bbox_name = name
                break
            if any(t.lower() in request_lower for t in bbox.get('targets', [])):
                bbox_name = name
                break

        tools = []
        keyword_map = {
            ('thermal', 'cold', 'heat', 'sink'): 'thermal',
            ('optical', 'glint', 'aluminum', 'aircraft'): 'optical',
            ('sar', 'vv', 'vh', 'radar'): 'sar',
            ('fusion', 'triple', 'lock', 'verify'): 'triple_lock',
            ('swot', 'displacement', 'mass'): 'swot',
            ('icesat', 'atl13', 'bathy', 'depth'): 'atl13',
            ('vrt', 'stack', 'multi-source', 'multi source'): 'vrt_slicer',
        }
        for kws, tool in keyword_map.items():
            if any(kw in request_lower for kw in kws):
                tools.append(tool)
        if not tools:
            tools = ['thermal', 'optical']

        sensitivity = 2.0
        if any(w in request_lower for w in ['conservative', 'strict', 'high confidence']):
            sensitivity = 3.0
        elif any(w in request_lower for w in ['aggressive', 'sensitive', 'all', 'wide net']):
            sensitivity = 1.0

        return {'bbox_name': bbox_name, 'tools': tools, 'sensitivity': sensitivity}

    # ── Config setters ───────────────────────────────────────────────────

    def set_bounding_box(self, bbox_name: str = None,
                         lat_min=None, lat_max=None, lon_min=None, lon_max=None):
        if bbox_name and bbox_name in BOUNDING_BOXES:
            self.config['bbox'] = BOUNDING_BOXES[bbox_name]
        elif all(v is not None for v in [lat_min, lat_max, lon_min, lon_max]):
            self.config['bbox'] = {
                'name': 'Custom',
                'lat_min': lat_min, 'lat_max': lat_max,
                'lon_min': lon_min, 'lon_max': lon_max,
            }
        else:
            self.config['bbox'] = BOUNDING_BOXES['lake_michigan_south']
        print(f"✓ Area: {self.config['bbox']['name']}")

    def set_tools(self, tools: List[str]):
        valid = [t for t in tools if t in AVAILABLE_TOOLS]
        self.config['tools'] = valid
        print(f"✓ Tools: {', '.join(valid)}")

    def set_parameters(self, sensitivity: float = None, thresholds: Dict = None):
        if sensitivity is not None:
            self.config['sensitivity'] = sensitivity
        if thresholds:
            self.config['thresholds'] = thresholds
        print(f"✓ Sensitivity: {self.config['sensitivity']}")

    # ── Execution ──────────────────────────────────────────────────────────

    def run_tool(self, tool_name: str) -> Dict:
        tool = AVAILABLE_TOOLS[tool_name]
        script_path = Path(__file__).parent / tool['script']

        if not script_path.exists():
            return {'tool': tool_name, 'success': False,
                    'error': f'Script not found: {script_path}'}

        cmd = [sys.executable, str(script_path)]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
            )
            return {
                'tool': tool_name,
                'success': result.returncode == 0,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {'tool': tool_name, 'success': False,
                    'error': 'Timed out (10 min)'}
        except Exception as e:
            return {'tool': tool_name, 'success': False, 'error': str(e)}

    def execute(self) -> List[Dict]:
        print(f"\n{'='*70}")
        print(f"AI DIRECTOR — {len(self.config['tools'])} tools")
        print(f"  Area: {self.config['bbox']['name']}")
        print(f"  Sensitivity: {self.config['sensitivity']}")
        print(f"  Tools: {', '.join(self.config['tools'])}")
        print(f"{'='*70}")

        results = []
        for tool_name in self.config['tools']:
            print(f"\n[{len(results)+1}/{len(self.config['tools'])}] {tool_name}...")
            result = self.run_tool(tool_name)
            results.append(result)
            status = "✓" if result.get('success') else "✗"
            print(f"  {status} {tool_name}")

        self.results = results
        return results

    def summarize(self) -> str:
        lines = [f"\n{'='*70}", "EXECUTION SUMMARY", f"{'='*70}"]
        for r in self.results:
            tool = r.get('tool', '?')
            status = "✓ SUCCESS" if r.get('success') else f"✗ FAILED: {r.get('error', '')}"
            lines.append(f"\n  {tool}: {status}")
            if r.get('stdout'):
                for line in r['stdout'].split('\n'):
                    if any(kw in line.lower() for kw in ['detection', 'anomalies', 'lock', 'complete', 'total', 'fused', 'triple']):
                        lines.append(f"    {line.strip()}")
        return '\n'.join(lines)

    # ── Interpretation ─────────────────────────────────────────────────────

    def interpret(self) -> str:
        """Send results to Qwen for interpretation."""
        if not QWEN_API_KEY:
            print("  ⚠ QWEN_API_KEY not set, skipping interpretation")
            return self.summarize()

        try:
            print("\n  🤖 Sending results to Qwen for interpretation...")
            briefing = interpret_results_with_qwen(self.results, self.config)
            return briefing
        except Exception as e:
            print(f"  ⚠ Interpretation failed: {e}")
            return self.summarize()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='AI Director — Qwen-Powered CESAROPS Orchestrator')
    parser.add_argument('--request', '-r', type=str, help='Natural language request')
    parser.add_argument('--bbox', type=str, help='Bounding box: lat_min,lon_min,lat_max,lon_max or preset name')
    parser.add_argument('--tools', type=str, help='Comma-separated tool list')
    parser.add_argument('--sensitivity', type=float, default=2.0, help='1.0=aggressive, 3.0=conservative')
    parser.add_argument('--list-tools', '-l', action='store_true')
    parser.add_argument('--execute', '-x', action='store_true')
    parser.add_argument('--interpret', '-i', type=str, nargs='*', help='Interpret existing result JSON files')
    parser.add_argument('--no-llm', action='store_true', help='Disable Qwen, use keyword matching')
    parser.add_argument('--output', '-o', type=str, help='Save results to JSON')
    parser.add_argument('--setup-keys', action='store_true', help='Interactively configure API keys')
    parser.add_argument('--data-sources', action='store_true', help='List available satellite data sources')
    args = parser.parse_args()

    # ── Setup API keys ─────────────────────────────────────────────────────
    if args.setup_keys:
        check_and_prompt_api_keys()
        return

    # ── List data sources ──────────────────────────────────────────────────
    if args.data_sources:
        src_path = Path(__file__).parent / "satellite_data_sources.json"
        if src_path.exists():
            data = json.loads(src_path.read_text())
            print(f"\n{'='*100}")
            print("SATELLITE DATA SOURCES")
            print(f"{'='*100}")
            for src_id, src in data['satellite_data_sources'].items():
                print(f"\n  {src_id}")
                print(f"    Name: {src['name']}")
                print(f"    {src['description']}")
                print(f"    URL: {src['url']}")
                print(f"    Auth: {'API key required' if src['requires_api_key'] else 'Open access'}")
                if 'env_key' in src:
                    print(f"    Env var: {src['env_key']}")
                if 'notes' in src:
                    print(f"    Notes: {src['notes']}")
            print(f"\n{'='*100}")
        else:
            print("satellite_data_sources.json not found")
        return

    if not QWEN_API_KEY:
        print("⚠ QWEN_API_KEY not set — LLM features disabled")
        print("  Run: python ai_director.py --setup-keys")
        print("  Or set in .env: QWEN_API_KEY=sk-...")

    # ── Interpret mode ─────────────────────────────────────────────────────
    if args.interpret is not None:
        results_data = []
        for fp in args.interpret:
            p = Path(fp)
            if p.exists():
                results_data.append(json.loads(p.read_text()))
            else:
                print(f"  ⚠ Not found: {fp}")

        if results_data and QWEN_API_KEY:
            messages = [
                {"role": "system", "content": (
                    "You are a Great Lakes wreck detection analyst AI. "
                    "Review these CESAROPS sensor results and provide:\n"
                    "1. Executive summary\n"
                    "2. Key anomalies to investigate\n"
                    "3. Recommended next steps\n"
                    "Be concise, use bullet points."
                )},
                {"role": "user", "content": json.dumps(results_data, indent=2)[:4000]},
            ]
            print(call_qwen(messages))
        elif results_data:
            print("  ⚠ QWEN_API_KEY not set, cannot interpret")
        return

    # ── List tools ─────────────────────────────────────────────────────────
    if args.list_tools:
        print(f"\n{'='*100}")
        print("AVAILABLE TOOLS")
        print(f"{'='*100}")
        for tid, t in AVAILABLE_TOOLS.items():
            print(f"\n  {tid}")
            print(f"    {t['description']}")
            print(f"    Best for: {', '.join(t['best_for'])}")
        print(f"\n{'='*100}")
        return

    # ── Run ────────────────────────────────────────────────────────────────
    director = AIDirector(use_llm=not args.no_llm)

    if args.request:
        print(f"\nRequest: {args.request}")
        parsed = director.parse_request(args.request)

        if parsed.get('bbox_name'):
            director.set_bounding_box(parsed['bbox_name'])
        elif parsed.get('bbox'):
            b = parsed['bbox']
            director.set_bounding_box(lat_min=b[0], lon_min=b[1], lat_max=b[2], lon_max=b[3])

        director.set_tools(parsed.get('tools', []))
        director.set_parameters(
            sensitivity=parsed.get('sensitivity', args.sensitivity),
            thresholds=parsed.get('thresholds'),
        )
    else:
        # Manual config
        if args.bbox:
            if args.bbox in BOUNDING_BOXES:
                director.set_bounding_box(args.bbox)
            else:
                try:
                    parts = [float(x) for x in args.bbox.split(',')]
                    director.set_bounding_box(lat_min=parts[0], lon_min=parts[1],
                                              lat_max=parts[2], lon_max=parts[3])
                except Exception:
                    print(f"Invalid bbox: {args.bbox}")
        if args.tools:
            director.set_tools(args.tools.split(','))
        director.set_parameters(sensitivity=args.sensitivity)

    if args.execute or args.request:
        results = director.execute()

        # Interpretation
        print(f"\n{'='*70}")
        print("QWEN INTERPRETATION")
        print(f"{'='*70}")
        briefing = director.interpret()
        print(briefing)

        # Save
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                'timestamp': datetime.now().isoformat(),
                'config': director.config,
                'results': results,
                'summary': director.summarize(),
                'interpretation': briefing,
            }, indent=2))
            print(f"\n✓ Saved: {out}")


if __name__ == '__main__':
    main()
