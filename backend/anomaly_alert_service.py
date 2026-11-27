"""
Deliverability anomaly alert for Observatory (daily email, 18:00 IST).

One email, three sections, built on the same detectors as the Pulsation trend tabs:
  1. Delivery rate drops   - day over day (with "N days straight") and week over week.
  2. Unique open % decline - daily, weekly and monthly trends.
  3. Send volume drops     - daily, weekly and monthly trends, each with a 12-month history chart.

The alert uses whatever Pulsation data has been collected (collection is still partly manual),
so it also reports days / regions with no data, so a gap is never read as a drop.

Charts are rendered with matplotlib and embedded as inline (cid:) images; preview mode embeds
them as data: URIs so the same HTML can be opened in a browser.
"""

import base64
import io
import os
import sqlite3
import statistics
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from delivery_rate_trend_service import detect_delivery_rate_trends
from open_rate_trend_service import detect_open_rate_trends
from pulsation_service import DB_PATH
from volume_trend_service import detect_volume_drops

# --------------------------------------------------------------------------- rules
# Each check: (mode, consecutive, threshold, include_current). Domain level, so every row is
# actionable; the account is shown alongside.
DELIVERY_CHECKS = [('daily', 1, 1.0, True), ('weekly', 1, 1.0, True)]           # pts, since yesterday / vs last week
OPEN_CHECKS = [('daily', 2, 2.0, True), ('weekly', 2, 2.0, True), ('monthly', 2, 2.0, True)]   # pts
VOLUME_CHECKS = [('daily', 3, 20.0, False), ('weekly', 2, 10.0, False), ('monthly', 2, 10.0, False)]  # %
MIN_DAILY = 5000          # sends (delivery, volume) / deliveries (open) per day at the start of the window
TABLE_ROWS = 15           # rows per section table
CHART_CARDS = {'delivery': 6, 'open': 6, 'volume': 8}
EXPECTED_REGIONS = ('US', 'EU')
MISSING_LOOKBACK_DAYS = 30

APP_URL = os.getenv('OBSERVATORY_URL', '')

# Palette shared with the app
C_EARLY, C_WIN, C_PART, C_INK, C_MUTED, C_GRID = '#86b6ef', '#eb6834', '#cbd5e1', '#0f172a', '#64748b', '#eef2f7'
C_BLUE, C_GREEN, C_RED, C_AMBER = '#2a78d6', '#1baf7a', '#d03b3b', '#eda100'
SEV_STYLE = {'severe': ('#fde2e2', '#b91c1c'), 'stopped': ('#fde2e2', '#b91c1c'),
             'high': ('#ffedd5', '#c2410c'), 'moderate': ('#fef3c7', '#a16207')}
UNIT = {'daily': 'day', 'weekly': 'week', 'monthly': 'month'}


# --------------------------------------------------------------------------- data checks
def _data_health(today: date) -> Dict[str, Any]:
    """Per-day / per-region coverage for the last MISSING_LOOKBACK_DAYS days up to yesterday."""
    start = today - timedelta(days=MISSING_LOOKBACK_DAYS)
    end = today - timedelta(days=1)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        rows = conn.execute(
            """SELECT report_date, region, COUNT(*) FROM daily_metrics
               WHERE report_date BETWEEN ? AND ? GROUP BY report_date, region""",
            (start.isoformat(), end.isoformat())).fetchall()
        last = conn.execute("SELECT MAX(report_date) FROM daily_metrics").fetchone()[0]
    finally:
        conn.close()
    by_day: Dict[str, Dict[str, int]] = {}
    for d, region, n in rows:
        by_day.setdefault(d, {})[region] = n
    # A region counts as partial when it has well under its usual number of domains.
    typical = {r: statistics.median([v[r] for v in by_day.values() if r in v] or [0]) for r in EXPECTED_REGIONS}
    missing_days, missing_region, partial = [], [], []
    d = start
    while d <= end:
        k = d.isoformat()
        got = by_day.get(k, {})
        if not got:
            missing_days.append(k)
        else:
            for r in EXPECTED_REGIONS:
                if r not in got:
                    missing_region.append((k, r))
                elif typical[r] and got[r] < 0.5 * typical[r]:
                    partial.append((k, r, got[r], int(typical[r])))
        d += timedelta(days=1)
    stale_days = (today - datetime.fromisoformat(last).date()).days if last else None
    return {'start': start.isoformat(), 'end': end.isoformat(), 'last_data_day': last, 'stale_days': stale_days,
            'missing_days': missing_days, 'missing_region': missing_region, 'partial': partial,
            'ok': not (missing_days or missing_region or partial) and stale_days == 1}


