"""
Deliverability Warm-up Planner -- data layer.

Owns warmup.db (its own SQLite file, matching this codebase's convention of
one small file-based DB per feature area -- see gpt_service.py, spamhaus
tables in deliverability_history.db). Handles plan CRUD and the day-by-day
curve/schedule generation. Signal evaluation and recommendations live in
warmup_decision_engine.py, kept separate so the pure planning math here stays
independently testable from the parts that need live data.
"""
import sqlite3
import json
import csv
import math
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from data_paths import data_path
import warmup_thresholds as cfg

WARMUP_DB_PATH = data_path('warmup.db')

VALID_WARMING_TYPES = {
    'new_ip', 'new_domain', 'new_ip_and_domain', 'esp_migration', 'infrastructure_change'
}
VALID_STRATEGIES = {'conservative', 'balanced', 'aggressive'}
VALID_STATUSES = {'active', 'paused', 'completed', 'aborted'}
VALID_AUDIENCE_QUALITY = {
    'highly_engaged', 'recently_engaged', 'active_subscribers',
    'purchasers', 'mixed', 'lapsed', 'inactive'
}


def initialize_database():
    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warmup_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            warming_type TEXT NOT NULL,
            esp TEXT,
            domain TEXT NOT NULL,
            subdomain TEXT,
            region TEXT,
            old_ip TEXT,
            new_ip TEXT,
            current_volume INTEGER DEFAULT 0,
            target_volume INTEGER NOT NULL,
            start_date DATE NOT NULL,
            target_date DATE NOT NULL,
            strategy TEXT NOT NULL DEFAULT 'balanced',
            isp_mix_json TEXT NOT NULL,
            audience_quality TEXT NOT NULL DEFAULT 'highly_engaged',
            status TEXT NOT NULL DEFAULT 'active',
            duration_extended BOOLEAN DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warmup_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            day_number INTEGER NOT NULL,
            scheduled_date DATE NOT NULL,
            planned_total INTEGER NOT NULL,
            planned_gmail INTEGER DEFAULT 0,
            planned_microsoft INTEGER DEFAULT 0,
            planned_yahoo INTEGER DEFAULT 0,
            planned_apple INTEGER DEFAULT 0,
            planned_other INTEGER DEFAULT 0,
            recommended_audience TEXT,
            UNIQUE(plan_id, day_number)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warmup_actuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            actual_date DATE NOT NULL,
            actual_total INTEGER,
            actual_gmail INTEGER,
            actual_microsoft INTEGER,
            actual_yahoo INTEGER,
            actual_apple INTEGER,
            actual_other INTEGER,
            delivery_rate REAL,
            hard_bounce_rate REAL,
            soft_bounce_rate REAL,
            spam_rate REAL,
            unsub_rate REAL,
            open_rate REAL,
            click_rate REAL,
            source TEXT DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(plan_id, actual_date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warmup_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            event_date DATE NOT NULL,
            decision TEXT NOT NULL,
            health_score INTEGER,
            health_band TEXT,
            reasons_json TEXT,
            thresholds_version TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(plan_id, event_date)
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warmup_schedule_plan ON warmup_schedule(plan_id, day_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warmup_actuals_plan ON warmup_actuals(plan_id, actual_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warmup_events_plan ON warmup_events(plan_id, event_date)')

    conn.commit()
    conn.close()
    print(f'Warmup database initialized at {WARMUP_DB_PATH}')


# --------------------------------------------------------------------------
# Plan CRUD
# --------------------------------------------------------------------------

def _row_to_plan(row: sqlite3.Row) -> Dict:
    d = dict(row)
    d['isp_mix'] = json.loads(d.pop('isp_mix_json') or '{}')
    d['duration_extended'] = bool(d.get('duration_extended'))
    return d


def create_plan(data: Dict) -> Dict:
    """Create a plan and generate its schedule in one step.

    Raises ValueError on invalid input (caller -- the API layer -- should
    turn that into a 400). Never silently coerces bad input to a default.
    """
    name = (data.get('name') or '').strip()
    domain = (data.get('domain') or '').strip().lower()
    warming_type = data.get('warming_type')
    strategy = data.get('strategy', 'balanced')
    audience_quality = data.get('audience_quality', 'highly_engaged')
    isp_mix = data.get('isp_mix') or {}
    target_volume = data.get('target_volume')
    current_volume = data.get('current_volume', 0) or 0
    start_date_str = data.get('start_date') or date.today().isoformat()
    target_date_str = data.get('target_date')

    if not name:
        raise ValueError('name is required')
    if not domain:
        raise ValueError('domain is required')
    if warming_type not in VALID_WARMING_TYPES:
        raise ValueError(f'warming_type must be one of {sorted(VALID_WARMING_TYPES)}')
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f'strategy must be one of {sorted(VALID_STRATEGIES)}')
    if audience_quality not in VALID_AUDIENCE_QUALITY:
        raise ValueError(f'audience_quality must be one of {sorted(VALID_AUDIENCE_QUALITY)}')
    if not target_volume or target_volume <= 0:
        raise ValueError('target_volume must be a positive number')
    if not target_date_str:
        raise ValueError('target_date is required')

    validate_isp_mix(isp_mix)

    start = date.fromisoformat(start_date_str)
    target = date.fromisoformat(target_date_str)
    if target <= start:
        raise ValueError('target_date must be after start_date')

    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO warmup_plans
            (name, warming_type, esp, domain, subdomain, region, old_ip, new_ip,
             current_volume, target_volume, start_date, target_date, strategy,
             isp_mix_json, audience_quality, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
    ''', (
        name, warming_type, data.get('esp'), domain, data.get('subdomain'), data.get('region'),
        data.get('old_ip'), data.get('new_ip'), current_volume, target_volume,
        start.isoformat(), target.isoformat(), strategy, json.dumps(isp_mix),
        audience_quality, data.get('notes'),
    ))
    plan_id = cursor.lastrowid
    conn.commit()
    conn.close()

    generate_schedule(plan_id)
    return get_plan(plan_id)


def validate_isp_mix(isp_mix: Dict) -> None:
    if not isp_mix:
        raise ValueError('isp_mix is required')
    unknown = set(isp_mix) - set(cfg.ISP_BUCKETS)
    if unknown:
        raise ValueError(f'isp_mix has unknown buckets: {sorted(unknown)} (expected {cfg.ISP_BUCKETS})')
    total = sum(float(v) for v in isp_mix.values())
    if abs(total - 100.0) > 0.5:
        raise ValueError(f'isp_mix must sum to 100 (got {total})')


def get_plan(plan_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(WARMUP_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM warmup_plans WHERE id = ?', (plan_id,))
    row = cursor.fetchone()
    conn.close()
    return _row_to_plan(row) if row else None


def list_plans(status: Optional[str] = None) -> List[Dict]:
    conn = sqlite3.connect(WARMUP_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if status:
        cursor.execute('SELECT * FROM warmup_plans WHERE status = ? ORDER BY created_at DESC', (status,))
    else:
        cursor.execute('SELECT * FROM warmup_plans ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_plan(r) for r in rows]


def update_plan_status(plan_id: int, status: str) -> Dict:
    if status not in VALID_STATUSES:
        raise ValueError(f'status must be one of {sorted(VALID_STATUSES)}')
    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE warmup_plans SET status = ?, updated_at = ? WHERE id = ?',
        (status, datetime.utcnow(), plan_id)
    )
    conn.commit()
    conn.close()
    return get_plan(plan_id)


def update_plan_config(plan_id: int, data: Dict) -> Dict:
    """Update a plan's ramp configuration (target volume/date, strategy, ISP
    mix) and regenerate its schedule from today's actual-to-date position.
    Fields not present in `data` are left unchanged.
    """
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError(f'No warmup plan with id {plan_id}')

    strategy = data.get('strategy', plan['strategy'])
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f'strategy must be one of {sorted(VALID_STRATEGIES)}')
    isp_mix = data.get('isp_mix', plan['isp_mix'])
    validate_isp_mix(isp_mix)
    target_volume = data.get('target_volume', plan['target_volume'])
    if not target_volume or target_volume <= 0:
        raise ValueError('target_volume must be a positive number')
    target_date_str = data.get('target_date', plan['target_date'])
    target = date.fromisoformat(target_date_str)
    start = date.fromisoformat(plan['start_date'])
    if target <= start:
        raise ValueError('target_date must be after start_date')

    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE warmup_plans
        SET target_volume = ?, target_date = ?, strategy = ?, isp_mix_json = ?,
            duration_extended = 0, updated_at = ?
        WHERE id = ?
    ''', (target_volume, target.isoformat(), strategy, json.dumps(isp_mix), datetime.utcnow(), plan_id))
    conn.commit()
    conn.close()

    generate_schedule(plan_id)
    return get_plan(plan_id)


# --------------------------------------------------------------------------
# Curve generation
# --------------------------------------------------------------------------

def _split_by_isp(total: int, isp_mix: Dict) -> Dict[str, int]:
    """Split `total` across ISP buckets by isp_mix percentages.

    Rounding remainder is assigned to 'other' so the parts always sum
    exactly to `total` -- a naive per-bucket round() can drift by a few
    messages, which would look like a bug on a dashboard showing totals.
    """
    parts = {}
    running = 0
    buckets = [b for b in cfg.ISP_BUCKETS if b != 'other']
    for bucket in buckets:
        pct = float(isp_mix.get(bucket, 0))
        val = round(total * pct / 100)
        parts[bucket] = val
        running += val
    parts['other'] = max(total - running, 0)
    return parts


def _audience_for_day(day_number: int, duration_days: int, override_start: Optional[str]) -> str:
    fraction = (day_number - 1) / max(duration_days - 1, 1)
    sequence = cfg.AUDIENCE_STAGE_SEQUENCE
    if override_start:
        # Start at the user's chosen stage, otherwise follow the same
        # progression from wherever that stage sits in the sequence.
        labels = [s[1] for s in sequence]
        start_idx = next(
            (i for i, s in enumerate(sequence) if s[1].lower().startswith(override_start.replace('_', ' ')[:8])),
            0
        )
        sequence = sequence[start_idx:]
        if not sequence:
            return labels[-1]
    stage = sequence[0][1]
    for threshold, label in sequence:
        if fraction >= threshold:
            stage = label
    return stage


def compute_curve(current_volume: int, target_volume: int, start: date, target: date, strategy: str):
    """Generate the day-by-day total-volume ramp.

    Fits a smooth exponential curve that reaches exactly target_volume on
    the final day, capped by the strategy's max daily growth rate. If
    hitting target_date would require growing faster than the strategy
    allows, the plan is extended (not silently made more aggressive) --
    the caller is told via `extended` so the UI can say so explicitly.

    Returns (days, extended, duration_days) where days is a list of
    {day_number, date, planned_total}.
    """
    # Inclusive of both endpoints, so the LAST scheduled day lands exactly on
    # target_date (not the day before it) -- a user picking a completion
    # date expects the ramp to conclude ON that date.
    duration_days = max((target - start).days + 1, 1)
    starting_volume = current_volume if current_volume >= cfg.MIN_STARTING_VOLUME else cfg.MIN_STARTING_VOLUME

    if starting_volume >= target_volume:
        return ([{
            'day_number': 1, 'date': start.isoformat(), 'planned_total': target_volume,
        }], False, 1)

    # The loop below performs (duration_days - 1) growth transitions (day 1
    # is the fixed starting_volume, day duration_days is forced to exactly
    # target_volume) -- so the exponent here MUST be transitions, not days,
    # or the computed rate undershoots what's actually needed and the final
    # forced jump can overshoot the growth cap on the last transition.
    transitions = max(duration_days - 1, 1)
    max_growth = cfg.STRATEGY_MAX_DAILY_GROWTH.get(strategy, 0.50)
    required_rate = (target_volume / starting_volume) ** (1.0 / transitions) - 1.0

    extended = False
    if required_rate > max_growth:
        needed_transitions = math.ceil(math.log(target_volume / starting_volume) / math.log(1 + max_growth))
        duration_days = max(needed_transitions + 1, duration_days)
        rate = max_growth
        extended = True
    else:
        rate = required_rate

    days = []
    volume = starting_volume
    for day_number in range(1, duration_days + 1):
        if day_number == 1:
            volume = starting_volume
        elif day_number == duration_days:
            volume = target_volume
        else:
            volume = min(volume * (1 + rate), target_volume)
        days.append({
            'day_number': day_number,
            'date': (start + timedelta(days=day_number - 1)).isoformat(),
            'planned_total': round(volume),
        })

    return days, extended, duration_days


def generate_schedule(plan_id: int) -> List[Dict]:
    """Compute and persist the full day-by-day schedule for a plan.

    Idempotent -- re-running replaces the existing schedule (e.g. after
    editing target volume/date), it does not append duplicates.
    """
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError(f'No warmup plan with id {plan_id}')

    start = date.fromisoformat(plan['start_date'])
    target = date.fromisoformat(plan['target_date'])
    days, extended, duration_days = compute_curve(
        plan['current_volume'], plan['target_volume'], start, target, plan['strategy']
    )

    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM warmup_schedule WHERE plan_id = ?', (plan_id,))
    for day in days:
        isp_split = _split_by_isp(day['planned_total'], plan['isp_mix'])
        audience = _audience_for_day(day['day_number'], duration_days, plan.get('audience_quality'))
        cursor.execute('''
            INSERT INTO warmup_schedule
                (plan_id, day_number, scheduled_date, planned_total,
                 planned_gmail, planned_microsoft, planned_yahoo, planned_apple, planned_other,
                 recommended_audience)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            plan_id, day['day_number'], day['date'], day['planned_total'],
            isp_split['gmail'], isp_split['microsoft'], isp_split['yahoo'], isp_split['apple'], isp_split['other'],
            audience,
        ))
        day['isp_split'] = isp_split
        day['recommended_audience'] = audience

    if extended and target.isoformat() != days[-1]['date']:
        cursor.execute(
            'UPDATE warmup_plans SET target_date = ?, duration_extended = 1, updated_at = ? WHERE id = ?',
            (days[-1]['date'], datetime.utcnow(), plan_id)
        )

    conn.commit()
    conn.close()
    return days


def get_schedule(plan_id: int) -> List[Dict]:
    conn = sqlite3.connect(WARMUP_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM warmup_schedule WHERE plan_id = ? ORDER BY day_number', (plan_id,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Actuals
# --------------------------------------------------------------------------

_ACTUAL_FIELDS = [
    'actual_total', 'actual_gmail', 'actual_microsoft', 'actual_yahoo', 'actual_apple', 'actual_other',
    'delivery_rate', 'hard_bounce_rate', 'soft_bounce_rate', 'spam_rate', 'unsub_rate', 'open_rate', 'click_rate',
]


def record_actual(plan_id: int, actual_date: str, data: Dict, source: str = 'manual') -> Dict:
    # Manual check-then-branch, not ON CONFLICT...DO UPDATE -- EC2's Python
    # links against SQLite 3.7.17 (confirmed during the GPT v2 migration),
    # which predates upsert support (needs 3.24+). See gpt_service.py's
    # store_domain_data for the same pattern/reasoning.
    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()
    values = [data.get(f) for f in _ACTUAL_FIELDS]
    cursor.execute(
        'SELECT 1 FROM warmup_actuals WHERE plan_id = ? AND actual_date = ?', (plan_id, actual_date)
    )
    exists = cursor.fetchone() is not None
    if exists:
        set_clause = ", ".join(f"{f} = ?" for f in _ACTUAL_FIELDS)
        cursor.execute(
            f'UPDATE warmup_actuals SET {set_clause}, source = ? WHERE plan_id = ? AND actual_date = ?',
            values + [source, plan_id, actual_date]
        )
    else:
        cursor.execute(f'''
            INSERT INTO warmup_actuals (plan_id, actual_date, {", ".join(_ACTUAL_FIELDS)}, source)
            VALUES (?, ?, {", ".join(["?"] * len(_ACTUAL_FIELDS))}, ?)
        ''', [plan_id, actual_date] + values + [source])
    conn.commit()
    conn.close()
    return get_actual(plan_id, actual_date)


def get_actual(plan_id: int, actual_date: str) -> Optional[Dict]:
    conn = sqlite3.connect(WARMUP_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM warmup_actuals WHERE plan_id = ? AND actual_date = ?', (plan_id, actual_date)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_actuals(plan_id: int) -> List[Dict]:
    conn = sqlite3.connect(WARMUP_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM warmup_actuals WHERE plan_id = ? ORDER BY actual_date', (plan_id,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def import_actuals_csv(plan_id: int, file_path: str) -> Dict:
    """Bulk-import actuals from a CSV, upserting per date.

    Expected columns: actual_date plus any of _ACTUAL_FIELDS (missing
    columns are left NULL for that row, not zeroed -- a blank complaint
    rate means "not provided", not "zero complaints").
    """
    imported = 0
    errors = []
    with open(file_path, newline='') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            actual_date = (row.get('actual_date') or '').strip()
            if not actual_date:
                errors.append(f'Row {i}: missing actual_date')
                continue
            try:
                date.fromisoformat(actual_date)
            except ValueError:
                errors.append(f'Row {i}: invalid actual_date {actual_date!r}')
                continue
            data = {}
            row_ok = True
            for field in _ACTUAL_FIELDS:
                raw = row.get(field)
                if raw not in (None, ''):
                    try:
                        data[field] = float(raw)
                    except ValueError:
                        errors.append(f'Row {i}: invalid value for {field}: {raw!r}')
                        row_ok = False
                        # Keep checking the rest of this row's fields so one
                        # CSV row surfaces all its problems in a single pass,
                        # not just the first -- but the row as a whole is
                        # rejected below, never partially imported with the
                        # bad field silently missing.
            if not row_ok:
                continue
            record_actual(plan_id, actual_date, data, source='csv')
            imported += 1
    return {'imported': imported, 'errors': errors}


# --------------------------------------------------------------------------
# Events (decision history -- written by warmup_decision_engine.py)
# --------------------------------------------------------------------------

def record_event(plan_id: int, event_date: str, decision: str, health_score: int,
                  health_band: str, reasons: List[Dict]) -> None:
    # Manual check-then-branch -- see record_actual() for why (SQLite 3.7 on EC2).
    conn = sqlite3.connect(WARMUP_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM warmup_events WHERE plan_id = ? AND event_date = ?', (plan_id, event_date))
    exists = cursor.fetchone() is not None
    params = (decision, health_score, health_band, json.dumps(reasons), cfg.THRESHOLDS_VERSION)
    if exists:
        cursor.execute('''
            UPDATE warmup_events SET decision = ?, health_score = ?, health_band = ?,
                reasons_json = ?, thresholds_version = ?
            WHERE plan_id = ? AND event_date = ?
        ''', params + (plan_id, event_date))
    else:
        cursor.execute('''
            INSERT INTO warmup_events (plan_id, event_date, decision, health_score, health_band, reasons_json, thresholds_version)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (plan_id, event_date) + params)
    conn.commit()
    conn.close()


def get_events(plan_id: int) -> List[Dict]:
    conn = sqlite3.connect(WARMUP_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM warmup_events WHERE plan_id = ? ORDER BY event_date', (plan_id,)
    )
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        d['reasons'] = json.loads(d.pop('reasons_json') or '[]')
        rows.append(d)
    conn.close()
    return rows


def get_latest_event(plan_id: int) -> Optional[Dict]:
    events = get_events(plan_id)
    return events[-1] if events else None


# Initialize database on module import (matches gpt_service.py convention).
initialize_database()
