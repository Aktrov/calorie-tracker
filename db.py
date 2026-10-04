"""SQLite layer for calorie-tracker.

One database file at data/calorie.db. init_db() is idempotent: it creates the
schema, runs additive column migrations, seeds a single default profile, and
loads the curated food list from data/foods_seed.csv the first time.

The schema is keyed by profile_id from day one so a profile picker can be added
later without a migration. For now there is exactly one profile (id 1).
"""
import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "data" / "calorie.db"
SEED_CSV = PROJECT_ROOT / "data" / "foods_seed.csv"

ACTIVE_PROFILE_ID = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT    NOT NULL DEFAULT 'Me',
    sex                     TEXT    NOT NULL DEFAULT 'male',
    birth_year              INTEGER,
    height_cm               REAL,
    activity_level          TEXT    NOT NULL DEFAULT 'light',
    goal_type               TEXT    NOT NULL DEFAULT 'lose',
    target_rate_kg_per_week REAL    NOT NULL DEFAULT 0.5,
    start_weight_kg         REAL,
    goal_weight_kg          REAL,
    manual_calorie_goal     INTEGER,
    manual_protein_goal     INTEGER,
    manual_fiber_goal       INTEGER,
    created_at              TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS foods (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    brand         TEXT,
    source        TEXT    NOT NULL DEFAULT 'custom',   -- curated | off | custom
    off_code      TEXT,
    serving_desc  TEXT    NOT NULL DEFAULT '100 g',
    serving_grams REAL,
    calories      REAL    NOT NULL,                    -- per serving
    protein_g     REAL    NOT NULL DEFAULT 0,
    fiber_g       REAL    NOT NULL DEFAULT 0,
    carbs_g       REAL,
    fat_g         REAL,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_foods_name ON foods(name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_foods_off_code
    ON foods(off_code) WHERE off_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS log_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id),
    entry_date  TEXT    NOT NULL,                      -- YYYY-MM-DD, local
    meal        TEXT    NOT NULL DEFAULT 'snack',      -- breakfast|lunch|dinner|snack
    food_id     INTEGER REFERENCES foods(id),
    description TEXT    NOT NULL,
    servings    REAL    NOT NULL DEFAULT 1,
    calories    REAL    NOT NULL,                      -- computed total for the entry
    protein_g   REAL    NOT NULL DEFAULT 0,
    fiber_g     REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,                      -- when it was logged
    eaten_at    TEXT                                   -- when it was eaten (UTC ISO); NULL = created_at
);
CREATE INDEX IF NOT EXISTS idx_log_profile_date
    ON log_entries(profile_id, entry_date);

CREATE TABLE IF NOT EXISTS weight_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id),
    entry_date  TEXT    NOT NULL,
    weight_kg   REAL    NOT NULL,
    note        TEXT,
    created_at  TEXT    NOT NULL,
    UNIQUE(profile_id, entry_date)
);

-- Occasional body measurements other than weight (waist, neck, hip, …).
-- Weight keeps its own table because TDEE math depends on it; everything else
-- lives here keyed by `kind`. One value per (profile, date, kind); logging is
-- always optional.
CREATE TABLE IF NOT EXISTS body_measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id),
    entry_date  TEXT    NOT NULL,
    kind        TEXT    NOT NULL,           -- waist | neck | hip
    value_cm    REAL    NOT NULL,
    note        TEXT,
    created_at  TEXT    NOT NULL,
    UNIQUE(profile_id, entry_date, kind)
);
CREATE INDEX IF NOT EXISTS idx_measure_profile_kind_date
    ON body_measurements(profile_id, kind, entry_date);

CREATE TABLE IF NOT EXISTS activity_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES profiles(id),
    entry_date      TEXT    NOT NULL,                    -- YYYY-MM-DD, local
    kind            TEXT    NOT NULL DEFAULT 'cardio',   -- cardio | strength | other
    name            TEXT    NOT NULL,
    duration_min    REAL,
    sets            INTEGER,
    reps            INTEGER,
    weight_kg       REAL,                                -- load lifted, optional
    calories_burned REAL    NOT NULL DEFAULT 0,
    manual_kcal     INTEGER NOT NULL DEFAULT 0,          -- 1 = user overrode the estimate
    notes           TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_profile_date
    ON activity_entries(profile_id, entry_date);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Additive migrations: {table: {column: "TYPE ..."}}. Applied only when missing.
