"""calorie-tracker — a personal food + calorie log with a daily budget and a
weight trend. Flask + SQLite, single process, bound to 127.0.0.1:5200, mounted
on the tailnet at /calorie-tracker.

Conventions match screen-time-dashboard: PrefixMiddleware threads SCRIPT_NAME so
url_for() emits correct links behind `tailscale serve --set-path`.
"""
import os
from datetime import datetime, timedelta

from flask import (
    Flask, jsonify, render_template, request, send_from_directory, url_for,
)

import activity
import config
import db
import nutrition
import offapi

app = Flask(__name__)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

MEALS = ("breakfast", "lunch", "dinner", "snack")
MAX_NAME_LEN = 120


class PrefixMiddleware:
    """Tells Flask's url_for() the external mount prefix (e.g. "/calorie-tracker")
    so generated links are correct when served behind tailscale serve --set-path,
    which strips the prefix before the request reaches this app."""

    def __init__(self, wsgi_app, prefix=""):
        self.wsgi_app = wsgi_app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ["SCRIPT_NAME"] = self.prefix
        return self.wsgi_app(environ, start_response)


app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=os.environ.get("SCRIPT_NAME", ""))

db.init_db()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _today():
    return datetime.now(config.timezone()).date()


def _parse_date(raw, default=None):
    if not raw:
        return default or _today().isoformat()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except (ValueError, TypeError):
        return default or _today().isoformat()


def _err(status, message):
    return jsonify({"error": message}), status


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _round(v, n=1):
    return round(v or 0, n)


def _entry_view(e):
    return {
        "id": e["id"],
        "meal": e["meal"],
        "description": e["description"],
        "brand": e.get("food_brand"),
        "food_id": e["food_id"],
        "servings": _round(e["servings"], 2),
        "serving_desc": e.get("food_serving_desc"),
        "calories": _round(e["calories"]),
        "protein_g": _round(e["protein_g"]),
        "fiber_g": _round(e["fiber_g"]),
    }


def _daily_goal_for(profile, on_date):
    """Targets for a specific date, using the weight known on or before it."""
    w = db.latest_weight(on_or_before=on_date) or profile.get("start_weight_kg")
    return nutrition.compute_targets(profile, w, today=_parse_dt(on_date))


def _body_weight_for(profile, on_date):
    return (
        db.latest_weight(on_or_before=on_date)
        or profile.get("start_weight_kg")
        or activity.DEFAULT_BODY_WEIGHT
    )


def _activity_view(a):
    return {
        "id": a["id"],
        "kind": a["kind"],
        "name": a["name"],
        "duration_min": a.get("duration_min"),
        "sets": a.get("sets"),
        "reps": a.get("reps"),
        "weight_kg": a.get("weight_kg"),
        "calories_burned": _round(a["calories_burned"]),
        "manual_kcal": bool(a.get("manual_kcal")),
        "notes": a.get("notes"),
    }