def _daily_totals(days: int = 35) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        return pd.read_sql_query(
            """SELECT report_date,
                      SUM(sent) AS sent,
                      SUM(CASE WHEN delivered <= sent AND delivered > 0 THEN delivered ELSE 0 END) AS delivered,
                      SUM(CASE WHEN delivered <= sent AND delivered > 0 THEN sent ELSE 0 END) AS sent_measured
               FROM daily_metrics WHERE report_date >= ? GROUP BY report_date ORDER BY report_date""",
            conn, params=((date.today() - timedelta(days=days)).isoformat(),))
    finally:
        conn.close()


def _monthly_history(domains: List[str], months: int = 12) -> Dict[str, List[Dict[str, Any]]]:
    """Average daily send per calendar month for each domain, last `months` months."""
    if not domains:
        return {}
    since = (date.today().replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        q = ','.join('?' * len(domains))
        df = pd.read_sql_query(
            f"""SELECT from_domain, substr(report_date, 1, 7) AS ym, SUM(sent) AS sent,
                       COUNT(DISTINCT report_date) AS days
                FROM daily_metrics WHERE report_date >= ? AND from_domain IN ({q})
                GROUP BY from_domain, ym""",
            conn, params=[since.isoformat(), *domains])
        cov = dict(conn.execute(
            "SELECT substr(report_date,1,7), COUNT(DISTINCT report_date) FROM daily_metrics "
            "WHERE report_date >= ? GROUP BY 1", (since.isoformat(),)).fetchall())
    finally:
        conn.close()
    yms, d = [], since
    while d <= date.today():
        yms.append(d.strftime('%Y-%m'))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    yms = [ym for ym in yms if cov.get(ym)]          # only months Pulsation has data for
    out = {}
    for dom in domains:
        sub = df[df['from_domain'] == dom].set_index('ym')
        series = []
        for ym in yms:
            sent = int(sub.loc[ym, 'sent']) if ym in sub.index else 0
            series.append({'ym': ym, 'label': datetime.strptime(ym, '%Y-%m').strftime("%b ’%y"),
                           'avg_daily': round(sent / cov[ym]) if cov[ym] else 0,
                           'partial': ym == date.today().strftime('%Y-%m')})
        out[dom] = series
    return out


# --------------------------------------------------------------------------- detection
def _collect(section: str) -> List[Dict[str, Any]]:
    rows = []
    if section == 'delivery':
        for mode, cons, thr, cur in DELIVERY_CHECKS:
            r = detect_delivery_rate_trends(mode=mode, level='domain', min_change_pts=thr, consecutive=cons,
                                            min_daily_sent=MIN_DAILY, include_current=cur)
            for f in r.get('flagged', []):
                rows.append({**f, 'mode': mode, 'check': r})
    elif section == 'open':
        for mode, cons, thr, cur in OPEN_CHECKS:
            r = detect_open_rate_trends(mode=mode, level='domain', min_change_pts=thr, consecutive=cons,
                                        min_daily_delivered=MIN_DAILY, include_current=cur)
            for f in r.get('flagged', []):
                rows.append({**f, 'mode': mode, 'check': r})
    else:
        for mode, cons, thr, cur in VOLUME_CHECKS:
            r = detect_volume_drops(mode=mode, level='domain', min_drop_pct=thr, consecutive=cons,
                                    min_daily_avg=MIN_DAILY, include_current=cur)
            for f in r.get('flagged', []):
                rows.append({**f, 'mode': mode, 'check': r})
    rank = {'stopped': 0, 'severe': 0, 'high': 1, 'moderate': 2}
    mode_rank = {'daily': 0, 'weekly': 1, 'monthly': 2}
    if section == 'volume':
        rows.sort(key=lambda x: (rank.get(x['severity'], 3), -abs(x.get('lost_per_day') or 0), mode_rank[x['mode']]))
    else:
        rows.sort(key=lambda x: (rank.get(x['severity'], 3), x.get('total_change_pts') or 0, mode_rank[x['mode']]))
    return rows


# --------------------------------------------------------------------------- charts
def _plt():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.edgecolor': '#e2e8f0',
                         'axes.labelcolor': C_MUTED, 'xtick.color': C_MUTED, 'ytick.color': C_MUTED})
    return plt


