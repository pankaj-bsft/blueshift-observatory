"""
Bounce reasons from Athena, for one sending domain + one recipient mailbox provider.

The ESP APIs report their own bounce strings; this reads the raw user_actions events
in Athena, which is the source of truth. The two fields we need live inside the
`extended_attributes` JSON string:

    reason        -> "smtp;550 5.4.317 Message expired, cannot connect to ..."
    from_address  -> "updates@send-edu.usnews.com"  (sending domain is after the @)

Cost control matters here: the table is raw JSON (TextInputFormat), so a single
account-day scans roughly 1.8 GB. Every query therefore prunes on all four partition
keys — year/month/day and account — before any JSON parsing happens. The account is
resolved from the local domain->account mapping, without which this would be
unaffordable.

Results are cached per (sending_domain, email_domain, date) so clicking the same cell
twice costs nothing.
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from data_paths import data_path

DB_PATH = data_path('athena_bounce_reasons.db')
MAPPING_DB_PATH = data_path('account_mappings.db')

ATHENA_REGION = os.getenv('ATHENA_REGION', 'us-west-2')
ATHENA_WORKGROUP = os.getenv('ATHENA_WORKGROUP', 'primary')
ATHENA_DATABASE = os.getenv('ATHENA_DATABASE', 'bsft_customers')
ATHENA_TABLE = os.getenv('ATHENA_TABLE', 'user_actions')
ATHENA_OUTPUT = os.getenv('ATHENA_OUTPUT', '')

# Days beyond this need explicit confirmation from the caller, because each
# account-day scans ~1.8 GB of raw JSON.
LARGE_RANGE_DAYS = 7
MAX_RANGE_DAYS = 31
RETENTION_DAYS = 30
POLL_TIMEOUT_SECONDS = 180
BOUNCE_ACTIONS = ('bounce', 'soft_bounce')

MAX_REASON_ROWS = 200

# The per-message sending domain, taken from the actual from_address.
FROM_DOMAIN_SQL = "split_part(json_extract_scalar(extended_attributes, '$.from_address'), '@', 2)"


# Grouping key for bounce reasons.
#
# Raw SMTP reasons carry a per-message session id and wrap across lines, so grouping
# on them raw yields one row per message: a real query returned 125 rows of count 1
# for what were actually four distinct reasons totalling 8,750 bounces. Collapsing
# whitespace, dropping URLs and keeping the leading 80 characters retains the SMTP
# code and the human-readable sentence while discarding the variable tail. The full
# text of one example is kept alongside as sample_reason.
# Recipient addresses appear inside some providers' reasons (Yahoo quotes the
# address back), so they are masked before truncation or every message forms its
# own group again.
REASON_KEY_SQL = (
    "trim(substr("
    "regexp_replace("                     # 4. tidy any double spaces left behind
    "regexp_replace("                     # 3. mask recipient addresses
    "regexp_replace("                     # 2. drop URLs
    "regexp_replace("                     # 1. collapse newlines/whitespace
    "json_extract_scalar(extended_attributes, '$.reason'), '\\s+', ' '), "
    "'https?://\\S*', ''), "
    "'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', '<recipient>'), "
    "'\\s+', ' ')"
    ", 1, 80))"
)

# Only identifiers we build ourselves go into the SQL text; every caller-supplied
# value is bound as a parameter via Athena's ExecutionParameters.
_SAFE_IDENT = re.compile(r'^[A-Za-z0-9_]+$')


class AthenaBounceError(RuntimeError):
    """Human-readable failure the API and UI can relay."""


def athena_enabled() -> bool:
    return os.getenv('ATHENA_ENABLED', '1').strip().lower() not in ('0', 'false', 'no')


def _client():
    """
    Build an Athena client using boto3's default credential chain.

    No profile is named here on purpose: locally that resolves the AWS_PROFILE SSO
    session, and on EC2 it falls through to the instance role. Same code both places.
    """
    try:
        import boto3
    except ImportError as exc:
        raise AthenaBounceError('boto3 is not installed; run pip install -r requirements.txt') from exc
    try:
        return boto3.client('athena', region_name=ATHENA_REGION)
    except Exception as exc:
        raise AthenaBounceError(f'Could not create an Athena client: {exc}') from exc


def _friendly_aws_error(exc: Exception) -> str:
    """Turn the common credential failures into something actionable."""
    text = str(exc)
    if 'SSO session' in text or 'sso' in text.lower() and 'expired' in text.lower():
        return ('The AWS SSO session has expired. Run: aws sso login --profile '
                f"{os.getenv('AWS_PROFILE', 'cli-prod-oregon')}")
    if 'Unable to locate credentials' in text:
        return ('No AWS credentials available. Locally run aws sso login; on EC2 this '
                'should come from the instance role.')
    if 'AccessDenied' in text or 'not authorized' in text:
        return f'AWS denied the request: {text[:300]}'
    return text[:400]


# --------------------------------------------------------------------------
# Account resolution — the partition key that makes this affordable
# --------------------------------------------------------------------------

def _mapped_account(sending_domain: str) -> Optional[str]:
    """The account_name our local mapping has for a sending domain, if any."""
    domain = (sending_domain or '').strip().lower()
    if not domain:
        return None
    try:
        conn = sqlite3.connect(f'file:{MAPPING_DB_PATH}?mode=ro', uri=True)
        row = conn.execute(
            'SELECT account_name FROM domain_account_mapping WHERE LOWER(sending_domain) = ?',
            (domain,)
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    return (row[0] or '').strip().lower() if row and row[0] else None


def parent_domains(domain: str) -> List[str]:
    """example.co.uk from a.b.example.co.uk — progressively shorter suffixes."""
    parts = (domain or '').strip().lower().split('.')
    return ['.'.join(parts[i:]) for i in range(len(parts) - 1)]


# Second-level suffixes where the registrable domain needs three labels, so
# x.example.co.uk resolves to example.co.uk rather than the useless co.uk.
_MULTI_PART_SUFFIXES = {
    'co', 'com', 'net', 'org', 'gov', 'edu', 'ac', 'mil', 'or', 'ne', 'go',
}


def org_domain(domain: str) -> str:
    """
    The registrable domain: mail.example.com -> example.com,
    x.example.co.uk -> example.co.uk.

    Used to match sibling sending domains within the same organisation. Taking the
    shortest parent instead would give 'co.uk' for UK domains and match everything.
    """
    parts = (domain or '').strip().lower().split('.')
    if len(parts) <= 2:
        return '.'.join(parts)
    if len(parts) >= 3 and parts[-2] in _MULTI_PART_SUFFIXES and len(parts[-1]) <= 3:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def _partition_exists(account: str, date: str) -> bool:
    """
    Glue metadata check — free, and far cheaper than discovering it by scanning.

    Only a genuine EntityNotFoundException counts as "absent". Anything else (expired
    credentials, denied permissions, throttling) is raised: catching everything here
    made an expired SSO session look like "no data partition found for this domain",
    which sent us hunting for a mapping problem that did not exist.
    """
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError

    glue = boto3.client('glue', region_name=ATHENA_REGION)
    y, m, d = date.split('-')
    try:
        glue.get_partition(DatabaseName=ATHENA_DATABASE, TableName=ATHENA_TABLE,
                           PartitionValues=[y, m, d, account])
        return True
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') == 'EntityNotFoundException':
            return False
        raise AthenaBounceError(
            f'Could not check Athena partitions: {_friendly_aws_error(exc)}'
        ) from exc
    except BotoCoreError as exc:
        raise AthenaBounceError(
            f'Could not reach AWS to check partitions: {_friendly_aws_error(exc)}'
        ) from exc


def account_candidates(sending_domain: str) -> List[str]:
    """
    Ordered, de-duplicated account-partition candidates for a sending domain.

    Shared with the failure message so the user is told exactly what was attempted.
    The bare public suffix (com.au, co.uk) is dropped — it is never an account and
    only made the error noisier.
    """
    domain = (sending_domain or '').strip().lower()
    if not domain:
        return []
    org = org_domain(domain)
    # Domain-derived candidates come FIRST, the mapped account last. Partitions are
    # named after the sending domain's organisation, and when both exist the mapped
    # account can be the wrong one: mail.adventuremoto.com.au maps to mxstore.com, but
    # its 7,037 bounces live in the adventuremoto.com.au partition while mxstore.com
    # holds only mail.mxstore.com.au. The mapping is still a useful fallback for cases
    # like brokerage.pivothealth.com, whose data really is under healthcare.com.
    raw = [domain]
    raw += [p for p in parent_domains(domain) if p.count('.') >= org.count('.')]
    raw.append(_mapped_account(domain))
    seen, ordered = set(), []
    for c in raw:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def resolve_account(sending_domain: str, date: Optional[str] = None) -> Optional[str]:
    """
    Find the Athena `account` partition value for a sending domain.

    The local mapping's account_name is usually right but not always: crm.
    bestwesternblackoak.com is mapped to fueltravel-sales.com, while the partition is
    actually bestwesternblackoak.com. Querying the wrong partition returns nothing and
    looks like "no bounces", so candidates are checked against Glue (metadata, free)
    and the first that really exists is used.

    Candidates, in order: the mapped account, the domain itself, then each parent
    domain (mail.x.co.uk -> x.co.uk).
    """
    domain = (sending_domain or '').strip().lower()
    if not domain:
        return None

    ordered = account_candidates(domain)

    if not date:
        return ordered[0] if ordered else None

    for c in ordered:
        if _partition_exists(c, date):
            return c
    return None


# --------------------------------------------------------------------------
# Date handling
# --------------------------------------------------------------------------

def resolve_dates(from_date: str, to_date: str = '') -> List[str]:
    """Inclusive list of YYYY-MM-DD strings, validated against the range cap."""
    if not from_date:
        raise AthenaBounceError('A date is required.')
    try:
        start = datetime.strptime(from_date, '%Y-%m-%d').date()
        end = datetime.strptime(to_date, '%Y-%m-%d').date() if to_date else start
    except ValueError as exc:
        raise AthenaBounceError(f'Dates must be YYYY-MM-DD: {exc}') from exc
    if end < start:
        raise AthenaBounceError('to_date must not be before from_date.')
    span = (end - start).days + 1
    if span > MAX_RANGE_DAYS:
        raise AthenaBounceError(
            f'{span} days requested; the maximum is {MAX_RANGE_DAYS}. '
            'Each account-day scans roughly 1.8 GB, so long ranges are slow and costly.'
        )
    return [(start + timedelta(days=i)).isoformat() for i in range(span)]


# --------------------------------------------------------------------------
# Query construction
# --------------------------------------------------------------------------

def build_query(dates: List[str]) -> str:
    """
    Bounce reasons grouped by reason and bounce type, for one account/domain/provider.

    Partition predicates come first so pruning happens before any JSON parsing. The
    (year, month, day) tuples are an OR of exact partitions rather than a date range,
    which keeps the pruning exact when a range crosses a month boundary.
    """
    for ident in (ATHENA_DATABASE, ATHENA_TABLE):
        if not _SAFE_IDENT.match(ident or ''):
            raise AthenaBounceError(f'Unsafe Athena identifier configured: {ident!r}')

    parts = " OR ".join(
        "(year = '{}' AND month = '{}' AND day = '{}')".format(*d.split('-'))
        for d in dates
    )
    actions = ", ".join(f"'{a}'" for a in BOUNCE_ACTIONS)

    return f"""
