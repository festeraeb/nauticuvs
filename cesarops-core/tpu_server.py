"""
TPU Inference Server — CESAROPS Coral Edge TPU Gateway

Minimal Flask server that accepts POST /infer with JSON:
  { "image_base64": "...PNG base64...", "meta": { "crs": "EPSG:4326", ... } }

Returns JSON detections:
  { "detections": [ {"row":.., "col":.., "score":..}, ... ] }

Runs on Xenon (with local TPU) or laptop (CPU stub fallback).

To run:
    python -m pip install Flask Pillow numpy
    python tpu_server.py

When Edge TPU is available, replace `run_inference` with pycoral/tflite-runtime.
"""
from flask import Flask, request, jsonify
import base64
from io import BytesIO
from PIL import Image
import numpy as np
import time
import sys

app = Flask(__name__)

# ── TPU detection ─────────────────────────────────────────────────────────────

TPU_AVAILABLE = False
try:
    import tflite_runtime.interpreter as tflite
    TFLITE_RUNTIME = True
except ImportError:
    TFLITE_RUNTIME = False

try:
    from pycoral.utils import edgetpu
    from pycoral.adapters import common
    TPU_AVAILABLE = True
except ImportError:
    pass


def _get_interpreter(model_path="models/glint_jitter_edgetpu.tflite"):
    """Load Edge TPU or CPU interpreter."""
    if TPU_AVAILABLE and TFLITE_RUNTIME:
        return tflite.Interpreter(
            model_path=model_path,
            experimental_delegates=[
                tflite.load_delegate("edgetpu.dll" if sys.platform == "win32"
                                     else "libedgetpu.so.1.0")
            ],
        )
    elif TFLITE_RUNTIME:
        return tflite.Interpreter(model_path=model_path.replace("_edgetpu", "_cpu"))
    else:
        return None


# Lazy-load interpreter
_interpreter = None


def get_interpreter():
    global _interpreter
    if _interpreter is None:
        _interpreter = _get_interpreter()
    return _interpreter


def run_inference(image: Image.Image, meta: dict):
    """
    Stub inference: locate brightest pixels as dummy glint/jitter events.

    Replace this body with real TFLite/Edge TPU code when model is available:

        interpreter = get_interpreter()
        interpreter.allocate_tensors()
        input_idx = interpreter.get_input_details()[0]["index"]
        output_idx = interpreter.get_output_details()[0]["index"]
        arr = np.array(image.convert('L'), dtype=np.uint8)
        interpreter.set_tensor(input_idx, arr.reshape(1, *arr.shape, 1))
        interpreter.invoke()
        output = interpreter.get_tensor(output_idx)
        return parse_output(output)
    """
    arr = np.array(image.convert('L'), dtype=np.float32)
    # Simple local maxima above threshold (brightest = specular glint candidates)
    thresh = np.nanpercentile(arr, 99.5)
    ys, xs = np.where(arr >= thresh)
    detections = []
    for y, x in zip(ys, xs):
        detections.append({
            'row': int(y),
            'col': int(x),
            'score': float(arr[y, x]) / 255.0,
        })
    # Limit
    detections = detections[:100]
    return detections


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/infer', methods=['POST'])
def infer():
    start = time.time()
    data = request.get_json()
    if not data or 'image_base64' not in data:
        return jsonify({'error': 'missing image_base64'}), 400

    b64 = data['image_base64']
    meta = data.get('meta', {})
    try:
        img_bytes = base64.b64decode(b64)
        img = Image.open(BytesIO(img_bytes))
    except Exception as e:
        return jsonify({'error': f'failed to decode image: {e}'}), 400

    detections = run_inference(img, meta)
    elapsed = time.time() - start
    resp = {
        'detections': detections,
        'meta': meta,
        'took_s': round(elapsed, 4),
        'used_tpu': TPU_AVAILABLE,
    }
    return jsonify(resp)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'tpu_available': TPU_AVAILABLE,
        'tflite_runtime': TFLITE_RUNTIME,
        'model_loaded': get_interpreter() is not None,
    })


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=5001)
    p.add_argument('--host', default='0.0.0.0')
    args = p.parse_args()

    print(f"TPU Server — TPU: {TPU_AVAILABLE}, TFLite: {TFLITE_RUNTIME}")
    app.run(host=args.host, port=args.port)
