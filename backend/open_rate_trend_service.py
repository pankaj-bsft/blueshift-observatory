"""
Unique-open-rate trend detection for Pulsation ("Open Rate Trends" tab).

Same idea as volume_trend_service, applied to unique open rate:
flags accounts / sending domains whose unique open % fell (or rose) by at least
N percentage points in each of N consecutive complete weeks or months.

  * Open rate per period = total unique opens / total delivered over that period
    (not an average of daily rates), so a tiny day can't swing it.
  * Only rows that actually carry open data are counted. Unique opens were not
    backfilled (collection started 2026-10-25), so earlier days are "not tracked",
    and rows with no delivery events are skipped (no denominator).
  * Changes are in percentage POINTS (30% -> 24% = -6 pts), not relative %.
  * Only complete periods with enough collected days are compared.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd

from pulsation_service import DB_PATH, _attach_account_name, pct
from volume_trend_service import (_HISTORY, _MAX_MATCHES, _MIN_COVERAGE, _MIN_PARTIAL, _period_end,
                                  _period_label, _period_start)


def _load_daily(since) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        return pd.read_sql_query(
            """
            SELECT report_date, from_domain,
                   GROUP_CONCAT(DISTINCT esp) AS esps,
                   SUM(sent) AS sent, SUM(delivered) AS delivered,
                   SUM(unique_user_open) AS opens, SUM(unique_user_click) AS clicks
            FROM daily_metrics
            WHERE report_date >= ? AND unique_user_open IS NOT NULL AND delivered > 0
            GROUP BY report_date, from_domain
            """,
            conn, params=(since.isoformat(),))
    finally:
        conn.close()


def _severity(total_pts: float, spike: bool) -> str:
    a = abs(total_pts)
    if spike:
        return 'surge' if a >= 10 else 'strong' if a >= 5 else 'moderate'
    return 'severe' if a >= 10 else 'high' if a >= 5 else 'moderate'


def detect_open_rate_trends(mode: str = 'weekly', level: str = 'account', min_change_pts: float = 2.0,
                            consecutive: int = 2, min_daily_delivered: float = 5000.0,
                            q: str = '', direction: str = 'drop', include_current: bool = True) -> Dict[str, Any]:
    """include_current: compare the week / month in progress (open rate so far) as the latest point.
    q: with a search term, every matching account / domain is returned (non-flagged ones in `matches`)."""
    spike = str(direction).lower().startswith('s')
    direction = 'spike' if spike else 'drop'
    mode = 'monthly' if str(mode).lower().startswith('m') else 'weekly'
    level = 'domain' if str(level).lower().startswith('d') else 'account'
    consecutive = max(1, min(int(consecutive), 6))
    threshold = max(0.0, float(min_change_pts))
    history = max(_HISTORY[mode], consecutive + 1)
    qn = (q or '').strip().lower()
    params = {'min_change_pts': threshold, 'consecutive': consecutive, 'min_daily_delivered': min_daily_delivered,
              'include_current': bool(include_current)}
    base_resp = {'mode': mode, 'level': level, 'direction': direction, 'params': params, 'q': qn,
                 'periods': [], 'window': [], 'flagged': [], 'matches': [], 'matches_total': 0, 'evaluated': 0}

    today = datetime.utcnow().date()
    span_days = history * (7 if mode == 'weekly' else 31) + 40
    df = _load_daily(today - timedelta(days=span_days))
    if df.empty:
        return {**base_resp, 'note': 'No unique-open data collected yet.'}

    df['d'] = pd.to_datetime(df['report_date']).dt.date
    last_data_day = max(df['d'])
    first_data_day = min(df['d'])

    cur = _period_start(last_data_day, mode)
    starts = [cur]
    while len(starts) < history + 1:
        starts.append(_period_start(starts[-1] - timedelta(days=1), mode))
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
    # Drop leading periods with no / too little open data (e.g. the 1-day week tracking started in):
    # a rate from one day would dominate the chart scale and is never compared anyway.
    while len(periods) > 1 and (periods[0]['collected_days'] == 0
                                or (periods[0]['complete'] and not periods[0]['trusted'])):
        periods.pop(0)

    df['period'] = df['d'].map(lambda d: _period_start(d, mode).isoformat())
    df = df[df['period'].isin([p['start'] for p in periods])]
    df = df.rename(columns={'from_domain': 'From_domain'})
    df = _attach_account_name(df)
    key = 'account_name' if level == 'account' else 'From_domain'

    grouped = (df.groupby([key, 'period'], as_index=False)
                 .agg(sent=('sent', 'sum'), delivered=('delivered', 'sum'),
                      opens=('opens', 'sum'), clicks=('clicks', 'sum')))
    by_entity = {name: g.set_index('period') for name, g in grouped.groupby(key)}
    domain_account = df.groupby('From_domain')['account_name'].first().to_dict()
    esps_by: Dict[str, set] = {}
    for name, g in df.groupby(key):
        esps_by[name] = {e for v in g['esps'].dropna() for e in str(v).split(',') if e}

    # Only trusted (complete, well-covered) periods are compared.
    trusted = [p for p in periods if p['trusted']]
    cur_p = periods[-1] if periods and not periods[-1]['complete'] else None
    if include_current and cur_p and cur_p['collected_days'] >= _MIN_PARTIAL[mode]:
        trusted = trusted + [cur_p]   # open rate so far is a ratio, comparable to a full period's
    else:
        cur_p = None
    window = trusted[-(consecutive + 1):]
    base_resp.update({'periods': periods, 'window': [p['label'] for p in window],
                      'window_partial': bool(cur_p) and bool(window) and window[-1] is cur_p,
                      'current_period': ({'label': cur_p['label'], 'collected_days': cur_p['collected_days'],
                                          'days': cur_p['days']} if cur_p else None),
                      'last_data_day': last_data_day.isoformat(),
                      'tracked_since': first_data_day.isoformat()})
    # The window must be the most recent trusted periods back to back (no gap).
    contiguous = len(window) == consecutive + 1 and all(
        _period_start(datetime.fromisoformat(b['start']).date() - timedelta(days=1), mode).isoformat() == a['start']
        for a, b in zip(window, window[1:]))
    if not contiguous:
        unit = 'weeks' if mode == 'weekly' else 'months'
        return {**base_resp, 'window': [], 'window_partial': False,
                'note': f'Not enough open-rate history yet: needs {consecutive + 1} complete {unit} '
                        f'(unique opens tracked since {first_data_day.isoformat()}).'}

    def series_for(g):
        out = []
        for p in periods:
            row = g.loc[p['start']] if p['start'] in g.index else None
            delivered = int(row['delivered']) if row is not None else 0
            opens = int(row['opens']) if row is not None else 0
            clicks = int(row['clicks']) if row is not None and pd.notna(row['clicks']) else 0
            days = p['collected_days'] or 0
            out.append({
                'start': p['start'], 'label': p['label'], 'complete': p['complete'],
                'trusted': p['trusted'], 'collected_days': days,
                'sent': int(row['sent']) if row is not None else 0,
                'delivered': delivered, 'opens': opens, 'clicks': clicks,
                'avg_daily_delivered': round(delivered / days) if days else None,
                'open_rate': round(pct(opens, delivered), 2) if delivered else None,
                'click_rate': round(pct(clicks, delivered), 2) if delivered else None,
            })
        return out

    def matches_q(name) -> bool:
        if not qn or qn in str(name).lower():
            return True
        if level == 'account':
            return any(qn in d.lower() for d, a in domain_account.items() if a == name)
        return qn in str(domain_account.get(name) or '').lower()

    flagged: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []
    evaluated = 0
    for name, g in by_entity.items():
        if level == 'account' and str(name).lower() == 'unmapped' and not qn:
            continue
        if not matches_q(name):
            continue
        series = series_for(g)
        by_start = {s['start']: s for s in series}
        win = [by_start[p['start']] for p in window]
        item = {
            'name': name, 'level': level,
            'account': domain_account.get(name) if level == 'domain' else name,
            'esps': sorted(esps_by.get(name, [])), 'series': series,
            'flagged': False, 'severity': None, 'status': 'ok',
            'window': [w['label'] for w in win], 'window_partial': not win[-1]['complete'],
        }
        if any(w['open_rate'] is None for w in win):
            item['status'] = 'no_data'
            if qn:
                others.append(item)
            continue
        steps = [round(b['open_rate'] - a['open_rate'], 2) for a, b in zip(win, win[1:])]
        base, latest = win[0]['open_rate'], win[-1]['open_rate']
        total_pts = round(latest - base, 2)
        item.update({
            'baseline_open_rate': base, 'latest_open_rate': latest,
            'total_change_pts': total_pts,
            'relative_change_pct': round(100.0 * (latest - base) / base, 1) if base else None,
            'steps_pts': steps,
            'baseline_avg_daily_delivered': win[0]['avg_daily_delivered'],
            'latest_avg_daily_delivered': win[-1]['avg_daily_delivered'],
            'click_rate_baseline': win[0]['click_rate'], 'click_rate_latest': win[-1]['click_rate'],
        })
        # Same rule as Volume Trends: drops need real volume at the start, spikes at the end.
        if ((win[-1] if spike else win[0])['avg_daily_delivered'] or 0) < min_daily_delivered:
            item['status'] = 'low_volume'
            if qn:
                others.append(item)
            continue
        evaluated += 1

        if (not all(st >= threshold for st in steps)) if spike else (not all(st <= -threshold for st in steps)):
            if qn:
                others.append(item)
            continue

        comp = [by_start[p['start']] for p in trusted if by_start[p['start']]['open_rate'] is not None]
        streak = 0
        for a, b in zip(reversed(comp[:-1]), reversed(comp[1:])):
            moved = (b['open_rate'] - a['open_rate'] >= threshold) if spike \
                else (a['open_rate'] - b['open_rate'] >= threshold)
            if moved:
                streak += 1
            else:
                break
        item.update({'flagged': True, 'status': 'flagged', 'severity': _severity(total_pts, spike),
                     'streak': max(streak, consecutive)})
        if level == 'account':
            item['domains'] = _domain_breakdown(df, name, window, spike)
        flagged.append(item)

    matches_total = len(others)
    if qn:
        others.sort(key=lambda x: (x.get('total_change_pts') is None,
                                   -(x.get('total_change_pts') or 0) if spike else (x.get('total_change_pts') or 0)))
        others = others[:_MAX_MATCHES]
        if level == 'account':
            for o in others:
                if o.get('baseline_open_rate') is not None:
                    o['domains'] = _domain_breakdown(df, o['name'], window, spike)

    order = {'severe': 0, 'surge': 0, 'high': 1, 'strong': 1, 'moderate': 2}
    flagged.sort(key=lambda x: (order[x['severity']], -abs(x['total_change_pts'])))
    sev_keys = ('surge', 'strong', 'moderate') if spike else ('severe', 'high', 'moderate')
    totals = {'flagged': len(flagged), **{k: sum(1 for f in flagged if f['severity'] == k) for k in sev_keys}}
    totals['avg_change_pts'] = round(sum(f['total_change_pts'] for f in flagged) / len(flagged), 2) if flagged else 0
    return {**base_resp, 'evaluated': evaluated, 'flagged': flagged, 'totals': totals,
            'matches': others, 'matches_total': matches_total}


def _domain_breakdown(df: pd.DataFrame, account: str, window: List[Dict[str, Any]],
                      spike: bool = False) -> List[Dict[str, Any]]:
    """Per-domain baseline vs latest unique open % for one account."""
    first, last = window[0], window[-1]
    sub = df[(df['account_name'] == account) & (df['period'].isin([first['start'], last['start']]))]
    out = []
    for dom, g in sub.groupby('From_domain'):
        b, l_ = g[g['period'] == first['start']], g[g['period'] == last['start']]
        b_del, l_del = int(b['delivered'].sum()), int(l_['delivered'].sum())
        b_rate = round(pct(int(b['opens'].sum()), b_del), 2) if b_del else None
        l_rate = round(pct(int(l_['opens'].sum()), l_del), 2) if l_del else None
        out.append({'domain': dom, 'baseline_open_rate': b_rate, 'latest_open_rate': l_rate,
                    'latest_avg_daily_delivered': round(l_del / last['collected_days']) if last['collected_days'] else 0,
                    'change_pts': round(l_rate - b_rate, 2) if b_rate is not None and l_rate is not None else None})
    # Biggest movers in the flagged direction first; domains missing a side go last.
    out.sort(key=lambda r: (r['change_pts'] is None, -(r['change_pts'] or 0) if spike else (r['change_pts'] or 0)))
    return out