def _style(ax):
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color('#e2e8f0')
    ax.grid(axis='y', color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=8)


def _thin_labels(ax, labels, max_n=10):
    n = len(labels)
    step = max(1, -(-n // max_n))
    show = {i for i in range(n) if (n - 1 - i) % step == 0}     # anchor on the latest period
    ax.set_xticks(range(n))
    ax.set_xticklabels([l if i in show else '' for i, l in enumerate(labels)])


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=180, bbox_inches='tight', facecolor='white')
    _plt().close(fig)
    return buf.getvalue()


def _fmt_n(n) -> str:
    if n is None:
        return '—'
    a = abs(n)
    if a >= 1e6:
        return f'{n / 1e6:.1f}M'.replace('.0M', 'M')
    if a >= 1e3:
        return f'{n / 1e3:.1f}k'.replace('.0k', 'k')
    return str(int(round(n)))


def _rate_chart(item: Dict[str, Any], key: str, ylabel: str) -> bytes:
    """Bars per period for a rate (delivery % / open %), compared window highlighted."""
    plt = _plt()
    series = item['series'][-30:]
    win = set(item.get('window') or [])
    vals = [p.get(key) for p in series]
    labels = [p['label'] for p in series]
    colors = [C_WIN if p['label'] in win else (C_PART if not p.get('trusted', p.get('complete')) else C_EARLY)
              for p in series]
    ok = [v for v in vals if v is not None]
    fig, ax = plt.subplots(figsize=(6.6, 1.9))
    ax.bar(range(len(vals)), [v if v is not None else 0 for v in vals], color=colors, width=0.72, zorder=2)
    if ok:
        lo = min(ok)
        if key == 'delivery_rate':
            ax.set_ylim(max(0, int(lo) - 1), 100.2)
        else:
            ax.set_ylim(max(0, lo - (max(ok) - lo) * 0.6 - 1), max(ok) * 1.08 + 0.5)
        in_win = [i for i, p in enumerate(series) if p['label'] in win]
        for i, (v, p) in enumerate(zip(vals, series)):
            if i in (in_win[:1] + in_win[-1:]) and v is not None:      # first and last of the window
                ax.annotate(f'{v:.1f}%', (i, v), xytext=(0, 3), textcoords='offset points', ha='center',
                            fontsize=7.5, color=C_INK, fontweight='bold')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:.0f}%'))
    ax.yaxis.set_major_locator(plt.MaxNLocator(4))
    _thin_labels(ax, labels)
    _style(ax)
    return _png(fig)


def _volume_chart(item: Dict[str, Any], hist: List[Dict[str, Any]]) -> bytes:
    """Left: 12-month average daily send. Right: the flagged period's series (window highlighted)."""
    plt = _plt()
    monthly = item['mode'] == 'monthly'
    fig, axes = plt.subplots(1 if monthly else 2, 1, figsize=(6.6, 2.0 if monthly else 3.9))
    axes = [axes] if monthly else list(axes)
    # 12-month history
    ax = axes[0]
    win = set(item.get('window') or []) if monthly else set()
    hv = [h['avg_daily'] for h in hist]
    hc = [C_WIN if datetime.strptime(h['ym'], '%Y-%m').strftime('%b %Y') in win else (C_PART if h['partial'] else C_BLUE)
          for h in hist]
    ax.bar(range(len(hv)), hv, color=hc, width=0.7, zorder=2)
    ax.plot(range(len(hv)), hv, color=C_INK, linewidth=1, alpha=0.35, zorder=3)
    _thin_labels(ax, [h['label'] for h in hist], 12)
    ax.set_title(f'{len(hist)}-month trend · avg sends / day', fontsize=8.5, color=C_MUTED, loc='left')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _fmt_n(v)))
    ax.yaxis.set_major_locator(plt.MaxNLocator(4))
    _style(ax)
    if not monthly:
        ax = axes[1]
        series = item['series'][-30:] if item['mode'] == 'daily' else item['series'][-13:]
        win = set(item.get('window') or [])
        vals = [p.get('avg_daily') or 0 for p in series]
        cols = [C_WIN if p['label'] in win else (C_PART if not p['complete'] else C_EARLY) for p in series]
        ax.bar(range(len(vals)), vals, color=cols, width=0.72, zorder=2)
        _thin_labels(ax, [p['label'] for p in series], 10)
        ax.set_title(f'{item["mode"].capitalize()} · sends / day', fontsize=8.5, color=C_MUTED, loc='left')
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _fmt_n(v)))
        ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        _style(ax)
    fig.tight_layout(h_pad=1.6)
    return _png(fig)


