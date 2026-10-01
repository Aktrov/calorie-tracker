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
    biryani", "30 g almonds"); a local Claude CLI returns calories + macros,
    which you can **Log it** or **Save food** (as a custom entry). No API key —
    see [Nutrition assistant](#nutrition-assistant).
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

The **Ask** tab shells out to a local Claude CLI in print mode — no API key, no
network config. `assistant.py` runs
`claude -p '<prompt>' --output-format json --model <model>` (cwd forced to a
temp dir so it doesn't load this project's context), parses the JSON reply, and
returns `{name, serving_desc, serving_grams, calories, protein_g, fiber_g,
carbs_g, fat_g, assumptions}`. Endpoint: `POST /api/nutrition/ask`
`{"query": "..."}`.

Config block in `config.json` (defaults in `config.py`):

```json
"assistant": { "enabled": true, "command": "claude", "model": "haiku", "timeout_seconds": 60 }
```

`enabled: false`, or the `command` not being on `PATH`, hides the tab and makes
the endpoint return 503. Each lookup takes a few seconds and costs whatever the
CLI's model call costs.

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

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Today page |
| GET | `/history`, `/profile` | Trends / settings pages |
| GET | `/api/summary?date=YYYY-MM-DD` | Day totals, goal, remaining, meals, activity |
| GET | `/api/foods/search?q=` | Local food search (+ OFF if re-enabled) |
| GET | `/api/foods/barcode/<code>` | OFF product lookup (OFF off by default) |
| POST | `/api/nutrition/ask` | `{"query"}` → calories + macros via the local Claude CLI |
| POST | `/api/foods` | Create a custom food |
| POST | `/api/log` | Add a log entry (`mode`: `food` / `off` / `quick`) |
| PUT/DELETE | `/api/log/<id>` | Edit / remove an entry |
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
