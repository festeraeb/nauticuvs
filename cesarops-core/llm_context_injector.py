#!/usr/bin/env python3
"""
LLM Context Injector — feeds satellite data sources and learning state into model prompts.

This module loads satellite_data_sources.json and the learning history, then provides
context snippets that get injected into system prompts for any LLM backend (DashScope,
KoboldCPP, Ollama, etc).

Usage:
    from llm_context_injector import build_satellite_context, build_learning_context
"""

import json
from pathlib import Path
from typing import Dict, Optional

# ── Paths ────────────────────────────────────────────────────────────────────

CORE_DIR = Path(__file__).parent
SAT_SOURCES_JSON = CORE_DIR / "satellite_data_sources.json"
WRECKS_JSON = CORE_DIR / "known_wrecks.json"
WARP_CONFIG_JSON = CORE_DIR / "warp_config.json"

# ── Satellite Data Sources Context ───────────────────────────────────────────

def load_satellite_sources() -> dict:
    """Load satellite data sources JSON."""
    if SAT_SOURCES_JSON.exists():
        return json.loads(SAT_SOURCES_JSON.read_text())
    return {}


def build_satellite_context() -> str:
    """Build a concise context string describing all available satellite data sources.
    This gets injected into the LLM system prompt so the model knows what data
    is available, how to access it, and what auth is needed."""
    data = load_satellite_sources()
    sources = data.get("satellite_data_sources", {})

    lines = [
        "SATELLITE DATA SOURCES AVAILABLE:",
        "These are the data sources CESAROPS can pull from. When recommending data "
        "acquisition, reference these source IDs and their auth requirements.\n",
    ]

    for src_id, src in sources.items():
        name = src.get("name", src_id)
        desc = src.get("description", "")
        requires_key = src.get("requires_api_key", False)
        env_key = src.get("env_key", "")
        collections = src.get("collections", [])
        bands = src.get("bands", {})
        resolution = src.get("resolution", {})
        best_for = src.get("best_for", [])
        notes = src.get("notes", "")

        lines.append(f"  [{src_id}] {name}")
        lines.append(f"    Description: {desc}")
        if requires_key and env_key:
            lines.append(f"    Auth: requires {env_key}")
        if collections:
            lines.append(f"    Collections: {', '.join(str(c) for c in collections)}")
        if bands:
            bands_str = ", ".join(f"{k}: {v}" if isinstance(v, str) else f"{k}: {', '.join(v)}"
                                  for k, v in bands.items())
            lines.append(f"    Bands: {bands_str}")
        if resolution:
            if isinstance(resolution, str):
                lines.append(f"    Resolution: {resolution}")
            else:
                res_parts = [f"{k}: {v}" for k, v in resolution.items()]
                lines.append(f"    Resolution: {'; '.join(res_parts)}")
        if best_for:
            lines.append(f"    Best for: {', '.join(best_for)}")
        if notes:
            lines.append(f"    Notes: {notes}")
        lines.append("")

    # Add data acquisition guidelines if present
    guidelines = data.get("data_acquisition_guidelines", {})
    if guidelines:
        lines.append("DATA ACQUISITION GUIDELINES:")
        for area, info in guidelines.items():
            lines.append(f"  {area}:")
            if "bbox" in info:
                lines.append(f"    BBOX: {info['bbox']}")
            if "best_months" in info:
                lines.append(f"    Best months: {', '.join(info['best_months'])}")
            if "recommended_sensors" in info:
                lines.append(f"    Recommended: {', '.join(info['recommended_sensors'])}")
            if "reason" in info:
                lines.append(f"    Reason: {info['reason']}")
            lines.append("")

    return "\n".join(lines)


# ── Knowledge Base ──────────────────────────────────────────────────────────

KB_JSON = CORE_DIR / "knowledge_base.json"

def load_knowledge_base() -> dict:
    """Load the living knowledge base."""
    if KB_JSON.exists():
        return json.loads(KB_JSON.read_text())
    return {}

