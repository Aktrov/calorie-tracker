# SHARED FILE — byte-identical copies live in calorie-tracker/ and pulse/.
# Edit one, copy it over, then run ~/.claude/optimus/scripts/check-shared.sh.
"""The day's energy: one model, used by calorie-tracker and Pulse alike.

    calories out = resting + active + logged
      resting  BMR (Mifflin-St Jeor) x 1.1 — basal burn plus ~10 % for
               digesting food. Everything else is movement, and the band
               measures movement.
      active   the band's active calories for the day (walks, cardio, the gym
               while a band workout runs). Huawei's "active calories" exclude
               resting burn, so nothing here overlaps `resting`.
      logged   logged activities the band can't see (weights, machines, bike,
               swimming...), net of resting, minus whatever the band already
               measured inside band workouts with ~no steps. Logged walks and
               cardio are a journal only — the band has them.

    Day with no band data (not worn, or before the band): resting is BMR x 1.2
    (sedentary) and every logged activity counts.

    budget    = calories out - planned deficit (goal rate, e.g. 0.5 kg/wk = 550),
                never below the safe minimum. A manual calorie goal on the
                profile replaces it as a fixed number.
    remaining = budget - eaten

Today the band total is "so far", so the budget grows as you move. Resting is
counted for the whole day (you will burn it by midnight); `out_so_far` and
`balance_so_far` prorate it for a running total.
"""
import sqlite3
from datetime import date, datetime, timedelta

import activity
import nutrition

RESTING_FACTOR = 1.1
NO_BAND_FACTOR = 1.2
STEPPING_SPM = 30          # a band workout under this many steps/min is stationary


def planned_deficit(profile):
    """kcal/day below maintenance the goal asks for (negative = surplus)."""
    goal_type = profile.get("goal_type") or "lose"
    swing = round((profile.get("target_rate_kg_per_week") or 0.0) * nutrition.KCAL_PER_KG / 7.0)
    if goal_type == "lose":
        return swing
    if goal_type == "gain":
        return -swing
    return 0


def _band_row(conn, date_str):
    row = conn.execute(
        "SELECT steps, calories_kcal FROM band_daily WHERE date = ?", (date_str,)).fetchone()
    return row if row and ((row[0] or 0) or (row[1] or 0)) else None


def _epoch(iso):
    return int(datetime.fromisoformat(iso).timestamp())


def band_summary(conn, date_str, today_str=None):
    """What the band measured on one day, read from Pulse's health.db.

    `conn` is any sqlite3 connection to health.db (outside Pulse, open it
    read-only). Returns None when the band has nothing for the day — the model
    then falls back to the log. Today with no rows yet but yesterday covered
    means the band is in use and the data just hasn't synced: active 0,
    pending True.

      active      kcal of movement (resting excluded)
      stationary  the part of `active` measured inside band workouts with
                  ~no steps (weights, machines)
    """
    try:
        row = _band_row(conn, date_str)
        if row is None:
            if date_str == today_str:
                prev = (date.fromisoformat(date_str) - timedelta(days=1)).isoformat()
                if _band_row(conn, prev) is not None:
                    return {"active": 0.0, "stationary": 0.0, "pending": True}
            return None
    except sqlite3.Error:
        return None

    stationary = 0.0
    try:
        workouts = conn.execute(
            "SELECT start_ts, end_ts, duration_s, steps FROM workouts WHERE date = ?",
            (date_str,)).fetchall()
        for start_ts, end_ts, duration_s, steps in workouts:
            minutes = (duration_s or 0) / 60.0
            if minutes <= 0 or not end_ts or (steps or 0) / minutes >= STEPPING_SPM:
                continue
            got = conn.execute(
                "SELECT COALESCE(SUM(calories_milli), 0) FROM activity_raw WHERE ts >= ? AND ts < ?",
                (_epoch(start_ts), _epoch(end_ts))).fetchone()
            stationary += (got[0] or 0) / 1000.0
    except (sqlite3.Error, ValueError):
        pass                       # older health.db without workouts: no reduction
    return {"active": float(row[1] or 0), "stationary": stationary, "pending": False}


def day(profile, weight_kg, date_str, today_str, band, entries, eaten, now=None):
    """The day's energy.

    profile    calorie-tracker profile row (dict)
    weight_kg  weight as of `date_str` (latest weigh-in, else start weight)
    band       band_summary(...) or None
    entries    that day's activity_entries rows (dicts)
    eaten      kcal in the food log for the day
    now        aware local datetime — prorates today's resting for running totals

    Returns None only when there is no way to set a budget (profile incomplete
    and no manual goal).
    """
    sex = profile.get("sex") or "male"
    height = profile.get("height_cm")
    age = nutrition.age_from_birth_year(profile.get("birth_year"), date.fromisoformat(date_str))
    manual = profile.get("manual_calorie_goal") or None
    eaten = eaten or 0
    body_weight = weight_kg or activity.DEFAULT_BODY_WEIGHT
    band_mode = band is not None

    items, blind, logged_total = [], 0.0, 0.0
    for e in entries:
        if e.get("manual_kcal"):
            net = float(e.get("calories_burned") or 0)
        else:
            net = float(activity.estimate_calories(
                e.get("kind"), e.get("name"), e.get("duration_min"),
                e.get("sets"), e.get("reps"), body_weight))
        in_band = band_mode and activity.band_sees(e.get("kind"), e.get("name"))
        logged_total += net
        if not in_band:
            blind += net
        items.append({"id": e.get("id"), "kcal": round(net), "in_band": in_band})

    stationary = min(band["stationary"], blind) if band_mode else 0.0
    active = band["active"] if band_mode else 0.0
    logged = blind - stationary

    if not (weight_kg and height and age):
        if not manual:
            return None
        return dict(mode="manual", pending=False, bmr=None, resting_factor=None,
                    resting=None, resting_so_far=None, active=round(active),
                    band_stationary=round(stationary), logged=round(logged),
                    logged_total=round(logged_total), out=None, out_so_far=None,
                    deficit_target=None, budget=int(manual), manual=True, floored=False,
                    eaten=round(eaten), remaining=round(manual - eaten),
                    balance_so_far=None, partial=date_str == today_str, entries=items)

    bmr = nutrition.bmr_mifflin(sex, weight_kg, height, age)
    factor = RESTING_FACTOR if band_mode else NO_BAND_FACTOR
    resting = bmr * factor
    frac = 1.0
    if date_str == today_str and now is not None:
        frac = (now.hour * 60 + now.minute) / 1440.0

    out = resting + active + logged
    out_so_far = resting * frac + active + logged
    deficit = planned_deficit(profile)
    floored = False
    if manual:
        budget = float(manual)
    else:
        budget = out - deficit
        floor = nutrition.MIN_CALORIES.get(sex, 1200)
        if deficit > 0 and budget < floor:
            budget, floored = floor, True

    return dict(
        mode="band" if band_mode else "log",
        pending=bool(band_mode and band.get("pending")),
        bmr=round(bmr), resting_factor=factor,
        resting=round(resting), resting_so_far=round(resting * frac),
        active=round(active), band_stationary=round(stationary),
        logged=round(logged), logged_total=round(logged_total),
        out=round(out), out_so_far=round(out_so_far),
        deficit_target=deficit, budget=round(budget), manual=bool(manual), floored=floored,
        eaten=round(eaten), remaining=round(budget - eaten),
        balance_so_far=round(out_so_far - eaten),     # > 0 = in deficit so far
        partial=date_str == today_str,
        entries=items,
    )
