"""
Send-volume drop detection for Pulsation ("Volume Drops" tab).

Reads the per-domain daily rows Pulsation already collects (daily_metrics) and
flags accounts / sending domains whose send volume fell for N consecutive
complete weeks or months.

Two things keep this honest:
  * Volume is compared as AVERAGE DAILY SEND over the days that were actually
    collected in each period. The daily collector has gaps (missing days), and
    comparing raw totals would make any week with a missing day look like a drop.
  * Only COMPLETE periods are compared (the current week/month is shown in the
    trend but never used to flag), so a half-finished month is not a "drop".
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from pulsation_service import DB_PATH, _attach_account_name, pct

# A period needs at least this many collected days to be trusted for detection.
_MIN_COVERAGE = {'weekly': 4, 'monthly': 20}
_HISTORY = {'weekly': 12, 'monthly': 12}


def _period_start(d: date, mode: str) -> date:
    if mode == 'weekly':
        return d - timedelta(days=d.weekday())          # Monday
    return d.replace(day=1)


def _period_end(start: date, mode: str) -> date:
    if mode == 'weekly':
        return start + timedelta(days=6)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def _period_label(start: date, mode: str) -> str:
    return start.strftime('%d %b') if mode == 'weekly' else start.strftime('%b %Y')


def _load_daily(since: date) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """
            SELECT report_date, from_domain,
                   GROUP_CONCAT(DISTINCT esp) AS esps,
                   SUM(sent) AS sent, SUM(delivered) AS delivered,
                   SUM(bounces) AS bounces, SUM(spam_report) AS spam_report
            FROM daily_metrics
            WHERE report_date >= ?
            GROUP BY report_date, from_domain
            """,
            conn, params=(since.isoformat(),))
    finally:
        conn.close()
    return df


def detect_volume_drops(mode: str = 'weekly', level: str = 'account', min_drop_pct: float = 10.0,
                        consecutive: int = 2, min_daily_avg: float = 5000.0,
                        q: str = '', direction: str = 'drop') -> Dict[str, Any]:
    """direction='drop' flags falling volume; 'spike' flags rising volume with the same rules."""
    spike = str(direction).lower().startswith('s')
    direction = 'spike' if spike else 'drop'
    mode = 'monthly' if str(mode).lower().startswith('m') else 'weekly'
    level = 'domain' if str(level).lower().startswith('d') else 'account'
    consecutive = max(1, min(int(consecutive), 6))
    threshold = max(0.0, float(min_drop_pct)) / 100.0
    history = max(_HISTORY[mode], consecutive + 1)

    # Load enough history for the chart plus the detection window.
    today = datetime.utcnow().date()
    span_days = history * (7 if mode == 'weekly' else 31) + 40
    df = _load_daily(today - timedelta(days=span_days))
    if df.empty:
        return {'mode': mode, 'level': level, 'direction': direction, 'periods': [], 'flagged': [],
                'evaluated': 0, 'note': 'No Pulsation data collected yet.'}

    df['d'] = pd.to_datetime(df['report_date']).dt.date
    last_data_day = max(df['d'])

    # Periods: the last `history` periods up to and including the current one.
    cur = _period_start(last_data_day, mode)
    starts = [cur]
    while len(starts) < history + 1:
        prev = starts[-1] - timedelta(days=1)
        starts.append(_period_start(prev, mode))
    starts = sorted(starts)[-(history + 1):]

    collected = set(df['d'].unique())
    periods = []
    for s in starts:
        e = _period_end(s, mode)
        days_in = (e - s).days + 1
        got = sum(1 for k in range(days_in) if (s + timedelta(days=k)) in collected)
        periods.append({
            'start': s.isoformat(), 'end': e.isoformat(), 'label': _period_label(s, mode),
            'days': days_in, 'collected_days': got,
            'complete': e <= last_data_day,
            'trusted': e <= last_data_day and got >= _MIN_COVERAGE[mode],
        })

    # Don't show periods from before Pulsation started collecting.
    while periods and periods[0]['collected_days'] == 0:
        periods.pop(0)

    df['period'] = df['d'].map(lambda d: _period_start(d, mode).isoformat())
    df = df[df['period'].isin([p['start'] for p in periods])]

    df.rename(columns={'from_domain': 'From_domain'}, inplace=True)
    df = _attach_account_name(df)
    key = 'account_name' if level == 'account' else 'From_domain'

    grouped = (df.groupby([key, 'period'], as_index=False)
                 .agg(sent=('sent', 'sum'), delivered=('delivered', 'sum'),
                      bounces=('bounces', 'sum'), spam_report=('spam_report', 'sum')))
    by_entity = {name: g.set_index('period') for name, g in grouped.groupby(key)}

    # Supporting detail: domains per account, ESPs, account per domain.
    domain_account = (df.groupby('From_domain')['account_name'].first().to_dict())
    esps_by: Dict[str, set] = {}
    for name, g in df.groupby(key):
        esps_by[name] = {e for v in g['esps'].dropna() for e in str(v).split(',') if e}

    complete = [p for p in periods if p['complete']]
    window = complete[-(consecutive + 1):]   # baseline + `consecutive` periods
    qn = (q or '').strip().lower()
    flagged: List[Dict[str, Any]] = []
    evaluated = 0

    def series_for(g):
        out = []
        for p in periods:
            row = g.loc[p['start']] if p['start'] in g.index else None
            sent = int(row['sent']) if row is not None else 0
            delivered = int(row['delivered']) if row is not None else 0
            bounces = int(row['bounces']) if row is not None else 0
            spam = int(row['spam_report']) if row is not None else 0
            days = p['collected_days'] or 0
            out.append({
                'start': p['start'], 'label': p['label'], 'complete': p['complete'],
                'trusted': p['trusted'], 'collected_days': days,
                'sent': sent, 'avg_daily': round(sent / days) if days else None,
                # No delivery events recorded (some ESP/domain combos) -> unknown, not 0%.
                'delivery_rate': round(pct(delivered, sent), 2) if sent and delivered else None,
                'bounce_rate': round(pct(bounces, sent), 2) if sent else None,
                'spam_rate': round(pct(spam, delivered), 4) if delivered else None,
            })
        return out

    if len(window) < consecutive + 1:
        return {'mode': mode, 'level': level, 'direction': direction, 'periods': periods, 'flagged': [],
                'evaluated': 0, 'note': f'Not enough complete {mode} periods yet.'}

    for name, g in by_entity.items():
        if level == 'account' and str(name).lower() == 'unmapped':
            continue  # a bucket of unrelated domains, not an account; see the domain view
        if qn and qn not in str(name).lower() and not (
                level == 'account' and any(qn in d.lower() for d, a in domain_account.items() if a == name)):
            continue
        series = series_for(g)
        by_start = {s['start']: s for s in series}
        win = [by_start[p['start']] for p in window]
        if any(w['avg_daily'] is None for w in win):
            continue
        base = win[0]['avg_daily']
        # Drops: the entity must have been sizeable at the start. Spikes: it must be sizeable
        # now (a sender ramping 2k -> 60k/day matters even though it started small).
        if (win[-1]['avg_daily'] if spike else base) < min_daily_avg:
            continue
        if spike and not base:
            continue  # growth from zero has no percentage; it shows up once it has a baseline
        evaluated += 1

        steps = []
        for a, b in zip(win, win[1:]):
            steps.append(round(100.0 * (b['avg_daily'] - a['avg_daily']) / a['avg_daily'], 1) if a['avg_daily'] else 0.0)
        if spike:
            if not all(st >= 100.0 * threshold for st in steps):
                continue
        elif not all(st <= -100.0 * threshold for st in steps):
            continue

        # How long the decline has really been running (may exceed `consecutive`).
        comp = [by_start[p['start']] for p in complete if by_start[p['start']]['avg_daily'] is not None]
        streak = 0
        for a, b in zip(reversed(comp[:-1]), reversed(comp[1:])):
            moved = (b['avg_daily'] > a['avg_daily'] * (1 + threshold)) if spike \
                else (b['avg_daily'] < a['avg_daily'] * (1 - threshold))
            if a['avg_daily'] and moved:
                streak += 1
            else:
                break

        latest = win[-1]['avg_daily']
        total_drop = round(100.0 * (latest - base) / base, 1)
        if spike:
            severity = 'surge' if total_drop >= 200 else 'strong' if total_drop >= 50 else 'moderate'
        else:
            severity = 'stopped' if latest < 0.05 * base else 'severe' if total_drop <= -50 else 'moderate'
        dr_base, dr_latest = win[0]['delivery_rate'], win[-1]['delivery_rate']

        item = {
            'name': name, 'level': level,
            'account': domain_account.get(name) if level == 'domain' else name,
            'esps': sorted(esps_by.get(name, [])),
            'baseline_avg_daily': base, 'latest_avg_daily': latest,
            'lost_per_day': base - latest, 'change_per_day': latest - base,
            'total_drop_pct': total_drop, 'total_change_pct': total_drop,
            'steps_pct': steps, 'streak': max(streak, consecutive), 'severity': severity,
            'window': [w['label'] for w in win],
            'delivery_rate_baseline': dr_base, 'delivery_rate_latest': dr_latest,
            'low_coverage': any(not p['trusted'] for p in window),
            'series': series,
        }
        if level == 'account':
            item['domains'] = _domain_breakdown(df, name, window, spike)
        flagged.append(item)

    order = {'stopped': 0, 'surge': 0, 'severe': 1, 'strong': 1, 'moderate': 2}
    flagged.sort(key=lambda x: (order[x['severity']], -abs(x['change_per_day'])))
    return {
        'mode': mode, 'level': level, 'direction': direction, 'periods': periods, 'window': [p['label'] for p in window],
        'last_data_day': last_data_day.isoformat(),
        'params': {'min_drop_pct': min_drop_pct, 'consecutive': consecutive, 'min_daily_avg': min_daily_avg},
        'evaluated': evaluated, 'flagged': flagged,
        'totals': {
            'flagged': len(flagged),
            'stopped': sum(1 for f in flagged if f['severity'] == 'stopped'),
            'severe': sum(1 for f in flagged if f['severity'] == 'severe'),
            'moderate': sum(1 for f in flagged if f['severity'] == 'moderate'),
            'surge': sum(1 for f in flagged if f['severity'] == 'surge'),
            'strong': sum(1 for f in flagged if f['severity'] == 'strong'),
            'lost_per_day': sum(f['lost_per_day'] for f in flagged),
            'change_per_day': sum(f['change_per_day'] for f in flagged),
        },
    }


def _domain_breakdown(df: pd.DataFrame, account: str, window: List[Dict[str, Any]],
                      spike: bool = False) -> List[Dict[str, Any]]:
    """Per-domain baseline vs latest average daily send for one account."""
    first, last = window[0], window[-1]
    sub = df[(df['account_name'] == account) & (df['period'].isin([first['start'], last['start']]))]
    out = []
    for dom, g in sub.groupby('From_domain'):
        b = int(g.loc[g['period'] == first['start'], 'sent'].sum())
        l_ = int(g.loc[g['period'] == last['start'], 'sent'].sum())
        b_avg = round(b / first['collected_days']) if first['collected_days'] else 0
        l_avg = round(l_ / last['collected_days']) if last['collected_days'] else 0
        out.append({'domain': dom, 'baseline_avg_daily': b_avg, 'latest_avg_daily': l_avg,
                    'change_pct': round(100.0 * (l_avg - b_avg) / b_avg, 1) if b_avg else None})
    out.sort(key=lambda r: (r['latest_avg_daily'] - r['baseline_avg_daily']), reverse=spike)
    return out
