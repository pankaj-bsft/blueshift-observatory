"""
Email domain (ISP) sending stats per sending domain, with a 30-day local day-level cache.

Pulsation reports a sending domain's totals; this breaks those totals down by recipient
mailbox provider (gmail.com, outlook.com, ...) so a delivery problem can be attributed to
a specific ISP.

Caching: Druid is queried with a per-day bucket and the resulting rows are stored per
(sending_domain, region, esp, email_domain, report_date). A later request for a date range
is served from those rows, and only the days that are missing are fetched. Rows older than
RETENTION_DAYS are pruned.

Additivity caveat: the sum(...) metrics re-aggregate exactly across days. The two
APPROX_COUNT_DISTINCT_DS_HLL metrics (unique opens, unique soft bounces) do not — summing
daily uniques double-counts a recipient active on more than one day, which measured a few
percent high on real data. Multi-day responses therefore mark those columns approximate via
`uniques_are_summed`, and the caller is expected to label them.
"""

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from config import DRUID_US_BROKER, DRUID_EU_BROKER
from data_paths import data_path

DB_PATH = data_path('email_domain_stats.db')
HISTORY_DB_PATH = data_path('deliverability_history.db')

RETENTION_DAYS = 30
DRUID_TIMEOUT_SECONDS = 180

# Recipient mailbox providers to report on. Kept as an explicit list (rather than a
# top-N) so the columns are stable from day to day.
ISP_DOMAINS = [
    'gmail.com', 'yahoo.com', 'hotmail.com', 'aol.com', 'icloud.com', 'comcast.net',
    'hotmail.co.uk', 'msn.com', 'outlook.com', 'sbcglobal.net', 'att.net', 'live.com',
    'verizon.net', 'me.com', 'yahoo.co.uk', 'bellsouth.net', 'ymail.com', 'cox.net',
    'live.co.uk', 'googlemail.com', 'charter.net', 'btinternet.com', 'mac.com', 'sky.com',
    'rocketmail.com', 'optonline.net', 'mail.com', 'roadrunner.com', 'frontier.com',
    'earthlink.net', 'aim.com', 'yahoo.ca', 'ntlworld.com', 'frontiernet.net', 'juno.com',
    'netscape.net', 'cfl.rr.com', 'suddenlink.net', 'ptd.net', 'windstream.net',
    'tampabay.rr.com', 'virginmedia.com', 'blueyonder.co.uk', 'mchsi.com', 'pacbell.net',
    'prodigy.net', 'rochester.rr.com', 'embarqmail.com', 'ameritech.net', 'nc.rr.com',
    'nycap.rr.com', 'rogers.com', 'swbell.net', 'wi.rr.com', 'yahoo.com.br', 'bigpond.com',
    'umich.edu', 'live.ca', 'carolina.rr.com', 'shaw.ca', 'hotmail.ca', 'cs.com', 'gmx.com',
    'yahoo.com.au', 'twcny.rr.com', 'woh.rr.com', 'triad.rr.com', 'bresnan.net', 'q.com',
    'mindspring.com', 'sc.rr.com', 'neo.rr.com', 'snet.net', 'centurylink.net', 'yahoo.fr',
    'bex.net', 'cinci.rr.com', 'tiscali.co.uk', 'zoominternet.net', 'netzero.net', 'vt.edu',
    'live.com.au', 'email.com', 'columbus.rr.com', 'stny.rr.com', 'kent.edu', 'optimum.net',
    'comporium.net', 'netzero.com', 'umn.edu', 'tds.net', 'btopenworld.com', 'maine.rr.com',
    'telus.net', 'atlanticbb.net', 'yahoo.es', 'hawaii.rr.com', 'sympatico.ca',
    'centurytel.net', 'hughes.net', 'live.at', 'live.be', 'live.cl', 'live.cn', 'live.co.kr',
    'live.com.ar', 'live.com.mx', 'live.com.my', 'live.com.pt', 'live.com.sg', 'live.de',
    'live.dk', 'live.fr', 'live.hk', 'live.ie', 'live.in', 'live.it', 'live.jp', 'live.nl',
    'live.no', 'live.ru', 'live.se', 'yahoo.co.in', 'yahoo.co.jp', 'yahoo.com.hk',
    'yahoo.com.in', 'yahoo.com.mx', 'yahoo.com.net', 'yahoo.com.sg', 'yahoo.com.tw',
    'yahoo.com.vn', 'yahoo.de', 'yahoo.gr', 'yahoo.ie', 'yahoo.in', 'yahoo.it', 'yahoo.se',
]

