# SHARED FILE — byte-identical copies live in calorie-tracker/ and pulse/.
# Edit one, copy it over, then run ~/.claude/optimus/scripts/check-shared.sh.
"""Daily targets — the 'auto-calc'.

NOTE: the calorie budget both apps show comes from energy.py (resting + band-
measured movement − planned deficit). `calories` here is the static,
activity-factor estimate; it is kept for the profile breakdown and for the
protein fallback. Protein / fiber targets and body fat still come from here.

Given a profile and the current weight, compute the static daily estimate:

  BMR  (Mifflin-St Jeor)
       men:   10*kg + 6.25*cm - 5*age + 5
       women: 10*kg + 6.25*cm - 5*age - 161
  TDEE = BMR * activity factor          (maintenance calories)
  goal = TDEE -/+ (target_rate_kg_per_week * 7700 / 7)   (deficit / surplus)
  goal is floored at a safe minimum.

A manual calorie goal on the profile always wins. If height / birth year /
weight are missing, auto-calc can't run and the goal is None until the user
either fills the profile or sets a manual number.
"""
import math
from datetime import date

ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}
KCAL_PER_KG = 7700.0
MIN_CALORIES = {"male": 1500, "female": 1200}
DEFAULT_FIBER_GOAL = 30
PROTEIN_G_PER_KG = 1.6  # sensible target while in a deficit


def age_from_birth_year(birth_year, today=None):
    if not birth_year:
        return None
    today = today or date.today()
    return today.year - int(birth_year)


def bmr_mifflin(sex, weight_kg, height_cm, age):
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + 5 if sex == "male" else base - 161


def navy_body_fat(sex, height_cm, waist_cm, neck_cm, hip_cm=None):
    """US Navy circumference method, metric form. Returns an estimated body-fat
    percentage (rounded to 0.1) or None if the inputs it needs are missing or
    out of range. It's an estimate, not a measurement — good for trend, not
    for a precise number."""
    if not (height_cm and waist_cm and neck_cm):
        return None
    try:
        if sex == "female":
            if not hip_cm:
                return None
            denom = (
                1.29579
                - 0.35004 * math.log10(waist_cm + hip_cm - neck_cm)
                + 0.22100 * math.log10(height_cm)
            )
        else:
            if waist_cm - neck_cm <= 0:
                return None
            denom = (
                1.0324
                - 0.19077 * math.log10(waist_cm - neck_cm)
                + 0.15456 * math.log10(height_cm)
            )
        bf = 495.0 / denom - 450.0
    except (ValueError, ZeroDivisionError):
        return None
    if bf <= 0 or bf >= 75:
        return None
    return round(bf, 1)


def compute_targets(profile, current_weight, today=None):
    """Returns a dict:
      calories, protein_g, fiber_g   -> the targets (calories may be None)
      auto                           -> bool, was calorie goal auto-calculated
      breakdown                      -> {bmr, tdee, activity_factor, deficit,
                                         age, current_weight} or None
    """
    sex = profile.get("sex") or "male"
    activity = profile.get("activity_level") or "light"
    factor = ACTIVITY_FACTORS.get(activity, 1.375)
    age = age_from_birth_year(profile.get("birth_year"), today)
    height = profile.get("height_cm")
    goal_type = profile.get("goal_type") or "lose"
    rate = profile.get("target_rate_kg_per_week") or 0.0

    breakdown = None
    auto_calories = None
    if current_weight and height and age:
        bmr = bmr_mifflin(sex, current_weight, height, age)
        tdee = bmr * factor
        swing = (rate * KCAL_PER_KG) / 7.0
        if goal_type == "lose":
            goal = tdee - swing
        elif goal_type == "gain":
            goal = tdee + swing
        else:
            goal = tdee
            swing = 0.0
        goal = max(goal, MIN_CALORIES.get(sex, 1200))
        auto_calories = int(round(goal))
        breakdown = {
            "bmr": int(round(bmr)),
            "tdee": int(round(tdee)),
            "activity_factor": factor,
            "deficit": int(round(swing)) * (-1 if goal_type == "gain" else 1),
            "age": age,
            "current_weight": current_weight,
        }

    manual_cal = profile.get("manual_calorie_goal")
    calories = manual_cal if manual_cal else auto_calories

    manual_pro = profile.get("manual_protein_goal")
    if manual_pro:
        protein_g = int(manual_pro)
    elif current_weight:
        protein_g = int(round(PROTEIN_G_PER_KG * current_weight))
    elif calories:
        protein_g = int(round(calories * 0.30 / 4))
    else:
        protein_g = None

    fiber_g = int(profile.get("manual_fiber_goal") or DEFAULT_FIBER_GOAL)

    return {
        "calories": calories,
        "protein_g": protein_g,
        "fiber_g": fiber_g,
        "auto": bool(auto_calories and not manual_cal),
        "breakdown": breakdown,
    }
