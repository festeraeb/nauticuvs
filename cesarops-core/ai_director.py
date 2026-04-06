#!/usr/bin/env python3
"""
AI DIRECTOR - Tool Picker & Parameter Adjuster

This is "little brother" - the director that:
1. Takes user requests (natural language or UI commands)
2. Picks the right tools
3. Sets bounding boxes
4. Adjusts parameters (sensitivity, thresholds)
5. Calls the tools
6. Returns consolidated results

Can use LOCAL LLM API (Kobold/Ollama) for reasoning!

Usage:
    python ai_director.py --request "Search for Gilcher near Fox Islands"
    python ai_director.py --api http://localhost:5001 --request "Find triple locks at Beaver Islands"
    python ai_director.py --bbox 45.8,-84.6,46.0,-84.4 --tools thermal,optical --sensitivity 1.5
"""

import argparse
import json
import subprocess
import sys
import requests
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

# Local LLM API endpoints
LLM_APIS = {
    'kobold': 'http://localhost:5001/api/v1/generate',
    'ollama': 'http://localhost:11434/api/generate',
    'lmstudio': 'http://localhost:1234/v1/chat/completions',
}

# Bounding box presets for known wreck locations
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

# Available tools with metadata
AVAILABLE_TOOLS = {
    'thermal': {
        'script': 'lake_michigan_scan.py',
        'description': 'Thermal cold-sink detection (Landsat B10/B11)',
        'usage': 'python lake_michigan_scan.py --sensitivity 1.5',
        'default_threshold': 2.5,
        'best_for': ['steel masses', 'large vessels', 'engine blocks'],
        'data_source': 'Landsat-8/9 B10/B11 thermal bands',
        'resolution': '100m/pixel',
        'depth_limit': 'Up to 300ft depth',
    },
    'optical': {
        'script': 'lake_michigan_scan.py',
        'description': 'Optical glint detection (Sentinel-2 B04/B08)',
        'usage': 'python lake_michigan_scan.py --sensitivity 1.5',
        'default_threshold': 2.0,
        'best_for': ['aluminum', 'aircraft', 'surface debris'],
        'data_source': 'Sentinel-2 B04/B08 optical bands',
        'resolution': '10m/pixel',
        'depth_limit': 'Surface/near-surface only',
    },
    'sar': {
        'script': 'lake_michigan_scan.py',
        'description': 'SAR VV/VH ratio (Sentinel-1)',
        'usage': 'Requires Sentinel-1 SAR data download',
        'default_threshold': 2.0,
        'best_for': ['heavy steel', 'dense masses', 'submerged structures'],
        'data_source': 'Sentinel-1 VV/VH polarization',
        'resolution': '20m/pixel',
        'depth_limit': 'Penetrates water column',
    },
    'triple_lock': {
        'script': 'triple_lock_fusion.py',
        'description': 'Multi-sensor fusion (thermal + optical + SAR)',
        'usage': 'python triple_lock_fusion.py --sensitivity 2.5',
        'default_threshold': 2.5,
        'best_for': ['high confidence targets', 'verification'],
        'data_source': 'All sensors combined',
        'resolution': 'Varies by sensor',
        'depth_limit': 'All depths',
    },
    'download_fox_beaver': {
        'script': 'dual_scan_downloader.py',
        'description': 'Download Fox/Beaver Islands satellite data',
        'usage': 'python dual_scan_downloader.py --area fox_beaver --years 2012-2025',
        'default_threshold': None,
        'best_for': ['Data acquisition for Gilcher, Parnell searches'],
        'data_source': 'USGS EarthExplorer, Sentinel Hub',
        'resolution': 'Varies',
        'depth_limit': 'N/A',
        'bbox': {'lat_min': 45.60, 'lat_max': 46.10, 'lon_min': -85.60, 'lon_max': -84.40},
    },
    'download_full_lake': {
        'script': 'dual_scan_downloader.py',
        'description': 'Download full Lake Michigan satellite data (5-year low water)',
        'usage': 'python dual_scan_downloader.py --area full_lake --years 2012,2013,2019,2020,2021,2024,2025',
        'default_threshold': None,
        'best_for': ['Complete basin coverage', 'Multi-year analysis'],
        'data_source': 'Landsat-8/9, Sentinel-1/2, SWOT, ICESat-2',
        'resolution': '10-100m/pixel',
        'depth_limit': 'N/A',
        'bbox': {'lat_min': 41.60, 'lat_max': 46.10, 'lon_min': -88.10, 'lon_max': -84.70},
    },
    'swot': {
        'script': 'dual_scan_downloader.py',
        'description': 'SWOT Ka-band displacement',
        'usage': 'python dual_scan_downloader.py --sensor swot',
        'default_threshold': 1.5,
        'best_for': ['large displacement', 'hull shapes'],
        'data_source': 'SWOT L2 Ka-band',
        'resolution': '100m/pixel',
        'depth_limit': 'Surface displacement',
    },
    'atl13': {
        'script': 'dual_scan_downloader.py',
        'description': 'ICESat-2 ATL13 bathymetry',
        'usage': 'python dual_scan_downloader.py --sensor atl13',
        'default_threshold': 2.0,
        'best_for': ['depth verification', 'seafloor mapping'],
        'data_source': 'ICESat-2 ATL13',
        'resolution': '17m along-track',
        'depth_limit': 'Up to 100ft (clear water)',
    },
}


