import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def load_config():
    # minimal config loader for now
    return {
        "robot_id": os.environ.get("ROBOT_ID", "koda-01"),
        "backend_url": os.environ.get("BACKEND_URL", "http://localhost:8000/api/events"),
        "log_level": os.environ.get("LOG_LEVEL", "INFO"),
    }
