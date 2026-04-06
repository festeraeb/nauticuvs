#!/usr/bin/env python3
"""
CESAROPS Remote Task Dispatcher — SSH to Pi and Xenon

The laptop orchestrator sends tasks to:
  - Pi (Janitor): download → VRT stack → slice → route tiles to delegate folders
  - Xenon (Waifu): process staged tiles via TPU server + GPU

Uses paramiko for SSH. Credentials from .env or environment variables.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


# ── Config ───────────────────────────────────────────────────────────────────

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

# Pi (Janitor)
PI_HOST = os.environ.get("PI_HOST", _dotenv.get("PI_HOST", "10.0.0.100"))
PI_USER = os.environ.get("PI_USER", _dotenv.get("PI_USER", "pi"))
PI_PASS = os.environ.get("PI_PASS", _dotenv.get("PI_PASS", ""))
PI_KEY = os.environ.get("PI_KEY", _dotenv.get("PI_KEY", ""))
PI_WORK = os.environ.get("PI_WORK", _dotenv.get("PI_WORK", "/home/pi/cesarops/sync"))

# Xenon (Waifu)
XENON_HOST = os.environ.get("XENON_HOST", _dotenv.get("XENON_HOST", "10.0.0.40"))
XENON_USER = os.environ.get("XENON_USER", _dotenv.get("XENON_USER", "cesarops"))
XENON_PASS = os.environ.get("XENON_PASS", _dotenv.get("XENON_PASS", ""))
XENON_KEY = os.environ.get("XENON_KEY", _dotenv.get("XENON_KEY", ""))
XENON_WORK = os.environ.get("XENON_WORK", _dotenv.get("XENON_WORK", "/home/cesarops/cesarops/sync"))


class SSHNode:
    """Represents a remote node (Pi or Xenon) with SSH access."""

    def __init__(self, host: str, user: str, password: str = "", key_path: str = ""):
        self.host = host
        self.user = user
        self.password = password
        self.key_path = key_path
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> bool:
        """Open SSH connection."""
        if not HAS_PARAMIKO:
            raise RuntimeError("paramiko not installed — pip install paramiko")

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if self.key_path and Path(self.key_path).exists():
            self._client.connect(
                self.host, username=self.user,
                key_filename=self.key_path, timeout=10,
            )
        elif self.password:
            self._client.connect(
                self.host, username=self.user,
                password=self.password, timeout=10,
            )
        else:
            raise RuntimeError(f"No auth method for {self.user}@{self.host}")
        return True

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def run(self, cmd: str, timeout: int = 3600) -> dict:
        """Run command, return {stdout, stderr, exit_code, duration_s}."""
        if not self._client:
            self.connect()

        start = time.time()
        stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        duration = time.time() - start

        return {
            "stdout": stdout.read().decode("utf-8", errors="replace"),
            "stderr": stderr.read().decode("utf-8", errors="replace"),
            "exit_code": exit_code,
            "duration_s": round(duration, 2),
        }

    def ping(self) -> bool:
        """Quick connectivity check."""
        try:
            if not self._client:
                self.connect()
            result = self.run("echo pong", timeout=5)
            return result["exit_code"] == 0 and "pong" in result["stdout"]
        except Exception:
            return False


# ── Task Definitions ─────────────────────────────────────────────────────────

def build_pi_slice_task(
    area_name: str,
    bbox: list,
    sources: list,
    tile_size: int = 1024,
    target_resolution: float = 10.0,
    mission_json: str = "",
) -> str:
    """Build the shell command for Pi to run: VRT stack → slice → route."""
    sources_str = " ".join(sources)
    cmd = (
        f"cd {PI_WORK} && "
        f"echo '[PI] Starting slice pipeline for {area_name}' && "
        f"mkdir -p tiles/cpu tiles/tpu tiles/gpu tiles/hybrid && "
        f"./slicer vrt "
        f"{sources_str} "
        f"--output tiles "
        f"--tile-size {tile_size} "
        f"--target-resolution {target_resolution}"
    )
    if mission_json:
        cmd += f" --mission {mission_json}"
    cmd += (
        f" && echo '[PI] Slicing complete — tiles staged in delegate folders' "
        f"&& ls -la tiles/*/ | tail -20"
    )
    return cmd


def build_xenon_process_task(
    tiles_dir: str = "",
    delegate: str = "",
) -> str:
    """Build the shell command for Xenon to process staged tiles."""
    work = XENON_WORK
    cmd = (
        f"cd {work} && "
        f"echo '[XENON] Starting tile processing'"
    )

    if delegate:
        # Only process specific delegate folder
        cmd += (
            f" && echo '[XENON] Processing {delegate} tiles...' "
            f"&& python cesarops_engine.py --tiles-dir tiles/{delegate} --delegate {delegate}"
        )
    else:
        # Process all delegate folders in order: TPU first (fastest), then GPU, then CPU
        cmd += (
            f" && for delegate in tpu gpu cpu hybrid; do "
            f"  count=$(ls tiles/$delegate/*.bin 2>/dev/null | wc -l); "
            f"  if [ $count -gt 0 ]; then "
            f"    echo '[XENON] Processing $delegate: $count tiles'; "
            f"    python cesarops_engine.py --tiles-dir tiles/$delegate --delegate $delegate; "
            f"  fi; "
            f"done"
        )

    cmd += f" && echo '[XENON] Processing complete'"
    return cmd


def build_xenon_tpu_health() -> str:
    """Check TPU server health on Xenon."""
    return (
        f"cd {XENON_WORK} && "
        f"curl -s http://localhost:5001/health 2>/dev/null || echo '{{\"status\": \"unreachable\"}}'"
    )


# ── High-level dispatcher ────────────────────────────────────────────────────

class TaskDispatcher:
    """Dispatches tasks to Pi and Xenon, collects status."""

    def __init__(self):
        self.pi = SSHNode(PI_HOST, PI_USER, PI_PASS, PI_KEY) if PI_PASS or PI_KEY else None
        self.xenon = SSHNode(XENON_HOST, XENON_USER, XENON_PASS, XENON_KEY) if XENON_PASS or XENON_KEY else None
        self.task_log = []

    def status(self) -> dict:
        """Ping all nodes, return connectivity status."""
        result = {"timestamp": datetime.now(timezone.utc).isoformat(), "nodes": {}}

        if self.pi:
            result["nodes"]["pi"] = {
                "host": PI_HOST,
                "online": self.pi.ping(),
                "work_dir": PI_WORK,
            }
        else:
            result["nodes"]["pi"] = {"host": PI_HOST, "online": False, "reason": "no credentials"}

        if self.xenon:
            result["nodes"]["xenon"] = {
                "host": XENON_HOST,
                "online": self.xenon.ping(),
                "work_dir": XENON_WORK,
            }
            # Also check TPU
            try:
                tpu_result = self.xenon.run(build_xenon_tpu_health(), timeout=10)
                result["nodes"]["xenon"]["tpu"] = json.loads(tpu_result["stdout"]) if tpu_result["exit_code"] == 0 else {"status": "error"}
            except Exception:
                result["nodes"]["xenon"]["tpu"] = {"status": "unreachable"}
        else:
            result["nodes"]["xenon"] = {"host": XENON_HOST, "online": False, "reason": "no credentials"}

        return result

    def task_pi_slice(self, area_name: str, bbox: list, sources: list,
                      tile_size: int = 1024, target_resolution: float = 10.0,
                      mission_json: str = "") -> dict:
        """Send slicing task to Pi."""
        if not self.pi:
            return {"error": "Pi SSH not configured"}

        cmd = build_pi_slice_task(area_name, bbox, sources, tile_size, target_resolution, mission_json)
        task_start = datetime.now(timezone.utc).isoformat()

        print(f"  📡 [PI] Running slice pipeline for {area_name}...")
        result = self.pi.run(cmd, timeout=7200)  # 2hr timeout for slicing
        result["task"] = "pi_slice"
        result["area"] = area_name
        result["started_at"] = task_start

        self.task_log.append(result)
        if result["exit_code"] == 0:
            print(f"  ✅ [PI] Slicing complete in {result['duration_s']}s")
        else:
            print(f"  ❌ [PI] Slicing failed (exit {result['exit_code']})")
            if result["stderr"]:
                print(f"     {result['stderr'][:500]}")

        return result

    def task_xenon_process(self, delegate: str = "") -> dict:
        """Send processing task to Xenon."""
        if not self.xenon:
            return {"error": "Xenon SSH not configured"}

        cmd = build_xenon_process_task(delegate=delegate)
        task_start = datetime.now(timezone.utc).isoformat()

        label = delegate if delegate else "all delegates"
        print(f"  📡 [XENON] Processing {label} tiles...")
        result = self.xenon.run(cmd, timeout=7200)
        result["task"] = "xenon_process"
        result["delegate"] = delegate or "all"
        result["started_at"] = task_start

        self.task_log.append(result)
        if result["exit_code"] == 0:
            print(f"  ✅ [XENON] Processing complete in {result['duration_s']}s")
        else:
            print(f"  ❌ [XENON] Processing failed (exit {result['exit_code']})")
            if result["stderr"]:
                print(f"     {result['stderr'][:500]}")

        return result

    def get_task_log(self) -> list:
        return self.task_log

    def close(self):
        if self.pi:
            self.pi.close()
        if self.xenon:
            self.xenon.close()