def _parse_dt(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return _today()


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", page="today")


@app.route("/profile")
def profile_page():
    return render_template("profile.html", page="profile")


@app.route("/history")
def history_page():
    return render_template("history.html", page="history")


@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "Calorie Tracker",
        "short_name": "Calories",
        "start_url": url_for("index"),
        "scope": url_for("index"),
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "background_color": "#f6f4ee",
        "theme_color": "#f6f4ee",
        "icons": [
            {
                "src": url_for("static", filename="logo.svg"),
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    })


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(
        os.path.join(PROJECT_ROOT, "static"), "sw.js", mimetype="application/javascript"
    )
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = request.script_root + "/"
    return resp


# --------------------------------------------------------------------------
# API — summary
# --------------------------------------------------------------------------
@app.route("/api/summary")
def api_summary():
    d = _parse_date(request.args.get("date"))
    profile = db.get_profile()
    targets = _daily_goal_for(profile, d)
    entries = db.entries_for_date(d)

    meals = {m: [] for m in MEALS}
    tot = {"calories": 0.0, "protein_g": 0.0, "fiber_g": 0.0}
    for e in entries:
        v = _entry_view(e)
        meals.get(e["meal"], meals["snack"]).append(v)
        tot["calories"] += e["calories"] or 0
        tot["protein_g"] += e["protein_g"] or 0
        tot["fiber_g"] += e["fiber_g"] or 0

    acts = db.activities_for_date(d)
    burned = round(sum(a["calories_burned"] or 0 for a in acts), 1)

    def remaining(key, goal_key):
        goal = targets.get(goal_key)
        return None if goal is None else _round(goal - tot[key])

    cal_goal = targets.get("calories")
    cal_remaining = None if cal_goal is None else _round(cal_goal - tot["calories"] + burned)

    return jsonify({
        "date": d,
        "today": _today().isoformat(),
        "profile": {"name": profile["name"], "goal_type": profile["goal_type"]},
        "goal": {
            "calories": cal_goal,
            "calories_adjusted": None if cal_goal is None else int(round(cal_goal + burned)),
            "protein_g": targets["protein_g"],
            "fiber_g": targets["fiber_g"],
            "auto": targets["auto"],
            "breakdown": targets["breakdown"],
        },
        "totals": {**{k: _round(v) for k, v in tot.items()}, "burned": burned},
        "remaining": {
            "calories": cal_remaining,
            "protein_g": remaining("protein_g", "protein_g"),
            "fiber_g": remaining("fiber_g", "fiber_g"),
        },
        "meals": meals,
        "activity": {"entries": [_activity_view(a) for a in acts], "burned": burned},
        "body": {
            "weight_kg": db.latest_weight(on_or_before=d),
            "waist_cm": db.latest_measurement("waist", on_or_before=d),
            "neck_cm": db.latest_measurement("neck", on_or_before=d),
            "body_fat_pct": _body_fat_for(profile, d),
            "goal_waist_cm": profile.get("goal_waist_cm"),
            "goal_weight_kg": profile.get("goal_weight_kg"),
        },
    })


# --------------------------------------------------------------------------
# API — food search / create
# --------------------------------------------------------------------------
@app.route("/api/foods/search")
def api_food_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"query": q, "local": [], "off": [], "off_enabled": offapi.enabled()})
    local = db.search_foods(q, limit=20)
    recent = db.recent_quick_entries(q, limit=6)
    want_off = request.args.get("off", "1") != "0"
    off_items = offapi.search(q) if (want_off and offapi.enabled()) else []
    seen_codes = {f["off_code"] for f in local if f.get("off_code")}
    off_items = [o for o in off_items if o.get("off_code") not in seen_codes]
    return jsonify({
        "query": q,
        "local": local,
        "recent": recent,
        "off": off_items,
        "off_enabled": offapi.enabled(),
    })


@app.route("/api/foods/barcode/<code>")
def api_food_barcode(code):
    item = offapi.product(code)
    if not item:
        return _err(404, "Product not found on Open Food Facts")
    return jsonify(item)


@app.route("/api/foods", methods=["POST"])
def api_food_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:MAX_NAME_LEN]
    calories = _num(data.get("calories"))
    if not name:
        return _err(400, "name is required")
    if calories is None:
        return _err(400, "calories is required")
    food = db.create_food({
        "name": name,
        "brand": (data.get("brand") or "").strip()[:MAX_NAME_LEN] or None,
        "source": "custom",
        "serving_desc": (data.get("serving_desc") or "1 serving").strip()[:60],
        "serving_grams": _num(data.get("serving_grams")),
        "calories": calories,
        "protein_g": _num(data.get("protein_g"), 0),
        "fiber_g": _num(data.get("fiber_g"), 0),
        "carbs_g": _num(data.get("carbs_g")),
        "fat_g": _num(data.get("fat_g")),
    })
    return jsonify(food), 201


