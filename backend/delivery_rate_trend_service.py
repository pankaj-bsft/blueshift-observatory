"""
Delivery-rate drop detection for Pulsation ("Delivery Rate Trends" tab).

Same idea as volume_trend_service, applied to delivery rate (delivered / sent):
flags accounts / sending domains whose delivery % fell by at least N percentage
points in each of N consecutive days, weeks or months.

  * Rate per period = total delivered / total sent over that period (not an
    average of daily rates), so a tiny day can't swing it.
  * Rows where delivered > sent (delivery events landing a day late) are ignored:
    they would read as >100% and hide a real drop. Rows with sent but zero
    delivered are "no delivery events recorded" (some ESP / domain combos), so
    they are unknown, not 0%.
  * Only drops are detected: a healthy sender sits near 100%, so a "spike" just
    means recovery.
  * Changes are in percentage POINTS (99% -> 96% = -3 pts).
  * With a search term, every matching account / domain is returned, flagged or
    not, so any sender's delivery trend can be looked up.
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import pandas as pd

from pulsation_service import DB_PATH, _attach_account_name, pct
from volume_trend_service import _period_end as _wm_end, _period_label as _wm_label, _period_start as _wm_start

_MIN_COVERAGE = {'daily': 1, 'weekly': 4, 'monthly': 20}
_HISTORY = {'daily': 30, 'weekly': 12, 'monthly': 12}
_UNIT = {'daily': ('day', 'days'), 'weekly': ('week', 'weeks'), 'monthly': ('month', 'months')}
_MAX_MATCHES = 200
# The week / month in progress can join the comparison once it has this many collected days.
# A delivery rate is a ratio, so a partial period is meaningful (unlike a partial volume total).
_MIN_PARTIAL = {'weekly': 2, 'monthly': 5}


def _period_start(d: date, mode: str) -> date:
    return d if mode == 'daily' else _wm_start(d, mode)


def _period_end(start: date, mode: str) -> date:
    return start if mode == 'daily' else _wm_end(start, mode)


def _period_label(start: date, mode: str) -> str:
    return start.strftime('%d %b') if mode == 'daily' else _wm_label(start, mode)


def _load_daily(since: date) -> pd.DataFrame:
    """Per domain per day. Over-100% rows are excluded but counted, so the UI can say so."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        return pd.read_sql_query(
            """
            SELECT report_date, from_domain,
                   GROUP_CONCAT(DISTINCT esp) AS esps,
                   SUM(CASE WHEN delivered <= sent THEN sent ELSE 0 END)      AS sent,
                   SUM(CASE WHEN delivered <= sent THEN delivered ELSE 0 END) AS delivered,
                   SUM(CASE WHEN delivered <= sent THEN bounces ELSE 0 END)   AS bounces,
                   SUM(CASE WHEN delivered > sent THEN 1 ELSE 0 END)          AS over100_rows
            FROM daily_metrics
            WHERE report_date >= ? AND sent > 0 AND delivered > 0
            GROUP BY report_date, from_domain
            """,
            conn, params=(since.isoformat(),))
    finally:
        conn.close()


def _severity(total_pts: float) -> str:
    a = abs(total_pts)
    return 'severe' if a >= 5 else 'high' if a >= 2 else 'moderate'