SELECT
  {REASON_KEY_SQL} AS bounce_reason,
  action AS bounce_type,
  count(*) AS bounces,
  max(json_extract_scalar(extended_attributes, '$.reason')) AS sample_reason,
  array_join(array_agg(DISTINCT {FROM_DOMAIN_SQL}), ', ') AS sending_domains
FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
WHERE ({parts})
  AND account = ?
  AND action IN ({actions})
  AND email_domain = ?
  AND ({FROM_DOMAIN_SQL} = ? OR {FROM_DOMAIN_SQL} LIKE ?)
GROUP BY 1, 2
ORDER BY bounces DESC
LIMIT {MAX_REASON_ROWS}
""".strip()


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def run_query(sql: str, params: List[str]) -> Dict:
    """Start, poll and fetch one Athena query. Raises rather than returning empty."""
    if not ATHENA_OUTPUT:
        raise AthenaBounceError('ATHENA_OUTPUT is not set (S3 location for query results).')

    client = _client()
    try:
        started = client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={'Database': ATHENA_DATABASE},
            ResultConfiguration={'OutputLocation': ATHENA_OUTPUT},
            WorkGroup=ATHENA_WORKGROUP,
            ExecutionParameters=params,
        )
    except Exception as exc:
        raise AthenaBounceError(f'Athena rejected the query: {_friendly_aws_error(exc)}') from exc

    qid = started['QueryExecutionId']
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    state, detail = 'QUEUED', {}
    while time.time() < deadline:
        time.sleep(2)
        try:
            detail = client.get_query_execution(QueryExecutionId=qid)['QueryExecution']
        except Exception as exc:
            raise AthenaBounceError(f'Could not check query status: {_friendly_aws_error(exc)}') from exc
        state = detail['Status']['State']
        if state in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            break
    else:
        try:
            client.stop_query_execution(QueryExecutionId=qid)
        except Exception:
            pass
        raise AthenaBounceError(
            f'Athena query timed out after {POLL_TIMEOUT_SECONDS}s (id {qid}). '
            'Try a shorter date range.'
        )

    stats = detail.get('Statistics', {}) or {}
    if state != 'SUCCEEDED':
        reason = (detail.get('Status', {}) or {}).get('StateChangeReason') or state
        raise AthenaBounceError(f'Athena query {state}: {reason}')

    rows = []
    try:
        paginator = client.get_paginator('get_query_results')
        first = True
        for page in paginator.paginate(QueryExecutionId=qid):
            for r in page['ResultSet']['Rows']:
                if first:  # the first row is the header
                    first = False
                    continue
                vals = [c.get('VarCharValue') for c in r['Data']]
                # Pad so a short row cannot raise IndexError mid-page.
                vals += [None] * (5 - len(vals))
                rows.append({
                    'bounce_reason': vals[0] or 'Unknown',
                    'bounce_type': vals[1] or 'unknown',
                    'bounces': int(vals[2] or 0),
                    'sample_reason': vals[3] or vals[0] or '',
                    'sending_domains': vals[4] or '',
                })
    except Exception as exc:
        raise AthenaBounceError(f'Could not read query results: {_friendly_aws_error(exc)}') from exc

    return {
        'rows': rows,
        'query_execution_id': qid,
        'scanned_bytes': int(stats.get('DataScannedInBytes') or 0),
        'runtime_ms': int(stats.get('TotalExecutionTimeInMillis') or 0),
    }


# --------------------------------------------------------------------------
# Cache — a repeat click on the same cell must not re-scan 1.8 GB
# --------------------------------------------------------------------------

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS athena_bounce_reasons (
            sending_domain TEXT NOT NULL,
            email_domain   TEXT NOT NULL,
            report_date    TEXT NOT NULL,
            bounce_reason  TEXT NOT NULL,
            bounce_type    TEXT NOT NULL,
            bounces        INTEGER DEFAULT 0,
            sample_reason  TEXT,
            sending_domains TEXT,
            fetched_at     TEXT,
            PRIMARY KEY (sending_domain, email_domain, report_date, bounce_reason, bounce_type)
        )
    ''')
    # Records which (domain, provider, date) combinations were queried, so a day that
    # genuinely had no bounces is not re-scanned on every click.
    c.execute('''
        CREATE TABLE IF NOT EXISTS athena_bounce_fetch_log (
            sending_domain TEXT NOT NULL,
            email_domain   TEXT NOT NULL,
            report_date    TEXT NOT NULL,
            row_count      INTEGER DEFAULT 0,
            scanned_bytes  INTEGER DEFAULT 0,
            account        TEXT,
            fetched_at     TEXT,
            PRIMARY KEY (sending_domain, email_domain, report_date)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_abr_lookup ON athena_bounce_reasons '
              '(sending_domain, email_domain, report_date)')
    conn.commit()
    conn.close()


def _now_iso() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _cached_dates(sending_domain, email_domain, dates) -> set:
    if not dates:
        return set()
    conn = sqlite3.connect(DB_PATH)
    ph = ', '.join('?' for _ in dates)
    rows = conn.execute(
        f'SELECT report_date FROM athena_bounce_fetch_log '
        f'WHERE sending_domain = ? AND email_domain = ? AND report_date IN ({ph})',
        [sending_domain, email_domain] + dates
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def _store(sending_domain, email_domain, dates, rows, scanned_bytes, account='') -> int:
    """
    Store one fetch. Athena aggregates across the whole range, so per-date attribution
    is not available; rows are recorded against the first date of the range and every
    requested date is logged as fetched.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = _now_iso()
    anchor = dates[0]
    ph = ', '.join('?' for _ in dates)
    c.execute(
        f'DELETE FROM athena_bounce_reasons WHERE sending_domain = ? AND email_domain = ? '
        f'AND report_date IN ({ph})',
        [sending_domain, email_domain] + dates
    )
    stored = 0
    for r in rows:
        c.execute(
            'INSERT OR REPLACE INTO athena_bounce_reasons '
            '(sending_domain, email_domain, report_date, bounce_reason, bounce_type, bounces, sample_reason, sending_domains, fetched_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (sending_domain, email_domain, anchor, r['bounce_reason'], r['bounce_type'],
             int(r['bounces']), r.get('sample_reason', ''), r.get('sending_domains', ''), now)
        )
        stored += 1
    for d in dates:
        c.execute(
            'INSERT OR REPLACE INTO athena_bounce_fetch_log '
            '(sending_domain, email_domain, report_date, row_count, scanned_bytes, account, fetched_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (sending_domain, email_domain, d, len(rows), int(scanned_bytes), account, now)
        )
    conn.commit()
    conn.close()
    _prune()
    return stored


