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

- **Today** — date switcher, calorie ring (eaten vs. goal + remaining), protein
  and fiber bars, per-meal accordion (breakfast / lunch / dinner / snack),
  activity log, quick weight log. The meal and activity sections are collapsible
  accordions that all start **collapsed** — nothing opens on its own. Your
  manual expand/collapse is remembered for the visit, and the section you just
  logged to re-opens so you can see the new entry.
- **Add food** — three ways:
  - **Search** the curated local database (~150 common Indian + Western foods)
    *and* Open Food Facts live. Local hits first; OFF results are cached into the
    local DB on log so they are searchable offline next time.
  - **Quick add** — free-text description + calories (+ optional protein / fiber).
  - **Barcode** — look a product up on Open Food Facts by its barcode digits.
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
- **History** — calories/day vs. the goal line (7 / 14 / 30 / 90 days; the goal
  line rises on days you logged exercise), weight trend with a goal-weight line,
  average intake, days logged, days active.
- **PWA** — installable, `display: standalone`, offline app shell via a service
  worker. Add to home screen like the command deck.

The background is a faint tiled line-art pattern (`static/bg.svg`) behind the
content; cards stay opaque so it only reads as texture.

## Data

- One SQLite file: `data/calorie.db` (gitignored). Created and seeded on first
  boot from `data/foods_seed.csv`.
- Schema is keyed by `profile_id` from day one. There is one profile (id 1) for
  now; a profile picker is a later addition with no migration needed.
- Log entries store their *computed* calories/protein/fiber, so later edits to a
  food row never rewrite history.
- `activity_entries` store the calories-burned figure used at log time; editing
  inputs re-estimates unless you set a manual number (`manual_kcal`).

## Open Food Facts

- Text search: `https://search.openfoodfacts.org/search`
- Barcode: `https://world.openfoodfacts.org/api/v2/product/<code>.json`
- Every call is best-effort with a hard timeout (default 5s). Any failure —
  network, timeout, or OFF serving its "temporarily unavailable" HTML — falls
  back to the local database silently. Toggle it off in `config.json`.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.example.json config.json   # optional; sane defaults apply without it
./run.sh                             # starts in tmux on 127.0.0.1:5200
./cleanup.sh                         # stops it
```

`config.json` is gitignored (suite habit) but holds no secrets — Open Food Facts
is an unauthenticated public API. The app runs fine without it on the defaults in
`config.py`. Copy `config.example.json` if you want to change the `timezone` or
the `openfoodfacts` block (`enabled`, `timeout_seconds`, `user_agent`).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Today page |
| GET | `/history`, `/profile` | Trends / settings pages |
| GET | `/api/summary?date=YYYY-MM-DD` | Day totals, goal, remaining, meals, activity |
| GET | `/api/foods/search?q=` | Local + Open Food Facts search |
| GET | `/api/foods/barcode/<code>` | OFF product lookup |
| POST | `/api/foods` | Create a custom food |
| POST | `/api/log` | Add a log entry (`mode`: `food` / `off` / `quick`) |
| PUT/DELETE | `/api/log/<id>` | Edit / remove an entry |
| GET | `/api/activities/catalog` | Built-in activity list (name + kind) |
| POST | `/api/activity` | Log activity (`kind`: `cardio` / `strength` / `other`) |
| PUT/DELETE | `/api/activity/<id>` | Edit / remove an activity |
| GET | `/api/history?days=N` | Per-day calories vs goal, burned, weight series |
| GET/POST | `/api/weight`, DELETE `/api/weight/<id>` | Weight log |
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
