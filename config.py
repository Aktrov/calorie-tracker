"""Config loader. config.json is optional — sensible defaults apply when it is
absent. It is gitignored (the assistant block can name a local CLI); a
config.example.json ships as the template."""
import json
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(__file__).parent / "config.json"

_DEFAULTS = {
    "timezone": "Europe/Berlin",
    "openfoodfacts": {
        "enabled": True,
        "timeout_seconds": 5,
        "user_agent": "calorie-tracker/0.1 (personal homelab app)",
    },
    "assistant": {
        # Nutrition lookup panel. Shells out to a local Claude CLI — no API key.
        "enabled": False,
        "command": "claude",
        "model": "haiku",
        "timeout_seconds": 60,
    },
}

_MERGE_NESTED = ("openfoodfacts", "assistant")

_config = None


def load():
    global _config
    if _config is None:
        cfg = dict(_DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                user = json.loads(CONFIG_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                user = {}
            cfg.update({k: v for k, v in user.items() if k not in _MERGE_NESTED})
            for key in _MERGE_NESTED:
                if isinstance(user.get(key), dict):
                    cfg[key] = {**_DEFAULTS[key], **user[key]}
        _config = cfg
    return _config


def timezone():
    try:
        return ZoneInfo(load().get("timezone", "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def off():
    return load()["openfoodfacts"]


def assistant():
    return load()["assistant"]
