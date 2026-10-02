# calorie-tracker

A personal food + calorie log with a daily budget and a weight trend. Log what
you eat, see calories in vs. your target, watch the weekly trend. HealthifyMe's
core loop, stripped to what drives weight change.

- **Repo:** `git@github.com:Aktrov/calorie-tracker.git`
- **Stack:** Flask + SQLite, single process.
- **Bind:** `127.0.0.1:5200` only.
- **Tailnet mount:** `https://groot.tail088f09.ts.net/calorie-tracker/`
- **Lifecycle:** `run.sh` / `cleanup.sh` + `.calorie-tracker.pid` + tmux session
  `calorie-tracker` (matches `screen-time-dashboard`).
- **Look:** deliberately *not* the suite's Cybertron-HUD theme — a calm
  food/health skin (warm cream + leaf green, soft shadows, rounded, light by
  default with a soft warm-dark toggle). Type is self-hosted **Nunito** (body) +
  **Fraunces** (headings / numbers). The logo is a custom apple mark, not the
  shared `assets/logo.svg`.

## Features

- **Today** — date switcher (can't go past today), calorie ring (eaten vs. goal
  + remaining), protein and fiber bars, per-meal accordion
  (breakfast / lunch / dinner / snack), activity log, body measurements. The
  meal and activity sections are collapsible
  accordions that all start **collapsed** — nothing opens on its own. Your
  manual expand/collapse is remembered for the visit, and the section you just
  logged to re-opens so you can see the new entry.