def _overview_chart(totals: pd.DataFrame, health: Dict[str, Any]) -> bytes:
    """Last 30 days: total sends (bars) + delivery % (line), uncollected days marked."""
    plt = _plt()
    end = date.today() - timedelta(days=1)
    days = [end - timedelta(days=i) for i in range(29, -1, -1)]
    t = totals.set_index('report_date')
    sent = [int(t.loc[d.isoformat(), 'sent']) if d.isoformat() in t.index else None for d in days]
    rate = [(100.0 * t.loc[d.isoformat(), 'delivered'] / t.loc[d.isoformat(), 'sent_measured'])
            if d.isoformat() in t.index and t.loc[d.isoformat(), 'sent_measured'] else None for d in days]
    fig, ax = plt.subplots(figsize=(6.8, 2.1))
    ax.bar(range(len(days)), [s or 0 for s in sent], color=C_EARLY, width=0.72, zorder=2)
    for i, s in enumerate(sent):
        if s is None:
            ax.axvspan(i - 0.45, i + 0.45, color='#fee2e2', zorder=1)
            ax.text(i, 0, 'no\ndata', ha='center', va='bottom', fontsize=6, color=C_RED)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _fmt_n(v)))
    ax.yaxis.set_major_locator(plt.MaxNLocator(4))
    _style(ax)
    ax2 = ax.twinx()
    xs = [i for i, r in enumerate(rate) if r is not None]
    ax2.plot(xs, [rate[i] for i in xs], color=C_GREEN, linewidth=2, marker='o', markersize=2.5, zorder=4)
    ok = [r for r in rate if r is not None]
    if ok:
        ax2.set_ylim(max(0, int(min(ok)) - 2), 100.5)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:.0f}%'))
    ax2.yaxis.set_major_locator(plt.MaxNLocator(4))
    for s in ('top', 'right', 'left', 'bottom'):
        ax2.spines[s].set_visible(False)
    ax2.tick_params(length=0, labelsize=8, colors=C_GREEN)
    _thin_labels(ax, [d.strftime('%d %b') for d in days], 10)
    return _png(fig)


# --------------------------------------------------------------------------- html
def _badge(sev: str) -> str:
    bg, fg = SEV_STYLE.get(sev, ('#f1f5f9', '#475569'))
    return (f'<span style="display:inline-block;padding:2px 9px;border-radius:999px;background:{bg};color:{fg};'
            f'font-size:11px;font-weight:700;white-space:nowrap;">{escape(sev.capitalize())}</span>')


def _mode_chip(mode: str) -> str:
    return (f'<span style="display:inline-block;padding:1px 7px;border-radius:6px;background:#eff6ff;color:#1d4ed8;'
            f'font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{mode}</span>')


def _pts(v) -> str:
    if v is None:
        return '—'
    return f'{"+" if v > 0 else "−" if v < 0 else ""}{abs(v):.1f} pts'


def _pct(v) -> str:
    return '—' if v is None else f'{"+" if v > 0 else ""}{v:.1f}%'


def _rate(v) -> str:
    return '—' if v is None else f'{v:.2f}%'


def _window_text(f) -> str:
    w = list(f.get('window') or [])
    if f.get('mode') == 'monthly' and len({x[-4:] for x in w}) == 1:
        w = [x[:3] for x in w[:-1]] + w[-1:]          # Aug → Sep → Oct 2026
    if w and f.get('window_partial'):
        w[-1] += ' (so far)'
    return ' → '.join(w)


def _streak_text(f) -> str:
    s = f.get('streak') or 0
    if s >= 2:
        return f'{s} {UNIT[f["mode"]]}s straight'
    return f'vs prev. {UNIT[f["mode"]]}'


