# SHARED FILE — byte-identical copies live in calorie-tracker/ and pulse/.
# Edit one, copy it over, then run ~/.claude/optimus/scripts/check-shared.sh.
"""Calorie-burn estimates for logged activity.

MET (metabolic equivalent) method:
    kcal/min = MET * 3.5 * bodyweight_kg / 200

`estimate_calories` returns the *net* burn — (MET - 1), i.e. what the activity
adds on top of the resting burn the daily budget already counts (energy.py).
Gross MET would count those minutes of resting burn twice.

Strength entries with no explicit duration are timed as sets * MIN_PER_SET
(a set plus its rest); with no sets either, DEFAULT_SETS is assumed. The lifted
load (`weight_kg` on the entry) is recorded but does not enter the formula.
Users can always type their own kcal number, stored verbatim (`manual_kcal`).

MET values follow the Compendium of Physical Activities. Machine and isolation
lifts use 3.5 ("resistance training, multiple exercises, 8-15 reps, varied
resistance"); heavy compound lifts keep higher values.

`steps` on each activity says whether the wrist band sees it through step
counting. Activities it can't see (weights, bike, rowing, swimming, yoga) are
topped up from the log by energy.py.
"""

# (display name, kind, MET, band counts it from steps)
ACTIVITIES = [
    ("Walking", "cardio", 3.5, True),
    ("Brisk walking", "cardio", 4.3, True),
    ("Treadmill", "cardio", 8.5, True),
    ("Running", "cardio", 9.8, True),
    ("Elliptical", "cardio", 5.0, True),
    ("Stationary bike", "cardio", 7.0, False),
    ("Cycling (outdoor)", "cardio", 7.5, False),
    ("Spinning", "cardio", 8.5, False),
    ("Rowing machine", "cardio", 7.0, False),
    ("Stair climber", "cardio", 9.0, True),
    ("Swimming", "cardio", 7.0, False),
    ("Jump rope", "cardio", 12.0, True),
    ("HIIT", "cardio", 8.0, False),
    ("Circuit training", "cardio", 8.0, False),
    ("Elliptical / cross-trainer", "cardio", 5.0, True),
    ("Yoga", "cardio", 3.0, False),
    ("Pilates", "cardio", 3.0, False),
    ("Stretching / mobility", "cardio", 2.3, False),
    ("Weight training (light)", "strength", 3.5, False),
    ("Weight training (vigorous)", "strength", 6.0, False),
    ("Machine weights", "strength", 3.5, False),
    ("Leg press", "strength", 3.5, False),
    ("Leg curl", "strength", 3.5, False),
    ("Leg extension", "strength", 3.5, False),
    ("Chest press", "strength", 3.5, False),
    ("Bench press", "strength", 3.5, False),
    ("Lat pulldown", "strength", 3.5, False),
    ("Back machine / row", "strength", 3.5, False),
    ("Shoulder press", "strength", 3.5, False),
    ("Bicep curl", "strength", 3.5, False),
    ("Tricep pushdown", "strength", 3.5, False),
    ("Deadlift", "strength", 6.0, False),
    ("Squat", "strength", 5.0, False),
    ("Pull-ups", "strength", 5.0, False),
    ("Push-ups", "strength", 3.8, False),
    ("Plank", "strength", 3.3, False),
    ("Abs / core", "strength", 3.8, False),
]

_BY_NAME = {name.lower(): (kind, met, steps) for name, kind, met, steps in ACTIVITIES}

DEFAULT_MET = {"cardio": 5.0, "strength": 3.5, "other": 4.0}
DEFAULT_BODY_WEIGHT = 70.0
MIN_PER_SET = 2.5        # one working set plus its rest
DEFAULT_SETS = 3         # strength entry logged with reps but no sets
DEFAULT_MINUTES = 10.0   # anything else logged without a duration


def catalog():
    return [{"name": n, "kind": k} for n, k, _m, _s in ACTIVITIES]


def _lookup(name):
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _BY_NAME:
        return _BY_NAME[key]
    for k, v in _BY_NAME.items():
        if k in key or key in k:
            return v
    return None


def met_for(kind, name):
    found = _lookup(name)
    if found:
        return found[1]
    return DEFAULT_MET.get(kind, 4.0)


def band_sees(kind, name):
    """True if the band counts this activity from steps. Unknown names: cardio
    is assumed to be on foot, strength is not."""
    found = _lookup(name)
    if found:
        return found[2]
    return kind == "cardio"


def minutes_for(kind, duration_min=None, sets=None):
    if duration_min and duration_min > 0:
        return float(duration_min)
    if sets and sets > 0:
        return sets * MIN_PER_SET
    if kind == "strength":
        return DEFAULT_SETS * MIN_PER_SET
    return DEFAULT_MINUTES


def estimate_calories(kind, name, duration_min=None, sets=None, reps=None,
                      body_weight_kg=None):
    """Net kcal above resting (see module docstring)."""
    bw = body_weight_kg or DEFAULT_BODY_WEIGHT
    net_met = max(0.0, met_for(kind, name) - 1.0)
    kcal = net_met * 3.5 * bw / 200.0 * minutes_for(kind, duration_min, sets)
    return max(1, round(kcal))