class AIDirector:
    """AI Director - picks tools and calls them"""

    def __init__(self):
        self.results = []
        self.config = {
            'bbox': None,
            'tools': [],
            'sensitivity': 1.5,
            'thresholds': {},
        }

    def parse_request(self, request: str) -> Dict:
        """
        Parse natural language request into tool configuration.
        In production, this would call Kobold/Ollama/LLM.
        For now, uses keyword matching.
        """
        request_lower = request.lower()

        # Detect bounding box from keywords
        bbox_name = None
        for name, bbox in BOUNDING_BOXES.items():
            if any(keyword in request_lower for keyword in name.lower().split('_')):
                bbox_name = name
                break
            if any(target.lower() in request_lower for target in bbox.get('targets', [])):
                bbox_name = name
                break

        # Detect tools from keywords
        tools = []
        if any(word in request_lower for word in ['thermal', 'cold', 'heat', 'sink']):
            tools.append('thermal')
        if any(word in request_lower for word in ['optical', 'glint', 'aluminum', 'aircraft']):
            tools.append('optical')
        if any(word in request_lower for word in ['sar', 'vv', 'vh', 'radar']):
            tools.append('sar')
        if any(word in request_lower for word in ['fusion', 'triple', 'lock', 'verify']):
            tools.append('triple_lock')
        if any(word in request_lower for word in ['swot', 'displacement']):
            tools.append('swot')
        if any(word in request_lower for word in ['icesat', 'atl13', 'bathymetry']):
            tools.append('atl13')

        # Default to all sensors if none specified
        if not tools:
            tools = ['thermal', 'optical']

        # Detect sensitivity from keywords
        sensitivity = 1.5  # Default aggressive
        if any(word in request_lower for word in ['conservative', 'strict', 'high confidence']):
            sensitivity = 3.0
        elif any(word in request_lower for word in ['aggressive', 'sensitive', 'all']):
            sensitivity = 1.0

        return {
            'bbox_name': bbox_name,
            'tools': tools,
            'sensitivity': sensitivity,
        }

    def set_bounding_box(self, bbox_name: str = None, lat_min=None, lat_max=None, lon_min=None, lon_max=None):
        """Set search area bounding box"""
        if bbox_name and bbox_name in BOUNDING_BOXES:
            self.config['bbox'] = BOUNDING_BOXES[bbox_name]
            print(f"✓ Bounding box: {self.config['bbox']['name']}")
        elif all(v is not None for v in [lat_min, lat_max, lon_min, lon_max]):
            self.config['bbox'] = {
                'name': 'Custom',
                'lat_min': lat_min,
                'lat_max': lat_max,
                'lon_min': lon_min,
                'lon_max': lon_max,
            }
            print(f"✓ Custom bounding box set")
        else:
            # Default to Lake Michigan South
            self.config['bbox'] = BOUNDING_BOXES['lake_michigan_south']
            print(f"✓ Default bounding box: {self.config['bbox']['name']}")

    def set_tools(self, tools: List[str]):
        """Select which tools to run"""
        valid_tools = [t for t in tools if t in AVAILABLE_TOOLS]
        self.config['tools'] = valid_tools
        print(f"✓ Tools selected: {', '.join(valid_tools)}")
        
        # Auto-set bounding box if tool has one
        for tool in valid_tools:
            if AVAILABLE_TOOLS[tool].get('bbox'):
                bbox = AVAILABLE_TOOLS[tool]['bbox']
                self.config['bbox'] = {
                    'name': tool,
                    **bbox
                }
                print(f"✓ Bounding box: {bbox['lat_min']:.2f}°N to {bbox['lat_max']:.2f}°N")

    def set_parameters(self, sensitivity: float = None, thresholds: Dict = None):
        """Adjust tool parameters"""
        if sensitivity is not None:
            self.config['sensitivity'] = sensitivity
        if thresholds:
            self.config['thresholds'] = thresholds

        print(f"✓ Sensitivity: {self.config['sensitivity']}")
        if self.config['thresholds']:
            print(f"✓ Thresholds: {self.config['thresholds']}")

    def run_tool(self, tool_name: str) -> Dict:
        """Execute a single tool with current config"""
        if tool_name not in AVAILABLE_TOOLS:
            return {'error': f'Unknown tool: {tool_name}'}

        tool = AVAILABLE_TOOLS[tool_name]
        script_path = Path(__file__).parent / tool['script']

        if not script_path.exists():
            return {'error': f'Script not found: {script_path}'}

        print(f"\n{'='*80}")
        print(f"RUNNING: {tool['description']}")
        print(f"{'='*80}")

        # Build command
        cmd = [sys.executable, str(script_path)]

        # Add parameters
        if '--sensitivity' in tool.get('description', ''):
            cmd.extend(['--sensitivity', str(self.config['sensitivity'])])

        # Add bounding box if tool supports it
        if self.config['bbox']:
            bbox = self.config['bbox']
            # Some tools may need bbox as args
            # cmd.extend(['--bbox', f"{bbox['lat_min']},{bbox['lon_min']},{bbox['lat_max']},{bbox['lon_max']}"])

        print(f"Command: {' '.join(cmd)}")

        # Execute
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
            )

            return {
                'tool': tool_name,
                'success': result.returncode == 0,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                'tool': tool_name,
                'success': False,
                'error': 'Tool execution timed out (10 min)',
            }
        except Exception as e:
            return {
                'tool': tool_name,
                'success': False,
                'error': str(e),
            }

    def execute(self) -> List[Dict]:
        """Run all selected tools"""
        print(f"\n{'='*80}")
        print(f"AI DIRECTOR - EXECUTING {len(self.config['tools'])} TOOLS")
        print(f"{'='*80}")
        print(f"Bounding box: {self.config['bbox']['name']}")
        print(f"Sensitivity: {self.config['sensitivity']}")
        print(f"Tools: {', '.join(self.config['tools'])}")

        results = []

        for tool_name in self.config['tools']:
            print(f"\n[{len(results)+1}/{len(self.config['tools'])}] Running {tool_name}...")
            result = self.run_tool(tool_name)
            results.append(result)

            if result.get('success'):
                print(f"✓ {tool_name} completed successfully")
            else:
                print(f"✗ {tool_name} failed: {result.get('error', result.get('stderr', 'Unknown error'))}")

        self.results = results
        return results

    def summarize(self) -> str:
        """Generate summary of all tool results"""
        summary = []
        summary.append("\n" + "="*80)
        summary.append("AI DIRECTOR - EXECUTION SUMMARY")
        summary.append("="*80)

        for result in self.results:
            tool = result.get('tool', 'Unknown')
            status = "✓ SUCCESS" if result.get('success') else "✗ FAILED"
            summary.append(f"\n{tool}: {status}")

            if result.get('stdout'):
                # Extract key info from output
                for line in result['stdout'].split('\n'):
                    if 'detections' in line.lower() or 'anomalies' in line.lower() or 'complete' in line.lower():
                        summary.append(f"  {line.strip()}")

            if result.get('error'):
                summary.append(f"  Error: {result['error']}")

        return '\n'.join(summary)