# Same From_domain derivation as config.DRUID_QUERY_TEMPLATE, used here as a filter so these
# numbers reconcile with the rest of the Pulsation page.
_FROM_DOMAIN_EXPR = (
    'MV_OFFSET(STRING_TO_MV(LOOKUP("extended_attributes.adapter_uuid", '
    "'accountadapters_uuid-to-accountadapters_from_address'), '@'), 1)"
)
_ADAPTER_NAME_EXPR = (
    'LOOKUP(LOOKUP("extended_attributes.adapter_uuid", '
    "'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name')"
)

# Metrics that re-aggregate exactly by summing daily rows.
ADDITIVE_METRICS = [
    'sent_count', 'delivered_count', 'click_count', 'bounce_count',
    'spam_report_count', 'unsubscribe_count',
]
# HLL approximations: summing daily values overstates the true range unique count.
APPROX_METRICS = [
    'unique_open_count_user', 'unique_open_count_pre_fetch', 'unique_open_count_proxy',
    'unique_soft_bounce_count',
]
ALL_METRICS = ADDITIVE_METRICS + APPROX_METRICS

RANGE_PRESETS = {'yesterday', 'past_24h', 'past_7_days', 'past_30_days', 'custom'}

# Everything not in ISP_DOMAINS is aggregated under this label so the table accounts for
# all of a domain's traffic rather than just the listed providers.
OTHER_BUCKET_LABEL = 'Other'


def _isp_in_clause() -> str:
    return ', '.join("'{}'".format(domain.replace("'", "''")) for domain in ISP_DOMAINS)


def _isp_bucket_expr() -> str:
    """
    Bucket every recipient domain outside ISP_DOMAINS into a single 'Other' row.

    Applied as a projection rather than a WHERE filter so no traffic is dropped: the
    table's total then reconciles with the Pulsation total for the same domain and range,
    instead of silently under-reporting by whatever fell outside the list. NULL and empty
    email_domain fall into 'Other' via the ELSE branch.
    """
    return (
        f"CASE WHEN email_domain IN ({_isp_in_clause()}) "
        f"THEN email_domain ELSE '{OTHER_BUCKET_LABEL}' END"
    )


def isp_list_fingerprint() -> str:
    """
    Short hash of the ISP list.

    'Other' means "everything not in ISP_DOMAINS", so cached rows are only comparable to
    rows computed from the same list. Stored with each cached day and checked on read, so
    editing ISP_DOMAINS invalidates affected days rather than silently mixing two
    definitions of 'Other' inside one range.
    """
    joined = ','.join(sorted(domain.lower() for domain in ISP_DOMAINS))
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()[:16]


def _build_query(sending_domain: str, start: str, end: str, bucket_by_day: bool) -> str:
    """
    Build the email-domain stats query.

    bucket_by_day adds a per-day dimension so one query fills the cache for every day in
    the range; without it the whole range is aggregated in one pass (used for the rolling
    24h window, where the HLL uniques are then exact).
    """
    safe_domain = sending_domain.replace("'", "''")
    day_select = "TIME_FLOOR(\"__time\", 'P1D') AS report_day,\n  " if bucket_by_day else ''
    day_group = "TIME_FLOOR(\"__time\", 'P1D'),\n  " if bucket_by_day else ''

    return f"""
SELECT
  {day_select}{_ADAPTER_NAME_EXPR} AS "adapter_name",
  {_isp_bucket_expr()} AS email_domain,
  sum(case action when 'sent' then "count" else null end) as sent_count,
  sum(case action when 'delivered' then "count" else null end) as delivered_count,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='open' AND "extended_attributes.opened_by" = 'user' then "message_distinct" else null end) as unique_open_count_user,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='open' AND "extended_attributes.opened_by" = 'pre-fetch' then "message_distinct" else null end) as unique_open_count_pre_fetch,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='open' AND "extended_attributes.opened_by" = 'proxy' then "message_distinct" else null end) as unique_open_count_proxy,
  sum(case action when 'click' then "count" else null end) as click_count,
  sum(case action when 'bounce' then "count" else null end) as bounce_count,
  APPROX_COUNT_DISTINCT_DS_HLL(case action when 'soft_bounce' then "message_distinct" else null end) as unique_soft_bounce_count,
  sum(case action when 'spam_report' then "count" else null end) as spam_report_count,
  sum(case action when 'unsubscribe' then "count" else null end) as unsubscribe_count
FROM ucts_1
WHERE "__time" >= TIMESTAMP '{start}'
  AND "__time" < TIMESTAMP '{end}'
  AND "extended_attributes.adapter_uuid" IS NOT NULL
  AND LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address') IS NOT NULL
  AND {_ADAPTER_NAME_EXPR} IN ('Sparkpost','Mailgun','Sendgrid')
  AND {_FROM_DOMAIN_EXPR} = '{safe_domain}'
GROUP BY
  {day_group}{_isp_bucket_expr()},
  {_ADAPTER_NAME_EXPR}
"""


