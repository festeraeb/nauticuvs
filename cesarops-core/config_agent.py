#!/usr/bin/env python3
"""
CESAROPS Configuration Agent
Interactive helper that asks questions and configures the pipeline
"""

import json
from pathlib import Path
from typing import Dict, Any

class ConfigAgent:
    """Interactive agent that helps configure the pipeline"""
    
    def __init__(self):
        self.config = {}
        
    def ask(self, question: str, default: Any = None, options: list = None) -> Any:
        """Ask user a question with optional default and choices"""
        if options:
            print(f"\n{question}")
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
            
            while True:
                choice = input(f"Choice [1-{len(options)}]: ").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(options):
                    return options[int(choice) - 1]
                print("Invalid choice, try again")
        else:
            prompt = f"\n{question}"
            if default is not None:
                prompt += f" [{default}]"
            prompt += ": "
            
            answer = input(prompt).strip()
            return answer if answer else default
    
    def ask_yes_no(self, question: str, default: bool = True) -> bool:
        """Ask yes/no question"""
        default_str = "Y/n" if default else "y/N"
        answer = input(f"\n{question} [{default_str}]: ").strip().lower()
        
        if not answer:
            return default
        return answer in ['y', 'yes', 'true', '1']
    
    def ask_number(self, question: str, default: float, min_val: float = None, max_val: float = None) -> float:
        """Ask for numeric input with validation"""
        while True:
            answer = input(f"\n{question} [{default}]: ").strip()
            
            if not answer:
                return default
            
            try:
                value = float(answer)
                if min_val is not None and value < min_val:
                    print(f"Value must be >= {min_val}")
                    continue
                if max_val is not None and value > max_val:
                    print(f"Value must be <= {max_val}")
                    continue
                return value
            except ValueError:
                print("Please enter a valid number")
    
    def configure_data_source(self):
        """Configure where to find TIFF files"""
        print("\n" + "=" * 80)
        print("DATA SOURCE CONFIGURATION")
        print("=" * 80)
        
        print("\nWhere are your satellite TIFF files located?")
        
        use_default = self.ask_yes_no(
            "Use default location (wreckhunter2000/data/cache/census_raw)?",
            default=True
        )
        
        if use_default:
            self.config['data_dir'] = r"C:\Users\thomf\programming\wreckhunter2000\data\cache\census_raw"
        else:
            custom_path = self.ask("Enter full path to TIFF directory")
            self.config['data_dir'] = custom_path
        
        print(f"\n✓ Data directory: {self.config['data_dir']}")
    
    def configure_sensors(self):
        """Configure which sensors to use"""
        print("\n" + "=" * 80)
        print("SENSOR CONFIGURATION")
        print("=" * 80)
        
        print("\nWhich sensors do you want to use?")
        
        use_thermal = self.ask_yes_no("Use thermal bands (B10/B11)?", default=True)
        use_optical = self.ask_yes_no("Use optical bands (B04/B08)?", default=True)
        use_sar = self.ask_yes_no("Use SAR data (if available)?", default=False)
        
        self.config['sensors'] = {
            'thermal': use_thermal,
            'optical': use_optical,
            'sar': use_sar
        }
        
        print("\n✓ Sensors configured:")
        for sensor, enabled in self.config['sensors'].items():
            status = "✓ Enabled" if enabled else "✗ Disabled"
            print(f"  {sensor.upper()}: {status}")
    
    def configure_detection(self):
        """Configure detection parameters"""
        print("\n" + "=" * 80)
        print("DETECTION PARAMETERS")
        print("=" * 80)
        
        print("\nHow sensitive should the detection be?")
        
        sensitivity = self.ask(
            "Sensitivity level",
            options=["Low (fewer false positives)", "Medium (balanced)", "High (catch everything)"]
        )
        
        # Map sensitivity to threshold
        threshold_map = {
            "Low (fewer false positives)": 3.0,
            "Medium (balanced)": 2.5,
            "High (catch everything)": 2.0
        }
        
        self.config['threshold'] = threshold_map[sensitivity]
        
        print(f"\n✓ Z-score threshold: {self.config['threshold']}")
        
        # Ask about minimum confidence
        print("\nWhat minimum confidence score should detections have?")
        min_confidence = self.ask_number(
            "Minimum confidence (0.0 to 1.0)",
            default=0.5,
            min_val=0.0,
            max_val=1.0
        )
        
        self.config['min_confidence'] = min_confidence
        print(f"✓ Minimum confidence: {min_confidence}")
    
    def configure_target_type(self):
        """Configure what type of target to look for"""
        print("\n" + "=" * 80)
        print("TARGET TYPE")
        print("=" * 80)
        
        print("\nWhat are you searching for?")
        
        target = self.ask(
            "Target type",
            options=[
                "Aircraft (aluminum, thermal sink)",
                "Shipwreck (steel, thermal mass)",
                "Unknown (detect all anomalies)",
                "Custom (I'll set parameters manually)"
            ]
        )
        
        # Set detection parameters based on target
        if "Aircraft" in target:
            self.config['target_type'] = 'aircraft'
            self.config['aluminum_weight'] = 2.0
            self.config['thermal_weight'] = 1.0
            print("\n✓ Optimized for aluminum aircraft detection")
            print("  - High weight on B08/B04 ratio (aluminum signature)")
            print("  - Thermal sink detection enabled")
        
        elif "Shipwreck" in target:
            self.config['target_type'] = 'shipwreck'
            self.config['aluminum_weight'] = 0.5
            self.config['thermal_weight'] = 2.0
            print("\n✓ Optimized for steel shipwreck detection")
            print("  - High weight on thermal mass")
            print("  - Steel signature detection enabled")
        
        elif "Unknown" in target:
            self.config['target_type'] = 'unknown'
            self.config['aluminum_weight'] = 1.0
            self.config['thermal_weight'] = 1.0
            print("\n✓ Balanced detection for all anomaly types")
        
        else:  # Custom
            self.config['target_type'] = 'custom'
            print("\nCustom parameter configuration:")
            
            self.config['aluminum_weight'] = self.ask_number(
                "Aluminum signature weight (0.0 to 3.0)",
                default=1.0,
                min_val=0.0,
                max_val=3.0
            )
            
            self.config['thermal_weight'] = self.ask_number(
                "Thermal signature weight (0.0 to 3.0)",
                default=1.0,
                min_val=0.0,
                max_val=3.0
            )
            
            print(f"\n✓ Custom weights: Aluminum={self.config['aluminum_weight']}, Thermal={self.config['thermal_weight']}")
    
    def configure_area(self):
        """Configure search area"""
        print("\n" + "=" * 80)
        print("SEARCH AREA")
        print("=" * 80)
        
        print("\nDo you want to limit the search area?")
        
        limit_area = self.ask_yes_no("Limit to specific geographic area?", default=False)
        
        if limit_area:
            print("\nEnter bounding box coordinates:")
            
            min_lat = self.ask_number("Minimum latitude", default=42.0, min_val=-90, max_val=90)
            max_lat = self.ask_number("Maximum latitude", default=43.0, min_val=-90, max_val=90)
            min_lon = self.ask_number("Minimum longitude", default=-88.0, min_val=-180, max_val=180)
            max_lon = self.ask_number("Maximum longitude", default=-87.0, min_val=-180, max_val=180)
            
            self.config['area'] = {
                'min_lat': min_lat,
                'max_lat': max_lat,
                'min_lon': min_lon,
                'max_lon': max_lon
            }
            
            print(f"\n✓ Search area: {min_lat}°N to {max_lat}°N, {min_lon}°E to {max_lon}°E")
        else:
            self.config['area'] = None
            print("\n✓ No area restriction - will process all available TIFFs")
    
    def configure_gpu(self):
        """Configure GPU settings"""
        print("\n" + "=" * 80)
        print("GPU CONFIGURATION")
        print("=" * 80)
        
        require_gpu = self.ask_yes_no(
            "Require GPU (fail if GPU not available)?",
            default=True
        )
        
        self.config['require_gpu'] = require_gpu
        
        if require_gpu:
            print("\n✓ GPU required - will fail if Quadro M2200 not detected")
        else:
            print("\n✓ GPU optional - will fall back to CPU if needed")
        
        # Batch size
        print("\nHow many TIFFs should be processed in parallel?")
        batch_size = self.ask_number(
            "Batch size (1 = sequential, higher = more memory)",
            default=1,
            min_val=1,
            max_val=10
        )
        
        self.config['batch_size'] = int(batch_size)
        print(f"✓ Batch size: {self.config['batch_size']}")
    
    def configure_output(self):
        """Configure output settings"""
        print("\n" + "=" * 80)
        print("OUTPUT CONFIGURATION")
        print("=" * 80)
        
        print("\nWhere should results be saved?")
        
        use_default = self.ask_yes_no(
            "Use default output directory (outputs/)?",
            default=True
        )
        
        if use_default:
            self.config['output_dir'] = "outputs"
        else:
            custom_path = self.ask("Enter output directory path")
            self.config['output_dir'] = custom_path
        
        print(f"\n✓ Output directory: {self.config['output_dir']}")
        
        # Output formats
        print("\nWhich output formats do you want?")
        
        export_json = self.ask_yes_no("Export JSON results?", default=True)
        export_kml = self.ask_yes_no("Export KML for Google Earth?", default=True)
        export_csv = self.ask_yes_no("Export CSV for spreadsheet?", default=False)
        
        self.config['output_formats'] = {
            'json': export_json,
            'kml': export_kml,
            'csv': export_csv
        }
        
        print("\n✓ Output formats:")
        for fmt, enabled in self.config['output_formats'].items():
            status = "✓ Enabled" if enabled else "✗ Disabled"
            print(f"  {fmt.upper()}: {status}")
    
    def run_interactive_config(self) -> Dict[str, Any]:
        """Run full interactive configuration"""
        print("=" * 80)
        print("CESAROPS CONFIGURATION AGENT")
        print("=" * 80)
        print("\nI'll help you configure the pipeline by asking a few questions.")
        print("Press Enter to accept default values shown in [brackets].")
        
        input("\nPress Enter to start...")
        
        # Run configuration steps
        self.configure_data_source()
        self.configure_sensors()
        self.configure_target_type()
        self.configure_detection()
        self.configure_area()
        self.configure_gpu()
        self.configure_output()
        
        # Summary
        print("\n" + "=" * 80)
        print("CONFIGURATION SUMMARY")
        print("=" * 80)
        
        print(f"\nData Source: {self.config['data_dir']}")
        print(f"Target Type: {self.config['target_type']}")
        print(f"Detection Threshold: {self.config['threshold']}")
        print(f"Min Confidence: {self.config['min_confidence']}")
        print(f"GPU Required: {self.config['require_gpu']}")
        print(f"Output Directory: {self.config['output_dir']}")
        
        # Confirm
        print("\n" + "=" * 80)
        
        if self.ask_yes_no("Does this configuration look correct?", default=True):
            # Save config
            config_path = Path("pipeline_config.json")
            with open(config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
            
            print(f"\n✓ Configuration saved to: {config_path}")
            print("\nRun the pipeline with:")
            print("  python run_configured_pipeline.py")
            
            return self.config
        else:
            print("\nConfiguration cancelled. Run again to reconfigure.")
            return None

def main():
    agent = ConfigAgent()
    config = agent.run_interactive_config()
    
    if config:
        print("\n" + "=" * 80)
        print("READY TO RUN")
        print("=" * 80)
        print("\nYour pipeline is configured and ready.")
        print("\nNext steps:")
        print("  1. Review: pipeline_config.json")
        print("  2. Run: python run_configured_pipeline.py")
        print("  3. Or manually adjust config and run again")

if __name__ == "__main__":
    main()
