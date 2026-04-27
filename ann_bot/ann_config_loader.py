import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def load():
    config_path = Path(__file__).parent.parent / "configs" / "ann_config.json"
    with open(config_path) as f:
        cfg = json.load(f)
    cfg["access_token"] = os.getenv("ACCESS_TOKEN", "")
    return cfg

cfg = load()