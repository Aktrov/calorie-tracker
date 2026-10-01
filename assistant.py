"""Nutrition lookup via a local Claude CLI (print mode) — no API key.

`ask("2 boiled eggs")` -> {ok, name, serving_desc, serving_grams,
calories, protein_g, fiber_g, carbs_g, fat_g, assumptions}

`ask_activity("ran 5k this morning")` -> {ok, name, kind, duration_min,
sets, reps, calories, assumptions}
"""
import json
import re
import shutil
import subprocess
import tempfile

import config

MAX_QUERY = 300

_PROMPT = """You are a nutrition database. For the food and portion below, reply with ONLY \
a single minified JSON object and nothing else — no markdown, no code fence, no commentary.

Keys:
  name           cleaned-up food name
  serving_desc   the portion, e.g. "2 medium eggs" or "1 cup (150 g)"
  serving_grams  number, grams for that portion, or null
  calories       number, kcal for the WHOLE portion
  protein_g fiber_g carbs_g fat_g   numbers for the WHOLE portion
  assumptions    short string: what you assumed (cooked vs raw, size, brand)

Use standard reference values (USDA FoodData Central, Indian IFCT). Prefer cooked \
values for dishes/grains/legumes unless "raw"/"dry" is stated. If the portion is \
unspecified, assume one typical serving and note it in assumptions.

Food: {query}"""

_NUM_KEYS = ("serving_grams", "calories", "protein_g", "fiber_g", "carbs_g", "fat_g")

_ACTIVITY_PROMPT = """You are a fitness/exercise-physiology assistant. For the activity described \
below, reply with ONLY a single minified JSON object and nothing else — no markdown, no code \
fence, no commentary.

Keys:
  name           cleaned-up short activity name, e.g. "Running" or "Bench press"
  kind           either "cardio" or "strength"
  duration_min   number, total minutes of the activity, or null if not applicable (e.g. a
                 strength set count was given instead)
  sets           number, sets performed, or null (strength only)
  reps           number, reps per set, or null (strength only)
  calories       number, estimated kcal burned for the WHOLE described activity, using MET
                 (metabolic equivalent) tables and the given body weight
  assumptions    short string: what you assumed (pace/intensity, duration, body weight used)

The person weighs approximately {body_weight} kg. Use standard MET values (Compendium of \
Physical Activities). If duration is unspecified for a cardio activity, assume a typical 20-30 \
minute session and note it in assumptions. If it's a strength/weights activity, prefer sets/reps \
over duration when both could apply.

Activity: {query}"""

_ACTIVITY_NUM_KEYS = ("duration_min", "sets", "reps", "calories")


def available():
    a = config.assistant()
    return bool(a.get("enabled")) and shutil.which(a.get("command") or "claude") is not None


def _num(v, lo=0.0, hi=1e5):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return max(lo, min(hi, n))


def _extract_json(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    return json.loads(text[start:end + 1])


def ask(query):
    query = (query or "").strip()[:MAX_QUERY]
    if not query:
        return {"ok": False, "error": "empty query"}
    if not available():
        return {"ok": False, "error": "assistant is not configured"}

    a = config.assistant()
    cmd = [a.get("command") or "claude", "-p", _PROMPT.format(query=query),
           "--output-format", "json", "--model", a.get("model") or "haiku"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=float(a.get("timeout_seconds") or 60),
            cwd=tempfile.gettempdir(),   # don't load this project's context
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "lookup timed out"}
    except OSError as e:
        return {"ok": False, "error": f"could not run '{cmd[0]}': {e}"}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "cli failed").strip()[:200]}

    try:
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            return {"ok": False, "error": str(envelope.get("result"))[:200]}
        data = _extract_json(envelope.get("result", ""))
    except (json.JSONDecodeError, ValueError) as e:
        return {"ok": False, "error": f"could not parse reply: {e}"}

    out = {
        "ok": True,
        "name": str(data.get("name") or query)[:120],
        "serving_desc": str(data.get("serving_desc") or "1 serving")[:80],
        "assumptions": str(data.get("assumptions") or "")[:300],
        "query": query,
    }
    for k in _NUM_KEYS:
        out[k] = _num(data.get(k))
    if out["calories"] is None:
        return {"ok": False, "error": "model did not return a calorie value"}
    for k in ("protein_g", "fiber_g", "carbs_g", "fat_g"):
        if out[k] is None:
            out[k] = 0.0
    return out


def ask_activity(query, body_weight_kg=None):
    query = (query or "").strip()[:MAX_QUERY]
    if not query:
        return {"ok": False, "error": "empty query"}
    if not available():
        return {"ok": False, "error": "assistant is not configured"}

    bw = body_weight_kg or 70.0
    a = config.assistant()
    prompt = _ACTIVITY_PROMPT.format(body_weight=round(bw), query=query)
    cmd = [a.get("command") or "claude", "-p", prompt,
           "--output-format", "json", "--model", a.get("model") or "haiku"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=float(a.get("timeout_seconds") or 60),
            cwd=tempfile.gettempdir(),   # don't load this project's context
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "lookup timed out"}
    except OSError as e:
        return {"ok": False, "error": f"could not run '{cmd[0]}': {e}"}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "cli failed").strip()[:200]}

    try:
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            return {"ok": False, "error": str(envelope.get("result"))[:200]}
        data = _extract_json(envelope.get("result", ""))
    except (json.JSONDecodeError, ValueError) as e:
        return {"ok": False, "error": f"could not parse reply: {e}"}

    kind = str(data.get("kind") or "").strip().lower()
    if kind not in ("cardio", "strength"):
        kind = "cardio"

    out = {
        "ok": True,
        "name": str(data.get("name") or query)[:120],
        "kind": kind,
        "assumptions": str(data.get("assumptions") or "")[:300],
        "query": query,
    }
    for k in _ACTIVITY_NUM_KEYS:
        out[k] = _num(data.get(k))
    if out["calories"] is None:
        return {"ok": False, "error": "model did not return a calorie value"}
    if out["sets"] is not None:
        out["sets"] = int(round(out["sets"]))
    if out["reps"] is not None:
        out["reps"] = int(round(out["reps"]))
    return out
