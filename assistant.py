"""Nutrition lookup via a local Claude CLI (print mode) — no API key.

`ask("2 boiled eggs")` -> {ok, name, serving_desc, serving_grams,
calories, protein_g, fiber_g, carbs_g, fat_g, assumptions,
kcal_low, kcal_high, confidence, clarify}
  Runs the `calorie-estimator` agent (~/.claude/agents/calorie-estimator.md),
  which owns the prompt and reads its food memory (FOODS.md in memory_dir).
  The app is READ-ONLY against that memory: Read is allowed only inside
  memory_dir, Write/Edit are explicitly denied. Only the user's own CLI chat
  with the agent writes memory.

`ask_activity("ran 5k this morning")` -> {ok, name, kind, duration_min,
sets, reps, calories, assumptions}   (generic inline prompt, model from config)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import config

MAX_QUERY = 300

_NUM_KEYS = ("serving_grams", "calories", "protein_g", "fiber_g", "carbs_g", "fat_g",
             "kcal_low", "kcal_high")
_CONFIDENCE = ("high", "medium", "low")

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
    """CLI present + enabled. Gates the activity assistant."""
    a = config.assistant()
    return bool(a.get("enabled")) and shutil.which(a.get("command") or "claude") is not None


def _agent_name():
    return config.assistant().get("agent") or "calorie-estimator"


def _memory_dir():
    return os.path.abspath(os.path.expanduser(
        config.assistant().get("memory_dir") or "~/.claude/calorie-estimator"))


def food_available():
    """available() + the calorie-estimator agent definition exists.
    Gates the food Ask tab and /api/nutrition/ask (503 when false)."""
    agent_file = os.path.expanduser(f"~/.claude/agents/{_agent_name()}.md")
    return available() and os.path.isfile(agent_file)


def _food_cmd(query):
    """The agent invocation. Read is scoped to memory_dir on purpose: a bare
    `--allowedTools Read` lets an injected query read any file this user can
    (verified 2026-10-02) and echo it back in `assumptions`."""
    a = config.assistant()
    mem = _memory_dir()
    # Permission-rule paths: '//' prefix = absolute filesystem path.
    read_rule = f"Read(/{mem}/**)"
    return [a.get("command") or "claude", "-p", query,
            "--agent", _agent_name(),
            "--output-format", "json",
            "--allowedTools", read_rule,
            "--disallowedTools", "Write", "Edit",
            "--add-dir", mem]


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
    if not food_available():
        return {"ok": False, "error": "assistant is not configured"}

    a = config.assistant()
    cmd = _food_cmd(query)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=float(a.get("timeout_seconds") or 90),
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
    if not isinstance(data, dict):
        return {"ok": False, "error": "could not parse reply: not a JSON object"}

    # Agent's refusal shape for non-food input: {"error": "<reason>"}
    if data.get("error") and data.get("calories") is None:
        return {"ok": False, "error": str(data["error"])[:200]}

    confidence = str(data.get("confidence") or "").strip().lower()
    clarify = data.get("clarify")
    clarify = str(clarify).strip()[:200] if clarify not in (None, "") else None

    out = {
        "ok": True,
        "name": str(data.get("name") or query)[:120],
        "serving_desc": str(data.get("serving_desc") or "1 serving")[:80],
        "assumptions": str(data.get("assumptions") or "")[:800],
        "confidence": confidence if confidence in _CONFIDENCE else None,
        "clarify": clarify or None,
        "query": query,
    }
    for k in _NUM_KEYS:
        out[k] = _num(data.get(k))
    if out["calories"] is None:
        return {"ok": False, "error": "model did not return a calorie value"}
    for k in ("protein_g", "fiber_g", "carbs_g", "fat_g"):
        if out[k] is None:
            out[k] = 0.0
    lo, hi = out["kcal_low"], out["kcal_high"]
    if lo is None or hi is None:
        out["kcal_low"] = out["kcal_high"] = None   # half a range is no range
    elif lo > hi:
        out["kcal_low"], out["kcal_high"] = hi, lo
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
