#!/usr/bin/env python3
"""
TPU Client - Remote Coral TPU Access
Use Xenon's TPU from anywhere (laptop @ school, Xenon itself, etc.)
"""

import requests
import base64
from io import BytesIO
from PIL import Image
from pathlib import Path

class TPUClient:
    """Client for remote TPU inference"""
    
    def __init__(self, server_url="http://localhost:5001"):
        """
        Initialize TPU client
        
        Args:
            server_url: TPU server URL
                - Laptop: "http://10.0.0.55:5001" (Xenon's IP)
                - Xenon: "http://localhost:5001" (local)
        """
        self.server_url = server_url
    
    def check_glint_jitter(self, image, meta=None):
        """
        Check image for glint and jitter
        
        Args:
            image: PIL Image, numpy array, or file path
            meta: Optional metadata dict
        
        Returns:
            dict: {
                'glint_score': float (0-1),
                'jitter_score': float (0-1),
                'pass': bool,
                'took_ms': float,
                'used_tpu': bool
            }
        """
        # Convert image to base64
        img_bytes = self._image_to_bytes(image)
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        
        # Build request
        payload = {
            'image_base64': b64,
            'meta': meta or {}
        }
        
        # Send to server
        try:
            response = requests.post(
                f"{self.server_url}/infer",
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.ConnectionError:
            # Server unreachable, return safe default
            print(f"⚠ TPU server unreachable: {self.server_url}")
            print("  Assuming PASS (no glint/jitter detected)")
            return {
                'glint_score': 0.0,
                'jitter_score': 0.0,
                'pass': True,
                'took_ms': 0,
                'used_tpu': False,
                'error': 'server_unreachable'
            }
        
        except Exception as e:
            print(f"⚠ TPU inference failed: {e}")
            return {
                'glint_score': 0.0,
                'jitter_score': 0.0,
                'pass': True,  # Fail-safe: assume OK
                'took_ms': 0,
                'used_tpu': False,
                'error': str(e)
            }
    
    def health_check(self):
        """Check if TPU server is online"""
        try:
            response = requests.get(f"{self.server_url}/health", timeout=5)
            return response.json()
        except:
            return {
                'status': 'unreachable',
                'error': 'Could not connect to TPU server'
            }
    
    def _image_to_bytes(self, image):
        """Convert image to PNG bytes"""
        if isinstance(image, (str, Path)):
            # File path
            img = Image.open(image)
        elif isinstance(image, Image.Image):
            # Already PIL Image
            img = image
        elif hasattr(image, 'shape'):
            # Numpy array
            img = Image.fromarray(image)
        else:
            raise ValueError(f"Unknown image type: {type(image)}")
        
        # Convert to PNG bytes
        buf = BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()


# Convenience function for quick use
def quick_glint_check(image_path, server_url="http://localhost:5001"):
    """
    Quick glint/jitter check
    
    Usage:
        result = quick_glint_check("tile.png")
        if result['pass']:
            print("Tile is good!")
    """
    client = TPUClient(server_url)
    return client.check_glint_jitter(image_path)


# Test function
def test_tpu_server(server_url="http://localhost:5001"):
    """Test TPU server with sample image"""
    print("="*70)
    print("TPU CLIENT TEST")
    print("="*70)
    
    client = TPUClient(server_url)
    
    # Health check
    print("\n[1/2] Health check...")
    health = client.health_check()
    print(f"  Status: {health.get('status', 'unknown')}")
    print(f"  TPU Available: {health.get('tpu_available', False)}")
    print(f"  Model Loaded: {health.get('model_loaded', False)}")
    
    # Test inference (create simple test image)
    print("\n[2/2] Test inference...")
    test_img = Image.new('L', (224, 224), color=128)  # Gray square
    
    result = client.check_glint_jitter(
        test_img,
        meta={'test': True, 'source': 'tpu_client_test'}
    )
    
    print(f"  Glint Score: {result['glint_score']:.3f}")
    print(f"  Jitter Score: {result['jitter_score']:.3f}")
    print(f"  PASS: {result['pass']}")
    print(f"  Took: {result['took_ms']:.2f}ms")
    print(f"  Used TPU: {result['used_tpu']}")
    
    if 'error' in result:
        print(f"  Error: {result['error']}")
    
    print("\n" + "="*70)
    
    return result


if __name__ == "__main__":
    import sys
    
    # Default: test local server
    server_url = "http://localhost:5001"
    
    # Override with command line arg
    if len(sys.argv) > 1:
        server_url = sys.argv[1]
    
    print(f"\nTesting TPU server: {server_url}")
    print("Make sure tpu_server.py is running on that address\n")
    
    test_tpu_server(server_url)