- **Add food** — three ways:
  - **Search** the curated local database (~90 common Indian + Western foods,
    every rice / dal / grain labelled cooked vs. dry). Anything you've logged
    before is ranked to the top (most-recent first, with a "N×" badge),
    including past free-text "quick add" entries — tap one to re-log it with the
    same numbers. Matching is word-order tolerant ("rice basmati" finds
    "Basmati rice").
  - **Quick add** — free-text description + calories (+ optional protein / fiber).
  - **Ask** — type a food and portion ("2 boiled eggs", "1 plate chicken
    biryani", "30 g almonds"); the `calorie-estimator` agent (local Claude CLI)
    returns calories + macros with a **likely range**, a **confidence** chip
    (high / medium / low) and, when it's unsure, the one question that would
    tighten it. Adjust the calories if you know better, then **Log it** or
    **Save food** (as a custom entry). No API key — see
    [Nutrition assistant](#nutrition-assistant).
- **Add activity** — log **cardio** (name + minutes) or **strength** (name +
  sets / reps + optional load). Calories burned are estimated with the MET method
  (`kcal/min = MET × 3.5 × bodyweight / 200`; `activity.py` holds the MET table,
  including gym machines). Strength with no time uses `sets × 3.5` minutes.
  Bodyweight comes from your latest weigh-in (→ start weight → 70 kg). Type your
  own kcal to override — it then stops re-estimating. Exercise is credited back
  to the day: **remaining = goal − food + burned**, and the ring fills against
  `goal + burned`.
- **Profile** — sex, age, height, activity level, goal (lose / maintain / gain)
  and target rate. The daily calorie budget is **auto-calculated**
  (Mifflin–St Jeor BMR × activity factor = TDEE, minus a deficit from your target
  rate, floored at a safe minimum). A manual calorie / protein / fiber goal
  overrides the calc. The budget panel shows every step.
- **Body measurements** — weight, plus optional **waist** and **neck**, logged
  whenever you measure (never required). Waist + neck + height give an
  **estimated body-fat %** (US Navy circumference method) shown on Today and
  trended on History. A goal waist draws a target line on the waist chart.
- **History** — calories/day **stacked by meal** vs. the goal line (7 / 14 / 30 /
  90 days; the goal line rises on days you logged exercise), a "Calories by meal"
  breakdown (avg kcal/day and % of intake per meal, with a takeaway), plus
  weight / waist / body-fat trend charts. Average intake, days logged, days
  active.
- **PWA** — installable, `display: standalone`, offline app shell via a service
  worker. Add to home screen like the command deck.

The background is a faint flat-lay illustration (tomatoes on the vine, basil,
chili, garlic, rosemary, a board arc, a measuring tape, a dumbbell) — a seamless
400px tile, `static/bg.svg` for light and `static/bg-dark.svg` for dark. The
fade (~7–9%) is baked into the SVGs and they're a `background-image` layer on
`<body>` (a `::before` at `z-index:-1` renders *below* the body colour and is
invisible). Cards are opaque so it only reads as texture in the margins.

Layout is single-column on mobile (the wrappers are `display:contents`); at
≥940px Today becomes a two-column dashboard (ring + meals / activity + body),
History's charts go two-up, and `.wrap` widens to use the screen. Profile stays
narrow.

## Data

- One SQLite file: `data/calorie.db` (gitignored). Created and seeded on first
  boot from `data/foods_seed.csv` (~90 hand-verified common foods, all
  `source = 'curated'`).
- **Rebuilding the food list:** edit `data/foods_seed.csv`, then
  `venv/bin/python tools/reseed_foods.py` — it backs up the DB to `backups/`,
  wipes `foods`, and reloads from the CSV. Log history is untouched (entries
  carry their own numbers; new food ids continue above the old max).
- Schema is keyed by `profile_id` from day one. There is one profile (id 1) for
  now; a profile picker is a later addition with no migration needed.
- Log entries store their *computed* calories/protein/fiber, so later edits to a
  food row never rewrite history.
- `activity_entries` store the calories-burned figure used at log time; editing
  inputs re-estimates unless you set a manual number (`manual_kcal`).
- Weight keeps its own table (`weight_entries`) because the TDEE math depends on
  it. Other measurements go in `body_measurements` keyed by `kind`
  (`waist` / `neck` / `hip`), one value per (profile, date, kind). Additive
  columns/tables land via `db.MIGRATIONS` / `CREATE TABLE IF NOT EXISTS` on boot
  — no destructive migrations.

## Nutrition assistant

The food **Ask** tab runs the **`calorie-estimator` agent** through a local
Claude CLI in print mode — no API key, no network config. The agent owns the
prompt and the model (sonnet, set in its own definition), and reads a
trust-ordered food memory (`label` > `user` > `seeded`) before estimating.
`assistant.ask()` runs, with cwd forced to a temp dir so this project's context
isn't loaded:

```
claude -p '<query>' --agent calorie-estimator --output-format json \
  --allowedTools 'Read(//<memory_dir>/**)' --disallowedTools Write Edit \
  --add-dir <memory_dir>
```

- **Read-only against the agent's memory.** `Read` is allowed *only* inside
  `memory_dir`. A bare `--allowedTools Read` was tested (2026-10-02) and lets a
  crafted query read any file this user can and echo it back in
  `assumptions` — don't widen it. `Write`/`Edit` are explicitly denied (the
  CLI removes them from the session). Only the user's own CLI chat with the
  agent (`claude --agent calorie-estimator`, e.g. to ingest a package label)
  writes `FOODS.md`.
- **No `--model`** — the agent's definition sets it.
- The app's `foods` table and the agent's `FOODS.md` are **separate stores**:
  a food saved in the app is not visible to the agent (no sync).

Endpoint: `POST /api/nutrition/ask` `{"query": "..."}` → `{name, serving_desc,
serving_grams, calories, protein_g, fiber_g, carbs_g, fat_g, assumptions,
kcal_low, kcal_high, confidence, clarify}`. `confidence` is one of
`high`/`medium`/`low` (anything else → `null`); `kcal_low`/`kcal_high` are both
set or both `null`; `clarify` is a string or `null`. Non-food input — the agent
replies `{"error": "<reason>"}` — comes back as a **502** with that reason as
the message.

The **activity** Ask (`POST /api/activity/ask`) is unchanged: an inline
MET-table prompt on `model` (haiku).

Config block in `config.json` (defaults in `config.py`):

```json
"assistant": {
  "enabled": true, "command": "claude", "model": "haiku",
  "agent": "calorie-estimator", "memory_dir": "~/.claude/calorie-estimator",
  "timeout_seconds": 90
}
```

`model` applies to the activity Ask only. `enabled: false`, or the `command`
not being on `PATH`, hides both Ask tabs (endpoints → 503). If
`~/.claude/agents/<agent>.md` is missing, only the **food** Ask is hidden and
`/api/nutrition/ask` returns 503. A food estimate takes ~5–11 s and ~$0.02 on
sonnet; the agent's behaviour (prompt, memory rules, seeded-value threshold) is
changed in its own definition, not here.

## Open Food Facts (disabled)

The OFF integration (`offapi.py`, text search + barcode) is still in the code but
**off by default** (`config.json` → `openfoodfacts.enabled: false`) — its
per-100g-as-sold values were unreliable (raw vs. cooked, inconsistent servings).
The **Ask** tab replaces it. Flip `enabled: true` to bring the Search tab's OFF
results and the `/api/foods/barcode` route back.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.example.json config.json   # optional; sane defaults apply without it
./run.sh                             # starts in tmux on 127.0.0.1:5200
./cleanup.sh                         # stops it
```

`config.json` is gitignored. The app runs fine without it on the defaults in
`config.py`. Copy `config.example.json` to change the `timezone`, the
`assistant` block (see [Nutrition assistant](#nutrition-assistant)), or the
`openfoodfacts` block.

## When it was eaten (`eaten_at`)

Logging late shouldn't move the meal. The Add-food sheet has an **Eaten at**
time (prefilled with now) shared by every add path — search, one-tap re-log,
Quick add, Ask — and tapping a logged entry opens a small sheet to change it
(or "Use logged time" to clear it).

- `log_entries.eaten_at` (UTC ISO, nullable) was added by the boot migration
  (`db.MIGRATIONS`, 2026-10-01). `NULL` = eaten when logged, so readers use
  `eaten_at` else `created_at` (`db.eaten_time()`); old rows were never
  rewritten. Pre-migration backup: `backups/calorie.db.*.pre-eaten-at.bak`.
- The time is **only sent if you change it**; untouched = the log time, exactly
  as before. The browser composes the instant in its own timezone and sends
  `toISOString()`; the server refuses naive or future times (5 min slack).
- The picker chooses a clock time on the day being viewed. On a **past** day,
  times before 04:00 mean that night after midnight (a 00:30 snack logged under
  the day before), never a future time; a hint spells it out. Entries whose
  time falls on another day show the date, e.g. `1 Oct, 00:30`.
- Entries are listed in eaten order. pulse reads the same column for its
  timeline and Lab (coffee/dinner timing) and its own log sheet writes it too.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Today page |
| GET | `/history`, `/profile` | Trends / settings pages |
| GET | `/api/summary?date=YYYY-MM-DD` | Day totals, goal, remaining, meals, activity |
| GET | `/api/foods/search?q=` | Local food search (+ OFF if re-enabled) |
| GET | `/api/foods/barcode/<code>` | OFF product lookup (OFF off by default) |
| POST | `/api/nutrition/ask` | `{"query"}` → calories + macros + range/confidence/clarify via the `calorie-estimator` agent (503 if the agent is missing; 502 + reason for non-food) |
| POST | `/api/foods` | Create a custom food |
| POST | `/api/log` | Add a log entry (`mode`: `food` / `off` / `quick`; optional `eaten_at`) |
| PUT/DELETE | `/api/log/<id>` | Edit / remove an entry (`eaten_at`: ISO sets, `null` clears) |
| GET | `/api/activities/catalog` | Built-in activity list (name + kind) |
| POST | `/api/activity` | Log activity (`kind`: `cardio` / `strength` / `other`) |
| PUT/DELETE | `/api/activity/<id>` | Edit / remove an activity |
| GET | `/api/history?days=N` | Per-day calories vs goal, burned, weight series |
| GET/POST | `/api/weight`, DELETE `/api/weight/<id>` | Weight log |
| GET/POST | `/api/measure/<kind>` (`waist`/`neck`), DELETE `/api/measure/<id>` | Body measurement log |
| GET/POST | `/api/profile` | Profile + computed targets |
| GET | `/manifest.webmanifest`, `/sw.js` | PWA |

## Tailnet routing

Served behind `tailscale serve --set-path=/calorie-tracker`, which strips the
prefix before the request arrives. `PrefixMiddleware` sets `SCRIPT_NAME` so
`url_for()` emits correct `/calorie-tracker/...` links. The front end reads
`request.script_root` (`window.BASE`) and prefixes every `fetch()`.

```bash
sudo tailscale serve --bg --set-path=/calorie-tracker 5200
```