def main():
    parser = argparse.ArgumentParser(description='AI Director - Tool Picker & Executor')

    # Natural language request
    parser.add_argument('--request', '-r', type=str, help='Natural language request (e.g., "Search for Gilcher near Fox Islands")')

    # Manual configuration
    parser.add_argument('--bbox', type=str, help='Bounding box: lat_min,lat_max,lon_min,lon_max or preset name')
    parser.add_argument('--tools', type=str, help='Comma-separated list of tools: thermal,optical,sar')
    parser.add_argument('--sensitivity', type=float, default=1.5, help='Sensitivity (1.0=aggressive, 3.0=conservative)')

    # List available tools
    parser.add_argument('--list-tools', '-l', action='store_true', help='List all available tools with metadata')

    # Execute
    parser.add_argument('--execute', '-x', action='store_true', help='Execute tools immediately')
    parser.add_argument('--output', '-o', type=str, help='Save results to JSON file')

    args = parser.parse_args()

    # List tools if requested
    if args.list_tools:
        print("\n" + "="*120)
        print("AVAILABLE TOOLS - CESAROPS TOOLKIT")
        print("="*120)
        
        for tool_id, tool in AVAILABLE_TOOLS.items():
            print(f"\n {tool_id.upper()}")
            print(f"   Description: {tool['description']}")
            print(f"   Usage: {tool['usage']}")
            if tool.get('data_source'):
                print(f"   Data Source: {tool['data_source']}")
            if tool.get('resolution'):
                print(f"   Resolution: {tool['resolution']}")
            if tool.get('best_for'):
                print(f"   Best For: {', '.join(tool['best_for'])}")
            if tool.get('bbox'):
                bbox = tool['bbox']
                print(f"   Coverage: {bbox['lat_min']:.2f}°N to {bbox['lat_max']:.2f}°N, {bbox['lon_min']:.2f}°W to {bbox['lon_max']:.2f}°W")
        
        print("\n" + "="*120)
        print("EXAMPLE COMMANDS:")
        print("="*120)
        print('  python ai_director.py --request "Search for Gilcher near Fox Islands" --execute')
        print('  python ai_director.py --tools download_fox_beaver --execute')
        print('  python ai_director.py --bbox 45.8,-84.6,46.0,-84.4 --tools thermal,optical --sensitivity 1.5')
        print('  python ai_director.py --list-tools')
        print("="*120)
        return

    # Initialize director
    director = AIDirector()

    # Parse request if provided
    if args.request:
        print(f"Parsing request: {args.request}")
        parsed = director.parse_request(args.request)

        if parsed['bbox_name']:
            director.set_bounding_box(parsed['bbox_name'])
        director.set_tools(parsed['tools'])
        director.set_parameters(sensitivity=parsed['sensitivity'])

    # Override with manual config
    if args.bbox:
        if args.bbox in BOUNDING_BOXES:
            director.set_bounding_box(args.bbox)
        else:
            try:
                lat_min, lon_min, lat_max, lon_max = map(float, args.bbox.split(','))
                director.set_bounding_box(lat_min=lat_min, lat_max=lat_max, lon_min=lon_min, lon_max=lon_max)
            except:
                print(f"Invalid bbox format: {args.bbox}")

    if args.tools:
        director.set_tools(args.tools.split(','))

    if args.sensitivity:
        director.set_parameters(sensitivity=args.sensitivity)

    # Execute if requested
    if args.execute or not args.request:
        results = director.execute()
        print(director.summarize())

        # Save results
        if args.output:
            output_path = Path(args.output)
            with open(output_path, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'config': director.config,
                    'results': results,
                    'summary': director.summarize(),
                }, f, indent=2)
            print(f"\n✓ Results saved to: {output_path}")


if __name__ == '__main__':
    main()
