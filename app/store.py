"""JSON document store for generated LLM results; selected because assignment permits a justified JSON store."""
import json, os, threading
from pathlib import Path
from datetime import datetime, timezone

STORE_PATH = Path(os.getenv("GENERATION_STORE", "./data/generations.json"))
_LOCK = threading.Lock()

def _read():
    if not STORE_PATH.exists(): return {}
    try: return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError): return {}

def save_generation(record: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        data = _read(); data[record["generation_id"]] = record
        temp = STORE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(STORE_PATH)

def get_generation(generation_id: str):
    with _LOCK: return _read().get(generation_id)

def all_generations():
    with _LOCK: return _read()