def update_knowledge_base(scan_results: dict, fusion_results: dict = None):
    """Update the knowledge base after a scan. Add new targets, lessons learned."""
    kb = load_knowledge_base()

    # Update version timestamp
    kb["version"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    kb["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Add scan lesson if not already present
    if "scan_lessons" in kb:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        existing_dates = [e.get("date") for e in kb["scan_lessons"]]
        if today not in existing_dates:
            lesson = {
                "date": today,
                "scan_type": "auto_scan",
                "what_worked": [],
                "what_failed": [],
                "parameter_recommendations": {},
            }

            # Extract what worked/failed from results
            if "results" in scan_results:
                for r in scan_results["results"]:
                    tool = r.get("tool", "?")
                    if r.get("success"):
                        detections = sum(1 for d in r.get("detections", []))
                        lesson["what_worked"].append(f"{tool}: {detections} detections")
                    else:
                        lesson["what_failed"].append(f"{tool}: {r.get('error', 'failed')}")

            # Add fusion insights
            if fusion_results and "clusters" in fusion_results:
                clusters = fusion_results["clusters"]
                if clusters:
                    lesson["what_worked"].append(
                        f"Fusion found {len(clusters)} multi-sensor targets"
                    )
                    for c in clusters[:5]:
                        lesson["what_worked"].append(
                            f"  Target at lat={c['lat']:.4f} lon={c['lon']:.4f} "
                            f"({c['lock_type']}, {c['detection_count']} dets)"
                        )
                else:
                    lesson["what_failed"].append("No multi-sensor lock targets found")

            kb["scan_lessons"]["entries"].append(lesson)

    # Add new targets to known_targets
    if fusion_results and "clusters" in fusion_results:
        for c in fusion_results.get("clusters", []):
            if c.get("lock_count", 0) >= 2:
                # Check if this target already exists
                existing = kb.get("known_targets", {}).get("entries", [])
                is_new = True
                for entry in existing:
                    dist = haversine_distance(
                        entry.get("lat", 0), entry.get("lon", 0),
                        c["lat"], c["lon"]
                    )
                    if dist < 5000:  # Within 5km = same target
                        is_new = False
                        break

                if is_new:
                    kb["known_targets"]["entries"].append({
                        "id": f"target_{len(existing)+1:03d}",
                        "name": f"Unknown target at {c['lat']:.2f}, {c['lon']:.2f}",
                        "lat": c["lat"],
                        "lon": c["lon"],
                        "confidence": "low" if c["lock_count"] == 2 else "medium",
                        "sensors_agreeing": c.get("sensors", []),
                        "lock_type": c.get("lock_type", "unknown"),
                        "detection_count": c.get("detection_count", 0),
                        "first_seen": today,
                        "notes": f"Auto-detected during scan. {c['lock_type']} lock.",
                    })

    KB_JSON.write_text(json.dumps(kb, indent=2))
    return kb


def build_knowledge_context() -> str:
    """Build context from the knowledge base for LLM injection."""
    kb = load_knowledge_base()
    if not kb:
        return "KNOWLEDGE BASE: Empty. Run a scan to start building knowledge.\n"

    lines = ["CESAROPS KNOWLEDGE BASE — What We Know So Far:\n"]

    # Known targets
    targets = kb.get("known_targets", {}).get("entries", [])
    if targets:
        lines.append("KNOWN TARGETS:")
        for t in targets:
            status = "✓ scanned" if t.get("lock_type") != "not_scanned" else "○ not yet scanned"
            lines.append(f"  [{t['id']}] {t['name']} ({t['lat']:.4f}, {t['lon']:.4f}) "
                        f"confidence={t['confidence']} {status}")
            if t.get("notes"):
                lines.append(f"    → {t['notes']}")
        lines.append("")

    # Scan lessons
    lessons = kb.get("scan_lessons", {}).get("entries", [])
    if lessons:
        lines.append("SCAN LESSONS LEARNED:")
        for lesson in lessons[-3:]:  # Last 3 scans
            lines.append(f"  [{lesson['date']}] {lesson.get('scan_type', 'scan')}")
            for w in lesson.get("what_worked", [])[:5]:
                lines.append(f"    ✓ {w}")
            for f in lesson.get("what_failed", [])[:5]:
                lines.append(f"    ✗ {f}")
            params = lesson.get("parameter_recommendations", {})
            if params:
                lines.append(f"    Params: {', '.join(f'{k}={v}' for k, v in params.items())}")
        lines.append("")

    # Agent behavior rules
    rules = kb.get("agent_behavior", {}).get("rules", [])
    if rules:
        lines.append("AGENT BEHAVIOR RULES:")
        for r in rules:
            lines.append(f"  • {r}")
        lines.append("")

    # Techniques to try (idle mode)
    techniques = kb.get("techniques_to_try", {}).get("entries", [])
    not_tested = [t for t in techniques if t.get("status") == "not_tested"]
    if not_tested:
        lines.append("TECHNIQUES TO TRY (idle research):")
        for t in sorted(not_tested, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("priority", "low"), 3)):
            lines.append(f"  [{t.get('priority', '?')}] {t['name']} — {t.get('notes', '')[:100]}")
        lines.append("")

    return "\n".join(lines)


# ── Warp Config Context ─────────────────────────────────────────────────────

def build_warp_context() -> str:
    """Build context showing current GDAL warp configuration."""
    if WARP_CONFIG_JSON.exists():
        try:
            cfg = json.loads(WARP_CONFIG_JSON.read_text())
            return (
                f"GDAL WARP CONFIG: working_memory_mb={cfg.get('working_memory_mb', '?')}, "
                f"block_size={cfg.get('block_size', '?')}, "
                f"resampling={cfg.get('resampling', '?')}"
            )
        except Exception:
            return "GDAL WARP CONFIG: (could not parse)"
    return "GDAL WARP CONFIG: (not set, defaults: wm=4000, block=512, resample=lanczos)"


# ── Combined Context Builder ────────────────────────────────────────────────

def build_full_context(include_satellite: bool = True,
                       include_learning: bool = True,
                       include_warp: bool = True,
                       include_weather_prompt: bool = True) -> str:
    """Build combined context string for LLM injection."""
    parts = []
    if include_satellite:
        parts.append(build_satellite_context())
    if include_learning:
        parts.append(build_learning_context())
    if include_warp:
        parts.append(build_warp_context())
    if include_weather_prompt:
        parts.append(
            "WEATHER CHECK: Before recommending a scan, the agent should verify "
            "current weather conditions (cloud cover, wind, precipitation) for the "
            "target area. Use web search to check NOAA/NWS forecasts. "
            "Clear skies = good for optical/thermal. Overcast = SAR only. "
            "Heavy rain/wind = postpone scan."
        )
    return "\n".join(parts)