TH = 'style="text-align:left;padding:8px 10px;font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #e2e8f0;background:#f8fafc;"'
THR = TH.replace('text-align:left', 'text-align:right')
TD = 'style="padding:9px 10px;font-size:12.5px;color:#334155;border-bottom:1px solid #f1f5f9;vertical-align:top;"'
TDR = TD.replace('vertical-align:top;', 'vertical-align:top;text-align:right;')


def _name_cell(f) -> str:
    acct = f.get('account')
    acct = '' if not acct or acct == 'Unmapped' else acct
    esp = ', '.join(f.get('esps') or [])
    sub = ' · '.join(x for x in (acct, esp) if x)
    return (f'<div style="font-weight:700;color:#0f172a;overflow-wrap:anywhere;">{escape(str(f["name"]))}</div>'
            f'<div style="font-size:11px;color:#94a3b8;margin-top:2px;">{escape(sub)}</div>')


def _table(section: str, rows: List[Dict[str, Any]]) -> str:
    """Compact 6-column table that fits a 700px email: values with the compared periods underneath."""
    small = 'style="font-size:11px;color:#94a3b8;margin-top:3px;"'

    def period(f):
        return f'{_mode_chip(f["mode"])}<div {small}>{_streak_text(f)}</div>'

    def change(main, sub=''):
        return f'<b style="color:#b91c1c;">{main}</b>' + (f'<div {small}>{sub}</div>' if sub else '')

    def move(a, b, f):
        w = _window_text(f).split(' → ')
        span = w[0] + ' → ' + w[-1] if len(w) > 2 else ' → '.join(w)
        return (f'<span style="white-space:nowrap;">{a} → <b style="color:#0f172a;">{b}</b></span>'
                f'<div {small}>{escape(span)}</div>')

    if section == 'delivery':
        cols = ('Delivery %', 'Change', 'Sent / day')
        cells = lambda f: (move(_rate(f['baseline_delivery_rate']), _rate(f['latest_delivery_rate']), f),
                           change(_pts(f['total_change_pts'])), _fmt_n(f.get('latest_avg_daily_sent')))
    elif section == 'open':
        cols = ('Unique open %', 'Change', 'Delivered / day')
        cells = lambda f: (move(_rate(f['baseline_open_rate']), _rate(f['latest_open_rate']), f),
                           change(_pts(f['total_change_pts']), ' → '.join(_pts(x) for x in f.get('steps_pts') or [])),
                           _fmt_n(f.get('latest_avg_daily_delivered')))
    else:
        cols = ('Sends / day', 'Change', 'Delivery %')
        cells = lambda f: (move(_fmt_n(f['baseline_avg_daily']), _fmt_n(f['latest_avg_daily']), f),
                           change(_pct(f['total_drop_pct']), ' → '.join(_pct(x) for x in f.get('steps_pct') or [])),
                           _rate(f.get('delivery_rate_latest')))
    head = (f'<th {TH}>Severity</th><th {TH}>Sending domain</th><th {TH}>Period</th>'
            f'<th {THR}>{cols[0]}</th><th {THR}>{cols[1]}</th><th {THR}>{cols[2]}</th>')
    body = ''
    for f in rows:
        c = cells(f)
        body += (f'<tr><td {TD}>{_badge(f["severity"])}</td><td {TD}>{_name_cell(f)}</td><td {TD}>{period(f)}</td>'
                 f'<td {TDR}>{c[0]}</td><td {TDR}>{c[1]}</td><td {TDR}>{c[2]}</td></tr>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
            f'style="border-collapse:collapse;border:1px solid #e2e8f0;">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


def _card(f, img_src: str, stat_line: str) -> str:
    return (f'<div style="border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px 6px;margin:12px 0;background:#fff;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="vertical-align:top;">{_name_cell(f)}</td>'
            f'<td style="text-align:right;vertical-align:top;white-space:nowrap;">{_badge(f["severity"])} {_mode_chip(f["mode"])}</td></tr></table>'
            f'<div style="font-size:12px;color:#475569;margin:6px 0 4px;">{stat_line}</div>'
            f'<img src="{img_src}" width="640" alt="trend chart for {escape(str(f["name"]))}" '
            f'style="display:block;width:100%;max-width:640px;height:auto;border:0;"></div>')


def _legend(items) -> str:
    return '<div style="font-size:11px;color:#64748b;margin:4px 0 0;">' + ' &nbsp; '.join(
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{c};vertical-align:-1px;"></span> {escape(t)}'
        for c, t in items) + '</div>'


def _section(num: int, title: str, desc: str, total: int, rows_html: str, cards_html: str, more: int, empty: str) -> str:
    color = '#b91c1c' if total else '#15803d'
    head = (f'<div style="margin:30px 0 10px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td><div style="font-size:11px;font-weight:700;color:#2563eb;letter-spacing:.08em;text-transform:uppercase;">Section {num}</div>'
            f'<div style="font-size:18px;font-weight:800;color:#0f172a;margin-top:2px;">{escape(title)}</div></td>'
            f'<td style="text-align:right;"><span style="font-size:22px;font-weight:800;color:{color};">{total}</span>'
            f'<div style="font-size:11px;color:#94a3b8;">flagged</div></td></tr></table>'
            f'<div style="font-size:12px;color:#64748b;margin-top:4px;line-height:1.5;">{desc}</div></div>')
    if not total:
        return head + (f'<div style="padding:16px;border-radius:10px;background:#f0fdf4;border:1px solid #bbf7d0;'
                       f'color:#166534;font-size:13px;">✅ {escape(empty)}</div>')
    more_html = (f'<div style="font-size:12px;color:#64748b;margin-top:8px;">+ {more} more — see Pulsation in Observatory.</div>'
                 if more > 0 else '')
    return head + rows_html + more_html + cards_html


def _health_html(h: Dict[str, Any]) -> str:
    items = []
    if h['stale_days'] is None:
        items.append('No Pulsation data has been collected.')
    elif h['stale_days'] > 1:
        items.append(f'Latest data is <b>{h["last_data_day"]}</b> ({h["stale_days"]} days old) — yesterday has not been collected yet.')
    fmt = lambda d: datetime.fromisoformat(d).strftime('%d %b')
    if h['missing_days']:
        items.append('No data at all for: <b>' + ', '.join(fmt(d) for d in h['missing_days']) + '</b>.')
    by_region: Dict[str, List[str]] = {}
    for d, r in h['missing_region']:
        by_region.setdefault(r, []).append(fmt(d))
    for r, ds in by_region.items():
        items.append(f'<b>{r}</b> region missing for: <b>{", ".join(ds)}</b>.')
    if h['partial']:
        items.append('Partly collected: ' + ', '.join(f'<b>{fmt(d)} {r}</b> ({n} of ~{t} domains)' for d, r, n, t in h['partial']) + '.')
    if not items:
        return ('<div style="padding:11px 14px;border-radius:10px;background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;'
                f'font-size:12.5px;">✅ Data complete for the last {MISSING_LOOKBACK_DAYS} days (US + EU), through {h["last_data_day"]}.</div>')
    return ('<div style="padding:12px 14px;border-radius:10px;background:#fffbeb;border:1px solid #fde68a;color:#92400e;font-size:12.5px;line-height:1.6;">'
            f'<b>⚠ Data gaps (last {MISSING_LOOKBACK_DAYS} days)</b> — gaps are skipped, never counted as drops; '
            'daily comparisons step over missing days.<ul style="margin:6px 0 0 18px;padding:0;">'
            + ''.join(f'<li>{i}</li>' for i in items) + '</ul></div>')


def _kpi(label: str, value: Any, sub: str, color: str) -> str:
    return (f'<td style="width:25%;padding:14px 16px;border-right:1px solid #e2e8f0;vertical-align:top;">'
            f'<div style="font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;">{label}</div>'
            f'<div style="font-size:26px;font-weight:800;color:{color};margin-top:4px;">{value}</div>'
            f'<div style="font-size:11px;color:#94a3b8;margin-top:2px;">{sub}</div></td>')


def build_anomaly_alert(inline: bool = True) -> Tuple[str, str, List[Tuple[str, bytes]], Dict[str, Any]]:
    """Returns (subject, html, images, summary). images: [(content_id, png_bytes)].
    inline=True -> <img src="cid:..."> for email; False -> data: URIs for a browser preview."""
    today = date.today()
    health = _data_health(today)
    sections = {k: _collect(k) for k in ('delivery', 'open', 'volume')}
    images: List[Tuple[str, bytes]] = []

    def src(cid: str, png: bytes) -> str:
        images.append((cid, png))
        return f'cid:{cid}' if inline else 'data:image/png;base64,' + base64.b64encode(png).decode()

    overview = src('overview', _overview_chart(_daily_totals(), health))

    # Section 1 - delivery
    d_rows = sections['delivery']
    d_cards = ''.join(_card(f, src(f'dr{i}', _rate_chart(f, 'delivery_rate', 'Delivery %')),
                            f'Delivery <b>{_rate(f["baseline_delivery_rate"])} → {_rate(f["latest_delivery_rate"])}</b> '
                            f'({_pts(f["total_change_pts"])}) · {escape(_window_text(f))} · {_streak_text(f)} · '
                            f'bounce {_rate(f.get("bounce_rate_baseline"))} → {_rate(f.get("bounce_rate_latest"))}')
                      for i, f in enumerate(d_rows[:CHART_CARDS['delivery']]))
    # Section 2 - open
    o_rows = sections['open']
    o_cards = ''.join(_card(f, src(f'or{i}', _rate_chart(f, 'open_rate', 'Unique open %')),
                            f'Unique open <b>{_rate(f["baseline_open_rate"])} → {_rate(f["latest_open_rate"])}</b> '
                            f'({_pts(f["total_change_pts"])}) · {escape(_window_text(f))}')
                      for i, f in enumerate(o_rows[:CHART_CARDS['open']]))
    # Section 3 - volume (with 12-month history)
    v_rows = sections['volume']
    v_top = v_rows[:CHART_CARDS['volume']]
    hist = _monthly_history(sorted({f['name'] for f in v_top}))
    v_cards = ''.join(_card(f, src(f'vo{i}', _volume_chart(f, hist.get(f['name'], []))),
                            f'Avg sends / day <b>{_fmt_n(f["baseline_avg_daily"])} → {_fmt_n(f["latest_avg_daily"])}</b> '
                            f'({_pct(f["total_drop_pct"])}, −{_fmt_n(f["lost_per_day"])}/day) · {escape(_window_text(f))}')
                      for i, f in enumerate(v_top))

    win_legend = _legend([(C_EARLY, 'Earlier'), (C_WIN, 'Compared window'), (C_PART, 'In progress / partial')])
    vol_legend = _legend([(C_BLUE, 'Month'), (C_WIN, 'Compared window'), (C_EARLY, 'Earlier period'), (C_PART, 'In progress')])
    nd, no, nv = len(d_rows), len(o_rows), len(v_rows)
    total = nd + no + nv

    rules_d = ', '.join(f'{m} (≥{t:g} pt{"s" if t != 1 else ""} {"in each of " + str(c) + " " + UNIT[m] + "s" if c > 1 else "vs previous " + UNIT[m]})'
                        for m, c, t, _ in DELIVERY_CHECKS)
    rules_o = ', '.join(f'{m} ({c} declines in a row, ≥{t:g} pts each)' for m, c, t, _ in OPEN_CHECKS)
    rules_v = ', '.join(f'{m} ({c} drops in a row, ≥{t:g}% each)' for m, c, t, _ in VOLUME_CHECKS)

    sec1 = _section(1, 'Delivery rate drops', f'Delivery % (delivered ÷ sent) fell — {rules_d}. Domains sending ≥ {_fmt_n(MIN_DAILY)}/day; '
                    'rows where delivered exceeds sent are ignored; the current week counts so far.',
                    nd, _table('delivery', d_rows[:TABLE_ROWS]), (win_legend + d_cards) if d_cards else '',
                    nd - TABLE_ROWS, 'No delivery-rate drops since yesterday or vs last week.')
    sec2 = _section(2, 'Unique open % declining', f'Unique opens ÷ delivered trending down — {rules_o}. Domains delivering ≥ {_fmt_n(MIN_DAILY)}/day; '
                    'the current week / month counts so far.',
                    no, _table('open', o_rows[:TABLE_ROWS]), (win_legend + o_cards) if o_cards else '',
                    no - TABLE_ROWS, 'No declining open-rate trends.')
    sec3 = _section(3, 'Send volume dropping', f'Average daily send falling — {rules_v}; complete weeks / months only. '
                    f'Domains sending ≥ {_fmt_n(MIN_DAILY)}/day at the start. Each chart shows the full 12-month history.',
                    nv, _table('volume', v_rows[:TABLE_ROWS]), (vol_legend + v_cards) if v_cards else '',
                    nv - TABLE_ROWS, 'No volume-drop trends.')

    when = today.strftime('%d %b %Y')
    subject = (f'Deliverability anomalies — {when}: {nd} delivery drop{"s" if nd != 1 else ""} · '
               f'{no} open-rate decline{"s" if no != 1 else ""} · {nv} volume drop{"s" if nv != 1 else ""}'
               if total else f'Deliverability anomalies — {when}: all clear')
    if not health['ok']:
        subject += ' · ⚠ data gaps'

    link = (f'<a href="{escape(APP_URL)}" style="color:#bfdbfe;">Open Observatory →</a>' if APP_URL else '')
    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:760px;margin:0 auto;padding:24px 12px;">
<div style="background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e2e8f0;">
<div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);background-color:#1e40af;padding:26px 30px;color:#fff;">
  <div style="font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;opacity:.8;">Blueshift Observatory · Pulsation</div>
  <div style="font-size:23px;font-weight:800;margin-top:6px;">Deliverability anomalies</div>
  <div style="font-size:13px;opacity:.85;margin-top:4px;">{when} · data through {escape(str(health["last_data_day"]))} {link}</div>
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid #e2e8f0;"><tr>
  {_kpi('Total flagged', total, 'domains across all checks', '#b91c1c' if total else '#15803d')}
  {_kpi('Delivery drops', nd, 'day / week', '#b91c1c' if nd else '#15803d')}
  {_kpi('Open-rate declines', no, 'day / week / month', '#b91c1c' if no else '#15803d')}
  {_kpi('Volume drops', nv, 'day / week / month', '#b91c1c' if nv else '#15803d')}