def _cached_account(sending_domain, email_domain, dates) -> Optional[str]:
    """The account a previous fetch used, so a cached read needs no AWS call."""
    if not dates:
        return None
    conn = sqlite3.connect(DB_PATH)
    ph = ', '.join('?' for _ in dates)
    row = conn.execute(
        f'SELECT account FROM athena_bounce_fetch_log '
        f'WHERE sending_domain = ? AND email_domain = ? AND report_date IN ({ph}) '
        f'AND account IS NOT NULL AND account != "" LIMIT 1',
        [sending_domain, email_domain] + dates
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _load(sending_domain, email_domain, dates) -> List[Dict]:
    if not dates:
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ph = ', '.join('?' for _ in dates)
    rows = conn.execute(
        f'SELECT bounce_reason, bounce_type, SUM(bounces) AS bounces, '
        f'MAX(sample_reason) AS sample_reason, MAX(sending_domains) AS sending_domains '
        f'FROM athena_bounce_reasons '
        f'WHERE sending_domain = ? AND email_domain = ? AND report_date IN ({ph}) '
        f'GROUP BY bounce_reason, bounce_type ORDER BY bounces DESC',
        [sending_domain, email_domain] + dates
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _prune(retention_days: int = RETENTION_DAYS) -> None:
    cutoff = (datetime.utcnow().date() - timedelta(days=retention_days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM athena_bounce_reasons WHERE report_date < ?', (cutoff,))
    conn.execute('DELETE FROM athena_bounce_fetch_log WHERE report_date < ?', (cutoff,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def get_bounce_reasons(sending_domain: str, email_domain: str, from_date: str,
                       to_date: str = '', force_refresh: bool = False,
                       confirm_large: bool = False) -> Dict:
    """
    Bounce reasons for one sending domain + recipient provider over a date range.

    Serves from cache when every requested day has been fetched before; otherwise runs
    one Athena query. Ranges longer than LARGE_RANGE_DAYS require confirm_large, since
    each account-day scans roughly 1.8 GB.
    """
    sending_domain = (sending_domain or '').strip().lower()
    email_domain = (email_domain or '').strip().lower()
    if not sending_domain or not email_domain:
        raise AthenaBounceError('Both a sending domain and an email domain are required.')

    init_db()
    dates = resolve_dates(from_date, to_date)

    if len(dates) > LARGE_RANGE_DAYS and not confirm_large:
        raise AthenaBounceError(
            f'LARGE_RANGE: {len(dates)} days would scan roughly '
            f'{len(dates) * 1.8:.1f} GB. Confirm to proceed.'
        )

    # Check the cache BEFORE touching AWS: a fully cached answer should not need
    # credentials at all, or an expired SSO session would hide data we already have.
    cached = set() if force_refresh else _cached_dates(sending_domain, email_domain, dates)
    missing = [d for d in dates if d not in cached]

    account = _cached_account(sending_domain, email_domain, dates) if not missing else \
        resolve_account(sending_domain, dates[0])
    base = {
        'sending_domain': sending_domain,
        'email_domain': email_domain,
        'account': account,
        'dates': dates,
        'from_date': dates[0],
        'to_date': dates[-1],
    }

    if missing and not account:
        tried = ', '.join(account_candidates(sending_domain)) or '(none)'
        raise AthenaBounceError(
            f'No Athena data partition found for {sending_domain} on {dates[0]}. '
            f'Tried account values: {tried}. `account` is a required partition key, so '
            'the query cannot run without one — the account may not have sent that day, '
            'or its Athena account name differs from our mapping.'
        )

    scanned_bytes = runtime_ms = 0
    from_cache = not missing
    if missing:
        if not athena_enabled():
            raise AthenaBounceError('Athena queries are disabled (ATHENA_ENABLED=0).')
        sql = build_query(missing)
        # Pulsation shows the adapter's configured from-domain, while Athena records
        # the actual per-message from_address; they differ often enough that an exact
        # match silently returns nothing. Also accept siblings under the same
        # organisational domain and report which domains actually sent.
        org = org_domain(sending_domain)
        result = run_query(sql, [account, email_domain, sending_domain, f'%.{org}'])
        _store(sending_domain, email_domain, missing, result['rows'], result['scanned_bytes'], account)
        scanned_bytes, runtime_ms = result['scanned_bytes'], result['runtime_ms']

    rows = _load(sending_domain, email_domain, dates)
    total = sum(r['bounces'] for r in rows)
    for r in rows:
        r['share_pct'] = round(r['bounces'] / total * 100, 2) if total else 0.0

    note = ''
    if not rows:
        note = (
            f'No bounces from {sending_domain} (or other {org_domain(sending_domain)} '
            f'sending domains) to {email_domain} in this range. The account partition '
            f'"{account}" was scanned, so this is a real absence of bounces rather than a '
            'lookup failure. Note Pulsation shows the adapter\'s configured from-domain '
            'while Athena records the actual per-message from_address; if they differ, the '
            'bounces may be recorded under a sibling domain.'
        )

    return {
        'status': 'success',
        **base,
        'note': note,
        'rows': rows,
        'row_count': len(rows),
        'total_bounces': total,
        'hard_bounces': sum(r['bounces'] for r in rows if r['bounce_type'] == 'bounce'),
        'soft_bounces': sum(r['bounces'] for r in rows if r['bounce_type'] == 'soft_bounce'),
        'from_cache': from_cache,
        'scanned_bytes': scanned_bytes,
        'scanned_mb': round(scanned_bytes / 1048576, 1) if scanned_bytes else 0,
        'runtime_ms': runtime_ms,
        'source': 'athena' if missing else 'cache',
    }
