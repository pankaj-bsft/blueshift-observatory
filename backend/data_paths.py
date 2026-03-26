import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv('BS_DATA_DIR', PROJECT_ROOT / 'data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def data_path(filename: str) -> str:
    return str(DATA_DIR / filename)