# --------------------------------------------------------------------------
# API — log entries
# --------------------------------------------------------------------------
@app.route("/api/log", methods=["POST"])
def api_log_add():
    data = request.get_json(silent=True) or {}
    entry_date = _parse_date(data.get("date"))
    meal = data.get("meal") if data.get("meal") in MEALS else "snack"

    mode = data.get("mode", "food")

    if mode == "food":
        food = db.get_food(data.get("food_id"))
        if not food:
            return _err(404, "food not found")
        servings = _num(data.get("servings"), 1) or 1
        entry = {
            "entry_date": entry_date,
            "meal": meal,
            "food_id": food["id"],
            "description": food["name"],
            "servings": servings,
            "calories": (food["calories"] or 0) * servings,
            "protein_g": (food["protein_g"] or 0) * servings,
            "fiber_g": (food["fiber_g"] or 0) * servings,
        }

    elif mode == "off":
        item = {
            "off_code": data.get("off_code"),
            "name": (data.get("name") or "").strip()[:MAX_NAME_LEN],
            "brand": (data.get("brand") or "").strip()[:MAX_NAME_LEN] or None,
            "kcal_100g": _num(data.get("kcal_100g"), 0),
            "protein_100g": _num(data.get("protein_100g"), 0),
            "fiber_100g": _num(data.get("fiber_100g"), 0),
            "carbs_100g": _num(data.get("carbs_100g")),
            "fat_100g": _num(data.get("fat_100g")),
        }
        if not item["name"]:
            return _err(400, "name is required")
        grams = _num(data.get("grams"), 100) or 100
        food = db.upsert_off_food(item)
        servings = grams / 100.0
        entry = {
            "entry_date": entry_date,
            "meal": meal,
            "food_id": food["id"],
            "description": food["name"],
            "servings": servings,
            "calories": (food["calories"] or 0) * servings,
            "protein_g": (food["protein_g"] or 0) * servings,
            "fiber_g": (food["fiber_g"] or 0) * servings,
        }

    elif mode == "quick":
        name = (data.get("description") or "").strip()[:MAX_NAME_LEN]
        calories = _num(data.get("calories"))
        if not name:
            return _err(400, "description is required")
        if calories is None:
            return _err(400, "calories is required")
        entry = {
            "entry_date": entry_date,
            "meal": meal,
            "food_id": None,
            "description": name,
            "servings": 1,
            "calories": calories,
            "protein_g": _num(data.get("protein_g"), 0),
            "fiber_g": _num(data.get("fiber_g"), 0),
        }
    else:
        return _err(400, "unknown mode")

    return jsonify(_entry_view({**db.add_log_entry(entry), "food_brand": None, "food_serving_desc": None})), 201


@app.route("/api/log/<int:entry_id>", methods=["PUT", "PATCH"])
def api_log_update(entry_id):
    existing = db.get_log_entry(entry_id)
    if not existing:
        return _err(404, "entry not found")
    data = request.get_json(silent=True) or {}
    fields = {}

    if data.get("meal") in MEALS:
        fields["meal"] = data["meal"]

    if "servings" in data and existing["food_id"]:
        food = db.get_food(existing["food_id"])
        servings = _num(data.get("servings"), existing["servings"]) or existing["servings"]
        if food:
            fields["servings"] = servings
            fields["calories"] = (food["calories"] or 0) * servings
            fields["protein_g"] = (food["protein_g"] or 0) * servings
            fields["fiber_g"] = (food["fiber_g"] or 0) * servings
    else:
        for k in ("calories", "protein_g", "fiber_g"):
            if k in data:
                fields[k] = _num(data.get(k), existing[k])
        if "description" in data:
            fields["description"] = (data["description"] or "").strip()[:MAX_NAME_LEN]

    updated = db.update_log_entry(entry_id, fields)
    return jsonify(_entry_view({**updated, "food_brand": None, "food_serving_desc": None}))


@app.route("/api/log/<int:entry_id>", methods=["DELETE"])
def api_log_delete(entry_id):
    if not db.delete_log_entry(entry_id):
        return _err(404, "entry not found")
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — activity
# --------------------------------------------------------------------------
ACTIVITY_KINDS = ("cardio", "strength", "other")


@app.route("/api/activities/catalog")
def api_activity_catalog():
    return jsonify({"activities": activity.catalog()})


def _int_or_none(v):
    n = _num(v)
    return int(n) if n and n > 0 else None


@app.route("/api/activity", methods=["POST"])
def api_activity_add():
    data = request.get_json(silent=True) or {}
    entry_date = _parse_date(data.get("date"))
    kind = data.get("kind") if data.get("kind") in ACTIVITY_KINDS else "cardio"
    name = (data.get("name") or "").strip()[:MAX_NAME_LEN]
    if not name:
        return _err(400, "name is required")

    duration = _num(data.get("duration_min"))
    sets = _int_or_none(data.get("sets"))
    reps = _int_or_none(data.get("reps"))
    load = _num(data.get("weight_kg"))

    manual = _num(data.get("calories_burned"))
    if manual and manual > 0:
        kcal, manual_flag = int(round(manual)), True
    else:
        bw = _body_weight_for(db.get_profile(), entry_date)
        kcal = activity.estimate_calories(kind, name, duration, sets, reps, bw)
        manual_flag = False

    entry = db.add_activity_entry({
        "entry_date": entry_date, "kind": kind, "name": name,
        "duration_min": duration, "sets": sets, "reps": reps, "weight_kg": load,
        "calories_burned": kcal, "manual_kcal": manual_flag,
        "notes": (data.get("notes") or "").strip()[:200] or None,
    })
    return jsonify(_activity_view(entry)), 201