</tr></table>
<div style="padding:20px 30px 30px;">
  {_health_html(health)}
  <div style="margin:22px 0 4px;font-size:13px;font-weight:700;color:#0f172a;">All senders · last 30 days</div>
  <div style="font-size:11px;color:#64748b;"><span style="color:{C_BLUE};">■</span> sends / day &nbsp; <span style="color:{C_GREEN};">●</span> delivery % (right axis)</div>
  <img src="{overview}" width="680" alt="30-day overview" style="display:block;width:100%;max-width:680px;height:auto;margin-top:6px;border:0;">
  {sec1}
  {sec2}
  {sec3}
</div>
<div style="padding:16px 30px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;line-height:1.6;">
  Sent daily at 18:00 IST by Blueshift Observatory from the Pulsation data collected so far.
  Same rules as the Volume / Open Rate / Delivery Rate Trends tabs (sending-domain view).
</div></div></div></body></html>'''
    summary = {'delivery': nd, 'open': no, 'volume': nv, 'total': total, 'images': len(images),
               'data_ok': health['ok'], 'health': health}
    return subject, html, images, summary


def send_anomaly_alert(recipients: List[str]) -> Dict[str, Any]:
    from dns_alert_service import FROM_EMAIL, REPLY_TO_EMAIL, SENDGRID_API_KEY
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (Attachment, Content, ContentId, Disposition, Email, FileContent, FileName,
                                       FileType, Mail, To)
    if not recipients:
        return {'status': 'skipped', 'reason': 'no recipients'}
    if not SENDGRID_API_KEY:
        return {'status': 'error', 'reason': 'SENDGRID_API_KEY not set'}
    subject, html, images, summary = build_anomaly_alert(inline=True)
    msg = Mail(from_email=Email(FROM_EMAIL), to_emails=[To(r) for r in recipients], subject=subject,
               html_content=Content('text/html', html))
    msg.reply_to = Email(REPLY_TO_EMAIL)
    for cid, png in images:
        msg.add_attachment(Attachment(FileContent(base64.b64encode(png).decode()), FileName(f'{cid}.png'),
                                      FileType('image/png'), Disposition('inline'), ContentId(cid)))
    try:
        resp = SendGridAPIClient(SENDGRID_API_KEY).send(msg)
        return {'status': 'success', 'recipients': recipients, 'subject': subject, 'status_code': resp.status_code,
                **{k: v for k, v in summary.items() if k != 'health'}}
    except Exception as e:
        return {'status': 'error', 'reason': str(e), 'subject': subject}