def detect_delivery_rate_trends(mode: str = 'weekly', level: str = 'account', min_change_pts: float = 1.0,
                                consecutive: int = 2, min_daily_sent: float = 5000.0,
                                q: str = '', include_current: bool = True) -> Dict[str, Any]:
    m = str(mode).lower()
    mode = 'daily' if m.startswith('d') else 'monthly' if m.startswith('m') else 'weekly'
    level = 'domain' if str(level).lower().startswith('d') else 'account'
    consecutive = max(1, min(int(consecutive), 6))
    threshold = max(0.0, float(min_change_pts))
    history = max(_HISTORY[mode], consecutive + 1)
    qn = (q or '').strip().lower()
    include_current = bool(include_current) and mode != 'daily'   # the latest day is always complete
    params = {'min_change_pts': threshold, 'consecutive': consecutive, 'min_daily_sent': min_daily_sent,
              'include_current': include_current}
    base_resp = {'mode': mode, 'level': level, 'direction': 'drop', 'params': params, 'q': qn,
                 'periods': [], 'window': [], 'flagged': [], 'matches': [], 'evaluated': 0}

    today = datetime.utcnow().date()
    span_days = {'daily': history + 7, 'weekly': history * 7 + 40, 'monthly': history * 31 + 40}[mode]
    df = _load_daily(today - timedelta(days=span_days))
    if df.empty:
        return {**base_resp, 'note': 'No Pulsation data collected yet.'}

    df['d'] = pd.to_datetime(df['report_date']).dt.date
    last_data_day = max(df['d'])

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
    while len(periods) > 1 and periods[0]['collected_days'] == 0:
        periods.pop(0)
    if mode == 'daily':
        # Days the collector missed are gaps, not data: leave them out of the daily view.
        periods = [p for p in periods if p['collected_days']]

    df['period'] = df['d'].map(lambda d: _period_start(d, mode).isoformat())
    df = df[df['period'].isin([p['start'] for p in periods])]
    df = df.rename(columns={'from_domain': 'From_domain'})
    df = _attach_account_name(df)
    key = 'account_name' if level == 'account' else 'From_domain'

    grouped = (df.groupby([key, 'period'], as_index=False)
                 .agg(sent=('sent', 'sum'), delivered=('delivered', 'sum'),
                      bounces=('bounces', 'sum'), over100=('over100_rows', 'sum')))
    by_entity = {name: g.set_index('period') for name, g in grouped.groupby(key)}
    domain_account = df.groupby('From_domain')['account_name'].first().to_dict()
    esps_by: Dict[str, set] = {}
    for name, g in df.groupby(key):
        esps_by[name] = {e for v in g['esps'].dropna() for e in str(v).split(',') if e}

    trusted = [p for p in periods if p['trusted']]
    cur_p = periods[-1] if periods and not periods[-1]['complete'] else None
    if include_current and cur_p and cur_p['collected_days'] >= _MIN_PARTIAL[mode]:
        trusted = trusted + [cur_p]
    else:
        cur_p = None
    trusted_idx = {p['start']: i for i, p in enumerate(trusted)}
    window = trusted[-(consecutive + 1):]
    base_resp.update({'periods': periods, 'window': [p['label'] for p in window],
                      'window_partial': bool(cur_p) and window[-1] is cur_p,
                      'current_period': ({'label': cur_p['label'], 'collected_days': cur_p['collected_days'],
                                          'days': cur_p['days']} if cur_p else None),
                      'last_data_day': last_data_day.isoformat()})
    if len(window) < consecutive + 1:
        return {**base_resp, 'note': f'Not enough complete {_UNIT[mode][1]} yet.'}

    def series_for(g):
        out = []
        for p in periods:
            row = g.loc[p['start']] if p['start'] in g.index else None
            sent = int(row['sent']) if row is not None else 0
            delivered = int(row['delivered']) if row is not None else 0
            bounces = int(row['bounces']) if row is not None else 0
            days = p['collected_days'] or 0
            rate = round(pct(delivered, sent), 2) if sent and delivered else None
            if rate is not None and rate > 100:
                rate = None   # never trust >100%
            out.append({
                'start': p['start'], 'label': p['label'], 'complete': p['complete'],
                'trusted': p['trusted'], 'collected_days': days,
                'sent': sent, 'delivered': delivered,
                'avg_daily_sent': round(sent / days) if days else None,
                'delivery_rate': rate,
                'bounce_rate': round(pct(bounces, sent), 2) if sent else None,
                'excluded_rows': int(row['over100']) if row is not None else 0,
            })
        return out

    def matches_q(name) -> bool:
        if not qn:
            return True
        if qn in str(name).lower():
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
        # The entity's own last N+1 trusted periods with a usable rate (see `slack` below).
        valid = [p for p in trusted if by_start[p['start']]['delivery_rate'] is not None]
        ewin = valid[-(consecutive + 1):]
        item = {
            'name': name, 'level': level,
            'account': domain_account.get(name) if level == 'domain' else name,
            'esps': sorted(esps_by.get(name, [])), 'series': series,
            'flagged': False, 'severity': None, 'status': 'ok',
        }
        # Daily: a day dropped as >100% may be stepped over (up to 2). Weekly / monthly: no gaps.
        slack = 2 if mode == 'daily' else 0
        recent = (len(ewin) == consecutive + 1
                  and trusted_idx[ewin[-1]['start']] >= len(trusted) - 1 - slack
                  and trusted_idx[ewin[-1]['start']] - trusted_idx[ewin[0]['start']] <= consecutive + slack)
        if not recent:
            item['status'] = 'no_data'
            if qn:
                others.append(item)
            continue

        win = [by_start[p['start']] for p in ewin]
        base, latest = win[0]['delivery_rate'], win[-1]['delivery_rate']
        steps = [round(b['delivery_rate'] - a['delivery_rate'], 2) for a, b in zip(win, win[1:])]
        total_pts = round(latest - base, 2)
        item.update({
            'baseline_delivery_rate': base, 'latest_delivery_rate': latest,
            'total_change_pts': total_pts, 'steps_pts': steps,
            'baseline_avg_daily_sent': win[0]['avg_daily_sent'], 'latest_avg_daily_sent': win[-1]['avg_daily_sent'],
            'bounce_rate_baseline': win[0]['bounce_rate'], 'bounce_rate_latest': win[-1]['bounce_rate'],
            'window': [w['label'] for w in win],
            'window_partial': not win[-1]['complete'],
            'skipped': trusted_idx[ewin[-1]['start']] - trusted_idx[ewin[0]['start']] - consecutive,
        })
        # Same rule as Volume Trends drops: the sender must have been sizeable at the start of the
        # window. A sender whose volume also collapsed while its delivery rate fell still counts.
        if (win[0]['avg_daily_sent'] or 0) < min_daily_sent:
            item['status'] = 'low_volume'
            if qn:
                others.append(item)
            continue
        evaluated += 1

        if not all(st <= -threshold for st in steps):
            if qn:
                others.append(item)
            continue

        streak = 0
        rates = [by_start[p['start']]['delivery_rate'] for p in valid]
        for a, b in zip(reversed(rates[:-1]), reversed(rates[1:])):
            if a - b >= threshold:
                streak += 1
            else:
                break
        item.update({'flagged': True, 'status': 'flagged', 'severity': _severity(total_pts),
                     'streak': max(streak, consecutive)})
        if level == 'account':
            item['domains'] = _domain_breakdown(df, name, ewin, mode)
        flagged.append(item)

    order = {'severe': 0, 'high': 1, 'moderate': 2}
    flagged.sort(key=lambda x: (order[x['severity']], x['total_change_pts']))
    if qn:
        # Searched senders that did not trip the rule: biggest decline first, then no-data ones.
        others.sort(key=lambda x: (x.get('total_change_pts') is None, x.get('total_change_pts') or 0))
        for o in others:
            if o.get('baseline_delivery_rate') is not None and level == 'account':
                ewin = [p for p in trusted if p['label'] in o['window']]
                o['domains'] = _domain_breakdown(df, o['name'], ewin, mode)
    totals = {'flagged': len(flagged), **{k: sum(1 for f in flagged if f['severity'] == k) for k in order}}
    totals['avg_change_pts'] = round(sum(f['total_change_pts'] for f in flagged) / len(flagged), 2) if flagged else 0
    return {**base_resp, 'evaluated': evaluated, 'flagged': flagged, 'totals': totals,
            'matches': others[:_MAX_MATCHES], 'matches_total': len(others)}


def _domain_breakdown(df: pd.DataFrame, account: str, window: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    """Per-domain baseline vs latest delivery % for one account."""
    first, last = window[0], window[-1]
    sub = df[(df['account_name'] == account) & (df['period'].isin([first['start'], last['start']]))]
    out = []
    for dom, g in sub.groupby('From_domain'):
        b, l_ = g[g['period'] == first['start']], g[g['period'] == last['start']]
        b_sent, l_sent = int(b['sent'].sum()), int(l_['sent'].sum())
        b_rate = round(pct(int(b['delivered'].sum()), b_sent), 2) if b_sent else None
        l_rate = round(pct(int(l_['delivered'].sum()), l_sent), 2) if l_sent else None
        out.append({'domain': dom, 'baseline_delivery_rate': b_rate, 'latest_delivery_rate': l_rate,
                    'latest_avg_daily_sent': round(l_sent / last['collected_days']) if last['collected_days'] else 0,
                    'change_pts': round(l_rate - b_rate, 2) if b_rate is not None and l_rate is not None else None})
    out.sort(key=lambda r: (r['change_pts'] is None, r['change_pts'] or 0))
    return out