def init_email_domain_stats_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_domain_daily_stats (
            sending_domain TEXT NOT NULL,
            region TEXT NOT NULL,
            esp TEXT NOT NULL,
            email_domain TEXT NOT NULL,
            report_date TEXT NOT NULL,
            sent_count INTEGER DEFAULT 0,
            delivered_count INTEGER DEFAULT 0,
            unique_open_count_user INTEGER DEFAULT 0,
            unique_open_count_pre_fetch INTEGER DEFAULT 0,
            unique_open_count_proxy INTEGER DEFAULT 0,
            click_count INTEGER DEFAULT 0,
            bounce_count INTEGER DEFAULT 0,
            unique_soft_bounce_count INTEGER DEFAULT 0,
            spam_report_count INTEGER DEFAULT 0,
            unsubscribe_count INTEGER DEFAULT 0,
            fetched_at TEXT,
            PRIMARY KEY (sending_domain, region, esp, email_domain, report_date)
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_email_domain_daily_lookup '
        'ON email_domain_daily_stats (sending_domain, region, report_date)'
    )
    # Records which (domain, region, date) combinations have been fetched, so a day that
    # genuinely had zero traffic is not re-queried on every request.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_domain_fetch_log (
            sending_domain TEXT NOT NULL,
            region TEXT NOT NULL,
            report_date TEXT NOT NULL,
            row_count INTEGER DEFAULT 0,
            fetched_at TEXT,
            PRIMARY KEY (sending_domain, region, report_date)
        )
    ''')
    # Which ISP list produced each cached day. Rows written under a different list define
    # 'Other' differently and must not be mixed into one range.
    _ensure_column(cursor, 'email_domain_daily_stats', 'isp_list_hash', 'TEXT')
    _ensure_column(cursor, 'email_domain_fetch_log', 'isp_list_hash', 'TEXT')
    conn.commit()
    conn.close()


def _ensure_column(cursor, table_name: str, column_name: str, definition: str) -> None:
    columns = [row[1] for row in cursor.execute(f'PRAGMA table_info({table_name})').fetchall()]
    if column_name not in columns:
        cursor.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}')


def _now_iso() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def resolve_domain_regions(sending_domain: str) -> List[str]:
    """
    Which Druid region(s) a sending domain has sent from, per stored Pulsation history.

    Returns both regions when the domain appears in both, and both as a fallback when the
    domain is unknown locally — querying an extra broker is cheap and returns nothing,
    whereas guessing wrong would silently show an empty table.
    """
    try:
        conn = sqlite3.connect(HISTORY_DB_PATH)
        rows = conn.execute(
            'SELECT DISTINCT region FROM daily_metrics WHERE LOWER(from_domain) = ?',
            (sending_domain.lower(),)
        ).fetchall()
        conn.close()
        regions = [row[0] for row in rows if row[0] in ('US', 'EU')]
        return regions or ['US', 'EU']
    except sqlite3.Error:
        return ['US', 'EU']


def _broker_for_region(region: str) -> str:
    return DRUID_US_BROKER if region == 'US' else DRUID_EU_BROKER


def resolve_range(range_type: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> Dict:
    """
    Turn a preset or custom range into query bounds.

    Presets other than past_24h are aligned to whole days so they can be served from the
    day-level cache. past_24h is a rolling window that cannot be, so it is marked
    cacheable=False and always goes to Druid.
    """
    today = datetime.utcnow().date()

    if range_type == 'past_24h':
        end = datetime.utcnow()
        start = end - timedelta(hours=24)
        # Druid TIMESTAMP literals use 'YYYY-MM-DD HH:MM:SS'; an ISO 'T' separator is a 400.
        return {
            'range_type': range_type,
            'start': start.strftime('%Y-%m-%d %H:%M:%S'),
            'end': end.strftime('%Y-%m-%d %H:%M:%S'),
            'dates': [],
            'cacheable': False,
            'label': 'Past 24 Hours'
        }

    if range_type == 'yesterday':
        # A single completed day. Day-aligned, so it caches like the other presets
        # (unlike past_24h, which is a rolling window and cannot be cached).
        start_date = today - timedelta(days=1)
        end_date = today
        label = f'Yesterday ({start_date.isoformat()})'
    elif range_type == 'past_7_days':
        start_date, end_date = today - timedelta(days=7), today
        label = 'Past 7 Days'
    elif range_type == 'past_30_days':
        start_date, end_date = today - timedelta(days=30), today
        label = 'Past 30 Days'
    elif range_type == 'custom':
        if not from_date or not to_date:
            raise ValueError('custom range requires from_date and to_date')
        start_date = datetime.strptime(from_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(to_date, '%Y-%m-%d').date()
        if end_date < start_date:
            raise ValueError('to_date must not be before from_date')
        # Treat the custom range as inclusive of to_date, which is what a date picker implies.
        end_date = end_date + timedelta(days=1)
        label = f'{start_date.isoformat()} to {(end_date - timedelta(days=1)).isoformat()}'
    else:
        raise ValueError(f'Unknown range type: {range_type}. Expected one of {sorted(RANGE_PRESETS)}')

    span_days = (end_date - start_date).days
    if span_days > RETENTION_DAYS + 1:
        raise ValueError(
            f'Range spans {span_days} days; the maximum is {RETENTION_DAYS} '
            f'(local history is kept for {RETENTION_DAYS} days)'
        )

    dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(span_days)]
    return {
        'range_type': range_type,
        'start': start_date.isoformat(),
        'end': end_date.isoformat(),
        'dates': dates,
        'cacheable': True,
        'label': label
    }


def _cached_dates(sending_domain: str, region: str, dates: List[str]) -> set:
    if not dates:
        return set()
    conn = sqlite3.connect(DB_PATH)
    placeholders = ', '.join('?' for _ in dates)
    # Only days computed from the current ISP list count as cached; a list change makes
    # affected days look missing so they are refetched under the new definition of 'Other'.
    rows = conn.execute(
        f'SELECT report_date FROM email_domain_fetch_log '
        f'WHERE sending_domain = ? AND region = ? AND isp_list_hash = ? '
        f'AND report_date IN ({placeholders})',
        [sending_domain.lower(), region, isp_list_fingerprint()] + dates
    ).fetchall()
    conn.close()
    return {row[0] for row in rows}


def _store_daily_rows(sending_domain: str, region: str, rows: List[Dict], dates_fetched: List[str]) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = _now_iso()
    fingerprint = isp_list_fingerprint()
    columns = ', '.join(ALL_METRICS)
    placeholders = ', '.join('?' for _ in ALL_METRICS)

    # Clear each refetched day first. INSERT OR REPLACE alone would leave behind rows for
    # ISPs dropped from the list, which are now folded into 'Other' and would double-count.
    if dates_fetched:
        date_placeholders = ', '.join('?' for _ in dates_fetched)
        cursor.execute(
            f'DELETE FROM email_domain_daily_stats '
            f'WHERE sending_domain = ? AND region = ? AND report_date IN ({date_placeholders})',
            [sending_domain.lower(), region] + dates_fetched
        )

    stored = 0
    for row in rows:
        cursor.execute(
            f'INSERT OR REPLACE INTO email_domain_daily_stats '
            f'(sending_domain, region, esp, email_domain, report_date, {columns}, fetched_at, isp_list_hash) '
            f'VALUES (?, ?, ?, ?, ?, {placeholders}, ?, ?)',
            [
                sending_domain.lower(), region, row['esp'], row['email_domain'], row['report_date'],
                *[int(row.get(metric) or 0) for metric in ALL_METRICS],
                now, fingerprint
            ]
        )
        stored += 1

    per_date_counts = {}
    for row in rows:
        per_date_counts[row['report_date']] = per_date_counts.get(row['report_date'], 0) + 1
    # Log every requested date, including those with no rows, so empty days are not refetched.
    for date in dates_fetched:
        cursor.execute(
            'INSERT OR REPLACE INTO email_domain_fetch_log '
            '(sending_domain, region, report_date, row_count, fetched_at, isp_list_hash) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (sending_domain.lower(), region, date, per_date_counts.get(date, 0), now, fingerprint)
        )

    conn.commit()
    conn.close()
    return stored


def _load_daily_rows(sending_domain: str, region: str, dates: List[str]) -> List[Dict]:
    if not dates:
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    placeholders = ', '.join('?' for _ in dates)
    # Mirror _cached_dates: never load rows written under a different ISP list, or one
    # range could blend two different definitions of 'Other'.
    rows = conn.execute(
        f'SELECT * FROM email_domain_daily_stats '
        f'WHERE sending_domain = ? AND region = ? AND isp_list_hash = ? '
        f'AND report_date IN ({placeholders})',
        [sending_domain.lower(), region, isp_list_fingerprint()] + dates
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def prune_old_stats(retention_days: int = RETENTION_DAYS) -> int:
    cutoff = (datetime.utcnow().date() - timedelta(days=retention_days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM email_domain_daily_stats WHERE report_date < ?', (cutoff,))
    deleted = cursor.rowcount
    cursor.execute('DELETE FROM email_domain_fetch_log WHERE report_date < ?', (cutoff,))
    conn.commit()
    conn.close()
    return deleted


def _execute_query_strict(broker_url: str, query: str, region: str) -> List[Dict]:
    """
    Run a Druid query, raising on failure instead of returning an empty list.

    druid_service.execute_druid_query catches RequestException and returns [], which makes a
    broker error or malformed SQL indistinguishable from "this domain sent nothing". That
    ambiguity is not acceptable here, and the existing helper is left untouched so nothing
    already depending on it changes behaviour.
    """
    try:
        response = requests.post(
            broker_url,
            headers={'Content-Type': 'application/json'},
            data=json.dumps({'query': query}),
            timeout=DRUID_TIMEOUT_SECONDS
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f'{region} Druid broker unreachable: {exc}') from exc

    if response.status_code != 200:
        detail = (response.text or '')[:300].replace('\n', ' ')
        raise RuntimeError(f'{region} Druid query failed ({response.status_code}): {detail}')

    payload = response.json()
    if isinstance(payload, dict) and payload.get('error'):
        raise RuntimeError(f'{region} Druid error: {payload.get("errorMessage") or payload["error"]}')
    return payload if isinstance(payload, list) else []


def _fetch_from_druid(sending_domain: str, region: str, start: str, end: str, bucket_by_day: bool) -> List[Dict]:
    """Run the stats query for one region. Raises on broker/query failure."""
    query = _build_query(sending_domain, start, end, bucket_by_day)
    results = _execute_query_strict(_broker_for_region(region), query, region)

    if not results:
        return []

    normalized = []
    for row in results:
        record = {
            'esp': row.get('adapter_name') or 'Unknown',
            'email_domain': row.get('email_domain') or 'Unknown',
            'region': region,
        }
        if bucket_by_day:
            report_day = row.get('report_day') or ''
            record['report_date'] = str(report_day)[:10]
        for metric in ALL_METRICS:
            record[metric] = int(row.get(metric) or 0)
        normalized.append(record)
    return normalized


def _aggregate(rows: List[Dict]) -> List[Dict]:
    """
    Collapse daily rows to one row per email domain.

    ESP and region are reported once for the whole result rather than per row, so they are
    rolled up here; a sending domain using two ESPs would otherwise produce duplicate
    email_domain rows with nothing to distinguish them.
    """
    grouped: Dict[str, Dict] = {}
    for row in rows:
        key = row.get('email_domain', '')
        target = grouped.get(key)
        if target is None:
            target = {'email_domain': key}
            for metric in ALL_METRICS:
                target[metric] = 0
            grouped[key] = target
        for metric in ALL_METRICS:
            target[metric] += int(row.get(metric) or 0)

    result = list(grouped.values())
    for row in result:
        sent = row['sent_count'] or 0
        row['delivery_rate'] = round(row['delivered_count'] / sent * 100, 2) if sent else 0.0
        row['bounce_rate'] = round(row['bounce_count'] / sent * 100, 2) if sent else 0.0
        row['spam_rate'] = round(row['spam_report_count'] / sent * 100, 4) if sent else 0.0
        row['unsub_rate'] = round(row['unsubscribe_count'] / sent * 100, 4) if sent else 0.0
    # 'Other' is an aggregate of many providers, not a peer of the named ones, so it is
    # pinned last regardless of volume.
    result.sort(key=lambda item: (
        item['email_domain'] == OTHER_BUCKET_LABEL,
        -item['sent_count'],
        item['email_domain']
    ))
    return result


def get_email_domain_stats(
    sending_domain: str,
    range_type: str = 'past_7_days',
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    region: Optional[str] = None,
    force_refresh: bool = False,
    cached_only: bool = False
) -> Dict:
    """
    ISP-level sending stats for one sending domain over a date range.

    Date-aligned ranges are served from the local day-level cache, fetching only missing
    days from Druid. The rolling 24h window always queries Druid.

    cached_only returns whatever is already cached and never queries Druid, listing the
    days it could not cover in `cache['missing_dates']`. The agent uses this so answering a
    question costs no Druid load unless a live pull is explicitly requested.
    """
    sending_domain = (sending_domain or '').strip().lower()
    if not sending_domain:
        raise ValueError('sending_domain is required')

    init_email_domain_stats_db()
    window = resolve_range(range_type, from_date, to_date)
    regions = [region] if region in ('US', 'EU') else resolve_domain_regions(sending_domain)

    all_rows: List[Dict] = []
    errors: List[str] = []
    missing_dates: List[str] = []
    days_from_cache = 0
    days_from_druid = 0

    for region_name in regions:
        try:
            if not window['cacheable']:
                # Rolling window: single aggregate query, so the HLL uniques are exact.
                # There is nothing cached to fall back on, so cached_only cannot serve it.
                if cached_only:
                    errors.append(
                        f'{region_name}: the rolling 24h window cannot be served from cache '
                        '(it is not day-aligned); a live Druid query is required'
                    )
                    continue
                all_rows.extend(_fetch_from_druid(
                    sending_domain, region_name, window['start'], window['end'], bucket_by_day=False
                ))
                days_from_druid += 1
                continue

            dates = window['dates']
            cached = set() if force_refresh else _cached_dates(sending_domain, region_name, dates)
            missing = [date for date in dates if date not in cached]

            if missing and cached_only:
                # Report the gap and serve what is cached rather than querying Druid.
                missing_dates.extend(missing)
                days_from_cache += len(dates) - len(missing)
                all_rows.extend(_load_daily_rows(sending_domain, region_name, dates))
                continue

            if missing:
                # One query covering the missing span; extra days it returns are stored too.
                fetch_start = min(missing)
                fetch_end = (datetime.strptime(max(missing), '%Y-%m-%d').date() + timedelta(days=1)).isoformat()
                fetched = _fetch_from_druid(
                    sending_domain, region_name, fetch_start, fetch_end, bucket_by_day=True
                )
                _store_daily_rows(sending_domain, region_name, fetched, missing)
                days_from_druid += len(missing)

            days_from_cache += len(dates) - len(missing)
            all_rows.extend(_load_daily_rows(sending_domain, region_name, dates))
        except Exception as exc:
            errors.append(f'{region_name}: {exc}')

    prune_old_stats()
    table_rows = _aggregate(all_rows)
    # ESP and region are reported once for the whole result instead of per row.
    esps = sorted({row.get('esp') for row in all_rows if row.get('esp')})
    regions_seen = sorted({row.get('region') for row in all_rows if row.get('region')})

    totals = {metric: sum(row[metric] for row in table_rows) for metric in ALL_METRICS}
    sent = totals['sent_count']
    totals['delivery_rate'] = round(totals['delivered_count'] / sent * 100, 2) if sent else 0.0
    totals['bounce_rate'] = round(totals['bounce_count'] / sent * 100, 2) if sent else 0.0

    return {
        'status': 'success' if not errors else ('partial_success' if table_rows else 'error'),
        'sending_domain': sending_domain,
        'rows': table_rows,
        'totals': totals,
        'row_count': len(table_rows),
        'window': {
            'range_type': window['range_type'],
            'label': window['label'],
            'start': window['start'],
            'end': window['end'],
            'day_count': len(window['dates']),
        },
        # regions_queried is which brokers were asked; regions is what actually returned data.
        'regions': regions_seen or regions,
        'regions_queried': regions,
        'esps': esps,
        'cache': {
            'days_from_cache': days_from_cache,
            'days_from_druid': days_from_druid,
            'retention_days': RETENTION_DAYS,
            'cached_only': cached_only,
            # Days the caller asked for that are not cached. Non-empty only under
            # cached_only, where they were deliberately not fetched.
            'missing_dates': sorted(set(missing_dates)),
        },
        # True when values came from summed daily rows, making the unique_* columns
        # slight overcounts. False for the rolling 24h window, where they are exact.
        'uniques_are_summed': bool(window['cacheable']),
        'approx_metrics': APPROX_METRICS,
        'errors': errors,
    }


def export_email_domain_stats_csv(
    sending_domain: str,
    range_type: str = 'past_7_days',
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    region: Optional[str] = None
) -> str:
    """
    Render the same rows the table shows as CSV.

    Served from cache like the table, so exporting straight after viewing does not re-query
    Druid. Approximate columns are named so the caveat survives outside the UI.
    """
    payload = get_email_domain_stats(
        sending_domain,
        range_type=range_type,
        from_date=from_date,
        to_date=to_date,
        region=region
    )

    approx_suffix = ' (summed daily, approx)' if payload['uniques_are_summed'] else ''
    output = io.StringIO()
    writer = csv.writer(output)

    # Header comment rows so a downloaded file still states what it covers.
    writer.writerow([f'# Sending domain: {payload["sending_domain"]}'])
    # Only the label — window start/end are exclusive-end query bounds, which read as an
    # off-by-one to anyone opening the file.
    day_count = payload['window']['day_count']
    span = f' ({day_count} days)' if day_count else ''
    writer.writerow([f'# Range: {payload["window"]["label"]}{span}'])
    writer.writerow([f'# Region(s): {", ".join(payload["regions"]) or "n/a"}'])
    writer.writerow([f'# ESP(s): {", ".join(payload["esps"]) or "n/a"}'])
    writer.writerow([f'# Generated (UTC): {_now_iso()}'])
    if payload['uniques_are_summed']:
        writer.writerow(['# Note: unique open/soft bounce columns are summed from daily figures and count'])
        writer.writerow(['#       a recipient active on multiple days more than once. Other columns are exact.'])
    if payload['errors']:
        writer.writerow([f'# Errors: {"; ".join(payload["errors"])}'])
    writer.writerow([])

    writer.writerow([
        'Email Domain', 'Sent', 'Delivered', 'Delivery %',
        'Bounces', 'Bounce %', f'Unique Soft Bounces{approx_suffix}',
        f'Unique Opens (user){approx_suffix}', f'Unique Opens (pre-fetch){approx_suffix}',
        f'Unique Opens (proxy){approx_suffix}', 'Clicks', 'Spam Reports', 'Unsubscribes',
        'Spam %', 'Unsub %'
    ])

    for row in payload['rows']:
        writer.writerow([
            row['email_domain'],
            row['sent_count'], row['delivered_count'], row['delivery_rate'],
            row['bounce_count'], row['bounce_rate'], row['unique_soft_bounce_count'],
            row['unique_open_count_user'], row['unique_open_count_pre_fetch'],
            row['unique_open_count_proxy'], row['click_count'],
            row['spam_report_count'], row['unsubscribe_count'],
            row['spam_rate'], row['unsub_rate']
        ])

    totals = payload['totals']
    writer.writerow([
        'TOTAL', totals['sent_count'], totals['delivered_count'], totals['delivery_rate'],
        totals['bounce_count'], totals['bounce_rate'], totals['unique_soft_bounce_count'],
        totals['unique_open_count_user'], totals['unique_open_count_pre_fetch'],
        totals['unique_open_count_proxy'], totals['click_count'],
        totals['spam_report_count'], totals['unsubscribe_count'], '', ''
    ])

    return output.getvalue()