MIGRATIONS = {
    "profiles": {"goal_waist_cm": "REAL"},
    # When the food was actually eaten, if the user set it. NULL = eaten when
    # logged, so every reader uses eaten_at or created_at (see eaten_time()).
    "log_entries": {"eaten_at": "TEXT"},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ts(s):
    """ISO timestamp -> aware datetime, for ordering. Rows carry mixed offsets
    (UTC from this app, local from pulse's food log), so compare parsed values,
    never the raw strings. Naive values are treated as UTC."""
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def eaten_time(entry):
    """When a log entry was eaten: the user-set eaten_at, else when it was logged."""
    return entry.get("eaten_at") or entry.get("created_at")


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _run_migrations(conn):
    for table, cols in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _seed_default_profile(conn):
    row = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()
    if row["n"] == 0:
        conn.execute(
            "INSERT INTO profiles (id, name, created_at) VALUES (?, ?, ?)",
            (ACTIVE_PROFILE_ID, "Me", _now()),
        )


def _seed_foods(conn):
    row = conn.execute("SELECT COUNT(*) AS n FROM foods WHERE source = 'curated'").fetchone()
    if row["n"] > 0 or not SEED_CSV.exists():
        return
    with open(SEED_CSV, newline="") as f:
        reader = csv.DictReader(f)
        now = _now()
        for r in reader:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            conn.execute(
                """INSERT INTO foods
                   (name, brand, source, serving_desc, serving_grams,
                    calories, protein_g, fiber_g, carbs_g, fat_g, created_at)
                   VALUES (?, ?, 'curated', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    (r.get("brand") or "").strip() or None,
                    (r.get("serving_desc") or "100 g").strip(),
                    _f(r.get("serving_grams")),
                    _f(r.get("calories")) or 0.0,
                    _f(r.get("protein_g")) or 0.0,
                    _f(r.get("fiber_g")) or 0.0,
                    _f(r.get("carbs_g")),
                    _f(r.get("fat_g")),
                    now,
                ),
            )


def _f(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _run_migrations(conn)
        _seed_default_profile(conn)
        _seed_foods(conn)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
PROFILE_FIELDS = {
    "name", "sex", "birth_year", "height_cm", "activity_level", "goal_type",
    "target_rate_kg_per_week", "start_weight_kg", "goal_weight_kg", "goal_waist_cm",
    "manual_calorie_goal", "manual_protein_goal", "manual_fiber_goal",
}


def get_profile(profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_profile(fields, profile_id=ACTIVE_PROFILE_ID):
    clean = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    if not clean:
        return get_profile(profile_id)
    sets = ", ".join(f"{k} = ?" for k in clean)
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE profiles SET {sets} WHERE id = ?",
            (*clean.values(), profile_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_profile(profile_id)


# --------------------------------------------------------------------------
# Foods
# --------------------------------------------------------------------------
def _word_match(query, col_expr):
    """(sql_fragment, params) that is true when every whitespace-separated token
    of `query` appears (case-insensitive substring) in `col_expr`. Order- and
    gap-tolerant, so "beet fried" matches "Pan fried beetroot"."""
    words = [w for w in query.split() if w]
    if not words:
        return "0", []
    frag = " AND ".join(f"{col_expr} LIKE ? COLLATE NOCASE" for _ in words)
    return frag, [f"%{w}%" for w in words]


def search_foods(query, limit=20, profile_id=ACTIVE_PROFILE_ID):
    """Search the food table, ranking things this profile has actually logged
    to the top (most-recent first), then by how often. `query` is matched
    fuzzily: all of its words must appear somewhere in name+brand, any order."""
    q = query.strip()
    if not q:
        return []
    conn = get_conn()
    try:
        where_sql, where_params = _word_match(q, "(f.name || ' ' || COALESCE(f.brand, ''))")
        rows = conn.execute(
            f"""
            SELECT f.*,
                   COALESCE(u.use_count, 0) AS use_count,
                   u.last_used             AS last_used
            FROM foods f
            LEFT JOIN (
                SELECT food_id, COUNT(*) AS use_count, MAX(entry_date) AS last_used
                FROM log_entries
                WHERE profile_id = ? AND food_id IS NOT NULL
                GROUP BY food_id
            ) u ON u.food_id = f.id
            WHERE {where_sql}
            ORDER BY
                CASE WHEN u.use_count > 0 THEN 0 ELSE 1 END,
                u.last_used DESC,
                u.use_count DESC,
                CASE WHEN f.name LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
                CASE f.source WHEN 'curated' THEN 0 WHEN 'custom' THEN 1 ELSE 2 END,
                length(f.name), f.name
            LIMIT ?
            """,
            [profile_id, *where_params, f"{q}%", limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recent_quick_entries(query, limit=6, profile_id=ACTIVE_PROFILE_ID):
    """Past free-text ("quick add") log entries whose description fuzzily
    matches `query`, newest first, with the macros from their most recent use
    so they can be re-logged in one tap."""
    q = query.strip()
    if not q:
        return []
    conn = get_conn()
    try:
        where_sql, where_params = _word_match(q, "description")
        groups = conn.execute(
            f"""SELECT description, COUNT(*) AS use_count, MAX(entry_date) AS last_used
                FROM log_entries
                WHERE profile_id = ? AND food_id IS NULL AND {where_sql}
                GROUP BY description
                ORDER BY last_used DESC, use_count DESC
                LIMIT ?""",
            [profile_id, *where_params, limit],
        ).fetchall()
        out = []
        for g in groups:
            m = conn.execute(
                """SELECT calories, protein_g, fiber_g FROM log_entries
                   WHERE profile_id = ? AND food_id IS NULL AND description = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (profile_id, g["description"]),
            ).fetchone()
            out.append({
                "description": g["description"],
                "use_count": g["use_count"],
                "last_used": g["last_used"],
                "calories": round((m["calories"] if m else 0) or 0, 1),
                "protein_g": round((m["protein_g"] if m else 0) or 0, 1),
                "fiber_g": round((m["fiber_g"] if m else 0) or 0, 1),
            })
        return out
    finally:
        conn.close()


def get_food(food_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM foods WHERE id = ?", (food_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_food(data):
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO foods
               (name, brand, source, off_code, serving_desc, serving_grams,
                calories, protein_g, fiber_g, carbs_g, fat_g, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["name"], data.get("brand"), data.get("source", "custom"),
                data.get("off_code"), data.get("serving_desc", "100 g"),
                data.get("serving_grams"), data["calories"],
                data.get("protein_g", 0) or 0, data.get("fiber_g", 0) or 0,
                data.get("carbs_g"), data.get("fat_g"), _now(),
            ),
        )
        conn.commit()
        return get_food(cur.lastrowid)
    finally:
        conn.close()


def upsert_off_food(item):
    """Cache an Open Food Facts product into foods so it is searchable offline
    next time. The 'serving' for an OFF food is 100 g (values come per 100 g).
    Returns the foods row."""
    conn = get_conn()
    try:
        if item.get("off_code"):
            existing = conn.execute(
                "SELECT * FROM foods WHERE off_code = ?", (item["off_code"],)
            ).fetchone()
            if existing:
                return dict(existing)
        cur = conn.execute(
            """INSERT INTO foods
               (name, brand, source, off_code, serving_desc, serving_grams,
                calories, protein_g, fiber_g, carbs_g, fat_g, created_at)
               VALUES (?, ?, 'off', ?, '100 g', 100, ?, ?, ?, ?, ?, ?)""",
            (
                item["name"], item.get("brand"), item.get("off_code"),
                item.get("kcal_100g", 0) or 0, item.get("protein_100g", 0) or 0,
                item.get("fiber_100g", 0) or 0, item.get("carbs_100g"),
                item.get("fat_100g"), _now(),
            ),
        )
        conn.commit()
        return get_food(cur.lastrowid)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Log entries
# --------------------------------------------------------------------------
def add_log_entry(data, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO log_entries
               (profile_id, entry_date, meal, food_id, description,
                servings, calories, protein_g, fiber_g, created_at, eaten_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id, data["entry_date"], data["meal"], data.get("food_id"),
                data["description"], data["servings"], data["calories"],
                data.get("protein_g", 0) or 0, data.get("fiber_g", 0) or 0, _now(),
                data.get("eaten_at"),
            ),
        )
        conn.commit()
        return get_log_entry(cur.lastrowid)
    finally:
        conn.close()


def get_log_entry(entry_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM log_entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_log_entry(entry_id, fields, profile_id=ACTIVE_PROFILE_ID):
    allowed = {"meal", "servings", "calories", "protein_g", "fiber_g", "description", "eaten_at"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return get_log_entry(entry_id)
    sets = ", ".join(f"{k} = ?" for k in clean)
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE log_entries SET {sets} WHERE id = ? AND profile_id = ?",
            (*clean.values(), entry_id, profile_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_log_entry(entry_id)


def delete_log_entry(entry_id, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM log_entries WHERE id = ? AND profile_id = ?",
            (entry_id, profile_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def entries_for_date(entry_date, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT le.*, f.brand AS food_brand, f.serving_desc AS food_serving_desc,
                      f.serving_grams AS food_serving_grams
               FROM log_entries le
               LEFT JOIN foods f ON f.id = le.food_id
               WHERE le.profile_id = ? AND le.entry_date = ?
               ORDER BY le.created_at""",
            (profile_id, entry_date),
        ).fetchall()
        # In the order they were eaten (stable, so same-time items keep log order).
        return sorted((dict(r) for r in rows), key=lambda e: _ts(eaten_time(e)))
    finally:
        conn.close()


def daily_totals(start_date, end_date, profile_id=ACTIVE_PROFILE_ID):
    """{date: {calories, protein_g, fiber_g}} for dates in [start, end]."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT entry_date,
                      SUM(calories)  AS calories,
                      SUM(protein_g) AS protein_g,
                      SUM(fiber_g)   AS fiber_g
               FROM log_entries
               WHERE profile_id = ? AND entry_date BETWEEN ? AND ?
               GROUP BY entry_date""",
            (profile_id, start_date, end_date),
        ).fetchall()
        return {
            r["entry_date"]: {
                "calories": round(r["calories"] or 0, 1),
                "protein_g": round(r["protein_g"] or 0, 1),
                "fiber_g": round(r["fiber_g"] or 0, 1),
            }
            for r in rows
        }
    finally:
        conn.close()


def daily_meal_calories(start_date, end_date, profile_id=ACTIVE_PROFILE_ID):
    """{date: {meal: kcal}} for food logged in [start, end]."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT entry_date, meal, SUM(calories) AS kcal
               FROM log_entries
               WHERE profile_id = ? AND entry_date BETWEEN ? AND ?
               GROUP BY entry_date, meal""",
            (profile_id, start_date, end_date),
        ).fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["entry_date"], {})[r["meal"]] = round(r["kcal"] or 0, 1)
        return out
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Activity entries
# --------------------------------------------------------------------------
def add_activity_entry(data, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO activity_entries
               (profile_id, entry_date, kind, name, duration_min, sets, reps,
                weight_kg, calories_burned, manual_kcal, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id, data["entry_date"], data["kind"], data["name"],
                data.get("duration_min"), data.get("sets"), data.get("reps"),
                data.get("weight_kg"), data.get("calories_burned", 0) or 0,
                1 if data.get("manual_kcal") else 0, data.get("notes"), _now(),
            ),
        )
        conn.commit()
        return get_activity_entry(cur.lastrowid)
    finally:
        conn.close()


def get_activity_entry(entry_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM activity_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_activity_entry(entry_id, fields, profile_id=ACTIVE_PROFILE_ID):
    allowed = {
        "kind", "name", "duration_min", "sets", "reps", "weight_kg",
        "calories_burned", "manual_kcal", "notes",
    }
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return get_activity_entry(entry_id)
    setclause = ", ".join(f"{k} = ?" for k in clean)
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE activity_entries SET {setclause} WHERE id = ? AND profile_id = ?",
            (*clean.values(), entry_id, profile_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_activity_entry(entry_id)


def delete_activity_entry(entry_id, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM activity_entries WHERE id = ? AND profile_id = ?",
            (entry_id, profile_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def activities_for_date(entry_date, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM activity_entries
               WHERE profile_id = ? AND entry_date = ?
               ORDER BY created_at""",
            (profile_id, entry_date),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def activities_between(start_date, end_date, profile_id=ACTIVE_PROFILE_ID):
    """{date: [activity rows]} for dates in [start, end]."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM activity_entries
               WHERE profile_id = ? AND entry_date BETWEEN ? AND ?
               ORDER BY created_at""",
            (profile_id, start_date, end_date),
        ).fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["entry_date"], []).append(dict(r))
        return out
    finally:
        conn.close()




# --------------------------------------------------------------------------
# Weight entries
# --------------------------------------------------------------------------
def upsert_weight(entry_date, weight_kg, note=None, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO weight_entries
               (profile_id, entry_date, weight_kg, note, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, entry_date) DO UPDATE SET
                 weight_kg = excluded.weight_kg,
                 note      = excluded.note""",
            (profile_id, entry_date, weight_kg, note, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_weight(entry_id, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM weight_entries WHERE id = ? AND profile_id = ?",
            (entry_id, profile_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def weight_series(profile_id=ACTIVE_PROFILE_ID, limit=400):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, entry_date, weight_kg, note FROM weight_entries
               WHERE profile_id = ? ORDER BY entry_date""",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows][-limit:]
    finally:
        conn.close()


def latest_weight(on_or_before=None, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        if on_or_before:
            row = conn.execute(
                """SELECT weight_kg FROM weight_entries
                   WHERE profile_id = ? AND entry_date <= ?
                   ORDER BY entry_date DESC LIMIT 1""",
                (profile_id, on_or_before),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT weight_kg FROM weight_entries
                   WHERE profile_id = ? ORDER BY entry_date DESC LIMIT 1""",
                (profile_id,),
            ).fetchone()
        return row["weight_kg"] if row else None
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Body measurements (waist / neck / …) — same optional cadence as weight
# --------------------------------------------------------------------------
MEASUREMENT_KINDS = ("waist", "neck", "hip")


def upsert_measurement(kind, entry_date, value_cm, note=None, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO body_measurements
               (profile_id, entry_date, kind, value_cm, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, entry_date, kind) DO UPDATE SET
                 value_cm = excluded.value_cm,
                 note     = excluded.note""",
            (profile_id, entry_date, kind, value_cm, note, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_measurement(entry_id, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM body_measurements WHERE id = ? AND profile_id = ?",
            (entry_id, profile_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def measurement_series(kind, profile_id=ACTIVE_PROFILE_ID, limit=400):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, entry_date, value_cm, note FROM body_measurements
               WHERE profile_id = ? AND kind = ? ORDER BY entry_date""",
            (profile_id, kind),
        ).fetchall()
        return [dict(r) for r in rows][-limit:]
    finally:
        conn.close()


def latest_measurement(kind, on_or_before=None, profile_id=ACTIVE_PROFILE_ID):
    conn = get_conn()
    try:
        if on_or_before:
            row = conn.execute(
                """SELECT value_cm FROM body_measurements
                   WHERE profile_id = ? AND kind = ? AND entry_date <= ?
                   ORDER BY entry_date DESC LIMIT 1""",
                (profile_id, kind, on_or_before),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT value_cm FROM body_measurements
                   WHERE profile_id = ? AND kind = ?
                   ORDER BY entry_date DESC LIMIT 1""",
                (profile_id, kind),
            ).fetchone()
        return row["value_cm"] if row else None
    finally:
        conn.close()
