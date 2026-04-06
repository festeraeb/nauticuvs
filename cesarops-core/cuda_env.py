import os
from pathlib import Path

# Prioritized CUDA install dirs (recent first) - can be extended
CUDA_CANDIDATES = [
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2",
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.0",
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.2",
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8",
]


def configure_cuda_environment():
    """Set CUDA environment variables and PATH in an idempotent way."""
    cuda_path = os.environ.get('CUDA_PATH', '').strip()

    if cuda_path and Path(cuda_path).exists():
        selected = cuda_path
    else:
        selected = None
        for candidate in CUDA_CANDIDATES:
            if Path(candidate).exists():
                selected = candidate
                break

    if selected is None:
        raise FileNotFoundError(
            'CUDA toolkit not found. Set CUDA_PATH to a valid CUDA installation directory ' 
            'or install CUDA 13.2/13.0/12.2/11.8.'
        )

    cuda_bin = str(Path(selected) / 'bin')
    os.environ['CUDA_PATH'] = selected
    os.environ['PATH'] = cuda_bin + os.pathsep + os.environ.get('PATH', '')

    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(cuda_bin)
        except Exception:
            pass

    return {
        'cuda_path': selected,
        'cuda_bin': cuda_bin,
    }