@app.route("/api/activity/<int:entry_id>", methods=["PUT", "PATCH"])
def api_activity_update(entry_id):
    existing = db.get_activity_entry(entry_id)
    if not existing:
        return _err(404, "entry not found")
    data = request.get_json(silent=True) or {}
    fields = {}

    if data.get("kind") in ACTIVITY_KINDS:
        fields["kind"] = data["kind"]
    if "name" in data:
        fields["name"] = (data["name"] or existing["name"]).strip()[:MAX_NAME_LEN]
    for k in ("duration_min", "weight_kg"):
        if k in data:
            fields[k] = _num(data.get(k))
    for k in ("sets", "reps"):
        if k in data:
            fields[k] = _int_or_none(data.get(k))
    if "notes" in data:
        fields["notes"] = (data.get("notes") or "").strip()[:200] or None

    if "calories_burned" in data and _num(data.get("calories_burned")):
        fields["calories_burned"] = int(round(_num(data.get("calories_burned"))))
        fields["manual_kcal"] = 1
    else:
        merged = {**existing, **fields}
        if not merged.get("manual_kcal"):
            bw = _body_weight_for(db.get_profile(), existing["entry_date"])
            fields["calories_burned"] = activity.estimate_calories(
                merged["kind"], merged["name"], merged.get("duration_min"),
                merged.get("sets"), merged.get("reps"), bw,
            )

    return jsonify(_activity_view(db.update_activity_entry(entry_id, fields)))


@app.route("/api/activity/<int:entry_id>", methods=["DELETE"])
def api_activity_delete(entry_id):
    if not db.delete_activity_entry(entry_id):
        return _err(404, "entry not found")
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — history
# --------------------------------------------------------------------------
@app.route("/api/history")
def api_history():
    try:
        days = max(7, min(365, int(request.args.get("days", 30))))
    except (TypeError, ValueError):
        days = 30
    end = _today()
    start = end - timedelta(days=days - 1)
    profile = db.get_profile()
    totals = db.daily_totals(start.isoformat(), end.isoformat())
    burned_by_day = db.daily_burned(start.isoformat(), end.isoformat())

    out_days = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        t = totals.get(d, {"calories": 0, "protein_g": 0, "fiber_g": 0})
        goal = _daily_goal_for(profile, d)["calories"]
        burned = burned_by_day.get(d, 0)
        out_days.append({
            "date": d,
            "calories": t["calories"],
            "protein_g": t["protein_g"],
            "fiber_g": t["fiber_g"],
            "burned": burned,
            "goal": goal,
            "goal_adjusted": None if goal is None else int(round(goal + burned)),
            "logged": d in totals or d in burned_by_day,
        })

    logged = [x for x in out_days if x["logged"]]
    food_days = [x for x in out_days if x["date"] in totals]
    avg_cal = round(sum(x["calories"] for x in food_days) / len(food_days)) if food_days else None
    active_days = sum(1 for x in out_days if x["burned"])

    waist = db.measurement_series("waist")
    neck = db.measurement_series("neck")
    bf_series = []
    for w in waist:
        bf = nutrition.navy_body_fat(
            profile.get("sex") or "male",
            profile.get("height_cm"),
            w["value_cm"],
            db.latest_measurement("neck", on_or_before=w["entry_date"]),
        )
        if bf is not None:
            bf_series.append({"entry_date": w["entry_date"], "value": bf})

    return jsonify({
        "days": out_days,
        "range_days": days,
        "avg_calories": avg_cal,
        "logged_days": len(food_days),
        "active_days": active_days,
        "weight": db.weight_series(),
        "start_weight_kg": profile.get("start_weight_kg"),
        "goal_weight_kg": profile.get("goal_weight_kg"),
        "waist": waist,
        "neck": neck,
        "body_fat": bf_series,
        "goal_waist_cm": profile.get("goal_waist_cm"),
    })


# --------------------------------------------------------------------------
# API — weight
# --------------------------------------------------------------------------
@app.route("/api/weight", methods=["GET"])
def api_weight_list():
    return jsonify({"weight": db.weight_series()})


