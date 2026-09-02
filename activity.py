"""Rough calorie-burn estimates for logged activity.

MET (metabolic equivalent) method:
    kcal/min = MET * 3.5 * bodyweight_kg / 200
    kcal     = kcal/min * duration_min

For strength entries with no explicit duration we approximate the working time
as sets * MIN_PER_SET (a set plus its rest). The lifted load (`weight_kg` on the
entry) is recorded but does not enter the MET formula — MET already scales with
body weight, and per-lift energy cost is noise at this precision. Users can
always type their own kcal number, which we store verbatim (`manual_kcal = 1`).

MET values are from the Compendium of Physical Activities, rounded to something
reasonable for a gym session.
"""

# (display name, kind, MET)
ACTIVITIES = [
    ("Walking", "cardio", 3.5),
    ("Brisk walking", "cardio", 4.3),
    ("Treadmill", "cardio", 8.5),
    ("Running", "cardio", 9.8),
    ("Elliptical", "cardio", 5.0),
    ("Stationary bike", "cardio", 7.0),
    ("Cycling (outdoor)", "cardio", 7.5),
    ("Spinning", "cardio", 8.5),
    ("Rowing machine", "cardio", 7.0),
    ("Stair climber", "cardio", 9.0),
    ("Swimming", "cardio", 7.0),
    ("Jump rope", "cardio", 12.0),
    ("HIIT", "cardio", 8.0),
    ("Circuit training", "cardio", 8.0),
    ("Elliptical / cross-trainer", "cardio", 5.0),
    ("Yoga", "cardio", 3.0),
    ("Pilates", "cardio", 3.0),
    ("Stretching / mobility", "cardio", 2.3),
    ("Weight training (light)", "strength", 3.5),
    ("Weight training (vigorous)", "strength", 6.0),
    ("Machine weights", "strength", 5.0),
    ("Leg press", "strength", 5.0),
    ("Leg curl", "strength", 5.0),
    ("Leg extension", "strength", 5.0),
    ("Chest press", "strength", 5.0),
    ("Bench press", "strength", 5.0),
    ("Lat pulldown", "strength", 5.0),
    ("Back machine / row", "strength", 5.0),
    ("Shoulder press", "strength", 5.0),
    ("Bicep curl", "strength", 4.5),
    ("Tricep pushdown", "strength", 4.5),
    ("Deadlift", "strength", 6.0),
    ("Squat", "strength", 5.5),
    ("Pull-ups", "strength", 5.0),
    ("Push-ups", "strength", 3.8),
    ("Plank", "strength", 3.3),
    ("Abs / core", "strength", 3.8),
]

_MET = {name.lower(): (kind, met) for name, kind, met in ACTIVITIES}

DEFAULT_MET = {"cardio": 5.0, "strength": 5.0, "other": 4.0}
DEFAULT_BODY_WEIGHT = 70.0
MIN_PER_SET = 3.5


def catalog():
    return [{"name": n, "kind": k} for n, k, _ in ACTIVITIES]


def _lookup(name):
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _MET:
        return _MET[key]
    for k, v in _MET.items():
        if k in key or key in k:
            return v
    return None


def met_for(kind, name):
    found = _lookup(name)
    if found:
        return found[1]
    return DEFAULT_MET.get(kind, 4.0)


def estimate_calories(kind, name, duration_min=None, sets=None, reps=None,
                      body_weight_kg=None):
    bw = body_weight_kg or DEFAULT_BODY_WEIGHT
    met = met_for(kind, name)

    minutes = duration_min
    if not minutes or minutes <= 0:
        if sets and sets > 0:
            minutes = sets * MIN_PER_SET
        else:
            minutes = 10.0

    kcal = met * 3.5 * bw / 200.0 * minutes
    return max(1, round(kcal))
