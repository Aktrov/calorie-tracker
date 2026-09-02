"""Config loader. config.json is optional — sensible defaults apply when it is
absent. Holds no secrets (Open Food Facts is an unauthenticated public API), so
it ships as config.example.json and a working config.json both."""
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
}

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
            cfg.update({k: v for k, v in user.items() if k != "openfoodfacts"})
            if isinstance(user.get("openfoodfacts"), dict):
                cfg["openfoodfacts"] = {**_DEFAULTS["openfoodfacts"], **user["openfoodfacts"]}
        _config = cfg
    return _config


def timezone():
    try:
        return ZoneInfo(load().get("timezone", "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def off():
    return load()["openfoodfacts"]