@app.route("/api/weight", methods=["POST"])
def api_weight_add():
    data = request.get_json(silent=True) or {}
    weight = _num(data.get("weight_kg"))
    if weight is None or weight <= 0 or weight > 500:
        return _err(400, "weight_kg must be between 0 and 500")
    entry_date = _parse_date(data.get("date"))
    note = (data.get("note") or "").strip()[:200] or None
    db.upsert_weight(entry_date, weight, note)
    return jsonify({"ok": True, "weight": db.weight_series()}), 201


@app.route("/api/weight/<int:entry_id>", methods=["DELETE"])
def api_weight_delete(entry_id):
    if not db.delete_weight(entry_id):
        return _err(404, "entry not found")
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — body measurements (waist / neck)
# --------------------------------------------------------------------------
MEASURE_RANGE = {"waist": (30, 250), "neck": (15, 80), "hip": (40, 250)}


def _body_fat_for(profile, on_date=None):
    """Navy-method body-fat estimate using the most recent waist/neck on or
    before `on_date` (or the latest overall when `on_date` is None)."""
    waist = db.latest_measurement("waist", on_or_before=on_date)
    neck = db.latest_measurement("neck", on_or_before=on_date)
    hip = db.latest_measurement("hip", on_or_before=on_date)
    return nutrition.navy_body_fat(
        profile.get("sex") or "male", profile.get("height_cm"), waist, neck, hip
    )


@app.route("/api/measure/<kind>", methods=["GET"])
def api_measure_list(kind):
    if kind not in db.MEASUREMENT_KINDS:
        return _err(404, "unknown measurement")
    return jsonify({"kind": kind, "series": db.measurement_series(kind)})


@app.route("/api/measure/<kind>", methods=["POST"])
def api_measure_add(kind):
    if kind not in db.MEASUREMENT_KINDS:
        return _err(404, "unknown measurement")
    data = request.get_json(silent=True) or {}
    value = _num(data.get("value_cm"))
    lo, hi = MEASURE_RANGE[kind]
    if value is None or value < lo or value > hi:
        return _err(400, f"value_cm must be between {lo} and {hi}")
    entry_date = _parse_date(data.get("date"))
    note = (data.get("note") or "").strip()[:200] or None
    db.upsert_measurement(kind, entry_date, value, note)
    return jsonify({
        "ok": True,
        "kind": kind,
        "series": db.measurement_series(kind),
        "body_fat_pct": _body_fat_for(db.get_profile()),
    }), 201


@app.route("/api/measure/<int:entry_id>", methods=["DELETE"])
def api_measure_delete(entry_id):
    if not db.delete_measurement(entry_id):
        return _err(404, "entry not found")
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — profile
# --------------------------------------------------------------------------
def _profile_view(profile):
    w = db.latest_weight() or profile.get("start_weight_kg")
    targets = nutrition.compute_targets(profile, w)
    return {
        "profile": profile,
        "current_weight_kg": w,
        "targets": targets,
        "activity_options": list(nutrition.ACTIVITY_FACTORS.keys()),
    }


@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    return jsonify(_profile_view(db.get_profile()))


@app.route("/api/profile", methods=["POST", "PUT"])
def api_profile_update():
    data = request.get_json(silent=True) or {}
    fields = {}

    if "name" in data:
        fields["name"] = (data["name"] or "Me").strip()[:MAX_NAME_LEN] or "Me"
    if data.get("sex") in ("male", "female"):
        fields["sex"] = data["sex"]
    if data.get("activity_level") in nutrition.ACTIVITY_FACTORS:
        fields["activity_level"] = data["activity_level"]
    if data.get("goal_type") in ("lose", "maintain", "gain"):
        fields["goal_type"] = data["goal_type"]

    for k in ("birth_year", "manual_calorie_goal", "manual_protein_goal", "manual_fiber_goal"):
        if k in data:
            v = _num(data.get(k))
            fields[k] = int(v) if v else None
    for k in ("height_cm", "target_rate_kg_per_week", "start_weight_kg",
              "goal_weight_kg", "goal_waist_cm"):
        if k in data:
            fields[k] = _num(data.get(k))

    updated = db.update_profile(fields)
    return jsonify(_profile_view(updated))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5200, debug=False, use_reloader=False, threaded=True)
