"""Open Food Facts client — a thin, defensive wrapper.

Two calls:
  search(q)        -> list of normalized food dicts (name/brand/off_code + per-100g)
  product(barcode) -> one normalized dict, or None

Everything is best-effort. Any network error, timeout, non-JSON body (OFF serves
an HTML "temporarily unavailable" page when overloaded), or missing energy value
yields [] / None so the caller can fall back to the local food database. The
legacy /cgi/search.pl is deprecated and frequently down; we use the newer
search.openfoodfacts.org service for text search and world.openfoodfacts.org
/api/v2 for barcode lookups (that one is reliable).
"""
import requests

import config

SEARCH_URL = "https://search.openfoodfacts.org/search"
PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{}.json"
_FIELDS = "code,product_name,brands,nutriments,serving_size,serving_quantity,quantity"


def _cfg():
    c = config.off()
    return c.get("enabled", True), c.get("timeout_seconds", 5), c.get(
        "user_agent", "calorie-tracker/0.1 (personal homelab app)"
    )


def enabled():
    return _cfg()[0]


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _brand(raw):
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return None
    return str(raw).split(",")[0].strip() or None


def _normalize(p):
    n = p.get("nutriments") or {}
    kcal = n.get("energy-kcal_100g")
    if kcal is None:
        kcal_kj = n.get("energy_100g")
        kcal = (kcal_kj / 4.184) if isinstance(kcal_kj, (int, float)) else None
    if kcal is None:
        return None
    name = (p.get("product_name") or "").strip()
    if not name:
        return None
    sg = p.get("serving_quantity")
    try:
        sg = float(sg) if sg not in (None, "") else None
    except (TypeError, ValueError):
        sg = None
    return {
        "source": "off",
        "off_code": str(p.get("code")) if p.get("code") else None,
        "name": name,
        "brand": _brand(p.get("brands")),
        "serving_size": p.get("serving_size") or p.get("quantity"),
        "serving_grams": sg,
        "kcal_100g": _num(kcal),
        "protein_100g": _num(n.get("proteins_100g")),
        "fiber_100g": _num(n.get("fiber_100g")),
        "carbs_100g": _num(n.get("carbohydrates_100g")),
        "fat_100g": _num(n.get("fat_100g")),
    }


def search(q, limit=12):
    enabled_, timeout, ua = _cfg()
    q = (q or "").strip()
    if not enabled_ or len(q) < 2:
        return []
    try:
        r = requests.get(
            SEARCH_URL,
            params={"q": q, "page_size": limit, "fields": _FIELDS},
            headers={"User-Agent": ua},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for hit in data.get("hits", []):
        item = _normalize(hit)
        if item:
            out.append(item)
    return out


def product(barcode):
    enabled_, timeout, ua = _cfg()
    barcode = (barcode or "").strip()
    if not enabled_ or not barcode.isdigit():
        return None
    try:
        r = requests.get(
            PRODUCT_URL.format(barcode),
            params={"fields": _FIELDS},
            headers={"User-Agent": ua},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if data.get("status") == 0 or "product" not in data:
        return None
    prod = data["product"]
    prod.setdefault("code", barcode)
    return _normalize(prod)
