from data_paths import data_path
"""
Google Postmaster Tools (GPT) Service
Handles OAuth 2.0 authentication and data collection from Google Postmaster API

MIGRATION NOTES (v1 -> v2, 2026-09):
Google decommissioned the v1 Postmaster Tools API for this project; v1 calls now
fail with a 429 whose body says "This version of the Postmaster Tools API is no
longer supported" (see _is_v1_deprecated_error). v2 replaced the old fixed-shape
trafficStats resource with a generic metric-query model (domainStats:query) and,
critically, DROPPED domain/IP reputation entirely -- there is no v2 field
equivalent to v1's domainReputation or ipReputations[]. Per product decision:
reputation-related columns (reputation, reputation_value, ip_reputation,
spf/dkim/dmarc_success_rate) are frozen -- left untouched/NULL for new rows,
never written by the v2 path -- rather than faked. New v2 rows are tagged
data_version='v2' so downstream code can tell "reputation available" history
from "reputation stopped here" going forward.

The old scope (postmaster.readonly) does not authorize v2 calls at all --
confirmed empirically (403 ACCESS_TOKEN_SCOPE_INSUFFICIENT). v2 also splits
what v1 covered with one scope into several: postmaster.traffic.readonly alone
still 403s on ListDomains -- postmaster.domain is additionally required. Both
are requested (re-authorize via /api/gpt/authorize if tokens predate this).

user_reported_spam_rate, auth_success_rate, and tls_rate are ALSO frozen/NULL
for v2 rows, not just the reputation columns above -- confirmed empirically
that FEEDBACK_LOOP_SPAM_RATE, AUTH_SUCCESS_RATE, and TLS_ENCRYPTION_RATE all
400 INVALID_ARGUMENT when queried with no `filter` (unlike SPAM_RATE and
DELIVERY_ERROR_RATE, which both work unfiltered). This strongly suggests a
required `filter` whose grammar Google's discovery doc doesn't document for
these three (unlike DELIVERY_ERROR_COUNT/RATE, where it's spelled out). Only
spam_rate and delivery_error_rate are populated for now -- see METRIC_MAP.

Not implemented (deferred, not silently dropped): Compliance/Deliverability
verdict (getComplianceStatus / DeliverabilityStatusVerdict) -- schema exists but
is marked [Developer Preview] by Google and no confirmed request shape was
available to test; the low_domain_reputation/low_ip_reputation delivery-error
filter values exist but the exact filter grammar wasn't verified against a live
call. All of the above are reasonable follow-ups once verified against Google's
full reference docs, not required for collection to work again.
"""
import os
import sqlite3
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# Database path
GPT_DB_PATH = data_path('gpt_data.db')

# OAuth 2.0 Configuration (loaded from environment)
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = 'https://developers.google.com/oauthplayground'
# v2 scopes -- postmaster.readonly (the old v1 scope) does NOT authorize v2 calls.
# Unlike v1's single scope, v2 splits this: traffic.readonly alone gets a 403
# ACCESS_TOKEN_SCOPE_INSUFFICIENT on ListDomains (confirmed empirically) --
# domain listing needs postmaster.domain too. No readonly-only variant of that
# scope exists, so this is as narrow as v2 allows for what collection needs.
SCOPES = [
    'https://www.googleapis.com/auth/postmaster.traffic.readonly',
    'https://www.googleapis.com/auth/postmaster.domain',
]
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_BASE_URL = 'https://gmailpostmastertools.googleapis.com/v2'

# v2 domainStats:query metric name -> Google's standardMetric enum value.
# Reputation has no v2 equivalent (see MIGRATION NOTES above) so it's absent here.
#
# FEEDBACK_LOOP_SPAM_RATE, AUTH_SUCCESS_RATE, and TLS_ENCRYPTION_RATE are
# deliberately NOT included -- confirmed empirically (live test call, 2026-09)
# that all three 400 INVALID_ARGUMENT when queried with no `filter`, while
# SPAM_RATE and DELIVERY_ERROR_RATE both succeed unfiltered. This strongly
# suggests those three require a `filter` value on MetricDefinition, but
# Google's discovery doc documents the filter grammar only for
# DELIVERY_ERROR_COUNT/RATE (reject/temp_fail reason values) -- nothing for
# these three. Rather than guess at unverified filter syntax and risk storing
# wrong data under a plausible-looking value, they're left out until the
# correct filter is confirmed against Google's full reference docs.
METRIC_MAP = {
    'spam_rate': 'SPAM_RATE',
    'delivery_error_rate': 'DELIVERY_ERROR_RATE',
}


def initialize_database():
    """Initialize GPT database with required tables"""
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    # Main data table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gpt_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            data_date DATE NOT NULL,

            -- Reputation Metrics
            reputation TEXT,
            reputation_value INTEGER,

            -- Spam Metrics
            spam_rate REAL DEFAULT 0,
            user_reported_spam_rate REAL DEFAULT 0,

            -- Authentication Metrics
            spf_success_rate REAL DEFAULT 0,
            dkim_success_rate REAL DEFAULT 0,
            dmarc_success_rate REAL DEFAULT 0,

            -- Encryption
            tls_rate REAL DEFAULT 0,

            -- Traffic
            message_volume INTEGER DEFAULT 0,

            -- Delivery Errors
            delivery_errors TEXT,

            -- IP Reputation (if available)
            ip_reputation TEXT,

            -- Metadata
            verified BOOLEAN DEFAULT 1,
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(domain, data_date)
        )
    ''')

    # OAuth tokens table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gpt_oauth_tokens (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            token_type TEXT,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Domain registry table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gpt_domains (
            domain TEXT PRIMARY KEY,
            verified BOOLEAN DEFAULT 1,
            last_collected TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # v2 compliance status snapshot (domains.getComplianceStatus) -- one
    # current-state row per domain, refreshed on each collection run, same
    # pattern as spamhaus_cache/account_info_snapshot elsewhere in this repo.
    # Not a daily time series like gpt_data -- compliance changes rarely.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gpt_compliance (
            domain TEXT PRIMARY KEY,
            root_domain TEXT,
            subdomain_deliverability_status TEXT,
            subdomain_deliverability_reason TEXT,
            root_deliverability_status TEXT,
            root_deliverability_reason TEXT,
            subdomain_requirements_json TEXT,
            root_requirements_json TEXT,
            subdomain_one_click_unsub_status TEXT,
            subdomain_honor_unsub_status TEXT,
            subdomain_honor_unsub_reason TEXT,
            raw_json TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gpt_domain_date ON gpt_data(domain, data_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gpt_date ON gpt_data(data_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gpt_reputation ON gpt_data(reputation)')

    # v1 -> v2 migration: add new columns to the existing table without touching
    # any existing row's data (see MIGRATION NOTES at the top of this file).
    # SQLite has no "ADD COLUMN IF NOT EXISTS", so check what's already there first.
    cursor.execute('PRAGMA table_info(gpt_data)')
    existing_cols = {row[1] for row in cursor.fetchall()}
    new_columns = [
        # Tags each row with the API version that produced it, so downstream
        # code/UI can tell "reputation available" history from "frozen" rows.
        ("data_version", "TEXT DEFAULT 'v1'"),
        ("auth_success_rate", "REAL"),
        ("delivery_error_rate", "REAL"),
    ]
    for col_name, col_def in new_columns:
        if col_name not in existing_cols:
            cursor.execute(f'ALTER TABLE gpt_data ADD COLUMN {col_name} {col_def}')

    conn.commit()
    conn.close()

    print(f'GPT database initialized at {GPT_DB_PATH}')


def get_authorization_url(state: str = 'gpt_auth') -> str:
    """
    Generate OAuth 2.0 authorization URL
    User needs to visit this URL to grant access
    """
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'state': state,
        'access_type': 'offline',
        'prompt': 'consent'
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(authorization_code: str) -> Dict:
    """
    Exchange authorization code for access and refresh tokens
    """
    data = {
        'code': authorization_code,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    }

    response = requests.post(TOKEN_URL, data=data)

    if response.status_code == 200:
        tokens = response.json()
        save_tokens(tokens)
        return tokens
    else:
        raise Exception(f'Failed to exchange code for tokens: {response.text}')


def save_tokens(tokens: Dict):
    """Save OAuth tokens to database"""
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    expires_at = datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600))

    cursor.execute('''
        INSERT OR REPLACE INTO gpt_oauth_tokens
        (id, access_token, refresh_token, token_type, expires_at, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
    ''', (
        tokens.get('access_token'),
        tokens.get('refresh_token'),
        tokens.get('token_type', 'Bearer'),
        expires_at,
        datetime.utcnow()
    ))

    conn.commit()
    conn.close()


def get_tokens() -> Optional[Dict]:
    """Get stored OAuth tokens"""
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT access_token, refresh_token, token_type, expires_at
        FROM gpt_oauth_tokens WHERE id = 1
    ''')

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        'access_token': row[0],
        'refresh_token': row[1],
        'token_type': row[2],
        'expires_at': datetime.fromisoformat(row[3]) if row[3] else None
    }


def refresh_access_token() -> Dict:
    """Refresh access token using refresh token"""
    tokens = get_tokens()

    if not tokens or not tokens.get('refresh_token'):
        raise Exception('No refresh token available. Please authorize first.')

    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': tokens['refresh_token'],
        'grant_type': 'refresh_token'
    }

    response = requests.post(TOKEN_URL, data=data)

    if response.status_code == 200:
        new_tokens = response.json()
        # Keep the refresh token from previous response
        new_tokens['refresh_token'] = tokens['refresh_token']
        save_tokens(new_tokens)
        return new_tokens
    else:
        raise Exception(f'Failed to refresh token: {response.text}')


def get_valid_access_token() -> str:
    """Get a valid access token (refresh if expired)"""
    tokens = get_tokens()

    if not tokens:
        raise Exception('No tokens available. Please authorize first.')

    # Check if token is expired or about to expire (within 5 minutes)
    if tokens['expires_at'] and tokens['expires_at'] <= datetime.utcnow() + timedelta(minutes=5):
        tokens = refresh_access_token()

    return tokens['access_token']


def _is_v1_deprecated_error(response: requests.Response) -> bool:
    """True if this is Google's "API version no longer supported" rejection.

    Google returns this as a 429/RESOURCE_EXHAUSTED (not a 401/403), which
    otherwise looks like a retryable rate limit. It never succeeds no matter
    how many times it's retried, so it must be distinguished from a real 429.
    """
    if response.status_code != 429:
        return False
    return 'no longer supported' in response.text.lower()


def _api_request(method: str, endpoint: str, params: Dict = None, json_body: Dict = None) -> Dict:
    """Make an authenticated request to the Postmaster API (GET or POST).

    Retries once on 401 (token refresh), and up to twice more with backoff on a
    genuine 429/5xx, but fails fast on the non-retryable v1-deprecation 429.
    """
    url = f"{API_BASE_URL}/{endpoint}"
    retried_auth = False
    backoff = 2

    for attempt in range(3):
        access_token = get_valid_access_token()
        headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
            response = requests.post(url, headers=headers, params=params, json=json_body, timeout=30)
        else:
            response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 200:
            return response.json()

        if response.status_code == 401 and not retried_auth:
            retried_auth = True
            refresh_access_token()
            continue

        if _is_v1_deprecated_error(response):
            raise Exception(
                f'Postmaster API rejected the request as an unsupported API version: {response.text}'
            )

        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 2:
                time.sleep(backoff)
                backoff *= 2
                continue

        raise Exception(f'API request failed: {response.status_code} - {response.text}')

    raise Exception('API request failed after retries')


def make_api_request(endpoint: str, params: Dict = None) -> Dict:
    """Make an authenticated GET request to the Postmaster API."""
    return _api_request('GET', endpoint, params=params)


def make_api_post_request(endpoint: str, json_body: Dict) -> Dict:
    """Make an authenticated POST request to the Postmaster API."""
    return _api_request('POST', endpoint, json_body=json_body)


def list_domains() -> List[str]:
    """List all domains registered in Google Postmaster Tools (paginated)."""
    domain_names = []
    page_token = None
    try:
        while True:
            params = {'pageSize': 100}
            if page_token:
                params['pageToken'] = page_token
            result = make_api_request('domains', params)

            for domain_info in result.get('domains', []):
                # Format: "domains/example.com"
                name = domain_info.get('name', '')
                if name.startswith('domains/'):
                    domain_names.append(name.replace('domains/', '', 1))

            page_token = result.get('nextPageToken')
            if not page_token:
                break

        return domain_names
    except Exception as e:
        print(f'Error listing domains: {e}')
        return domain_names


def _date_parts(date_str: str) -> Dict[str, int]:
    y, m, d = date_str.split('-')
    return {'year': int(y), 'month': int(m), 'day': int(d)}


def get_traffic_stats(domain: str, start_date: str, end_date: str) -> List[Dict]:
    """Query v2 domainStats for a domain over a date range (paginated).

    Returns the raw flat list of DomainStat dicts (one entry per date x
    metric) -- store_domain_data() pivots this into one row per date. Name
    kept as get_traffic_stats for backward compatibility with existing
    callers (collect_gpt_daily_offset.py, collect_and_store_gpt_data).
    """
    try:
        body = {
            'parent': f'domains/{domain}',
            'metricDefinitions': [
                {'name': key, 'baseMetric': {'standardMetric': std}}
                for key, std in METRIC_MAP.items()
            ],
            'timeQuery': {
                'dateRanges': {'dateRanges': [
                    {'start': _date_parts(start_date), 'end': _date_parts(end_date)}
                ]}
            },
            'aggregationGranularity': 'DAILY',
            'pageSize': 200,
        }

        endpoint = f'domains/{domain}/domainStats:query'
        all_stats = []
        page_token = None
        while True:
            if page_token:
                body['pageToken'] = page_token
            result = make_api_post_request(endpoint, body)
            all_stats.extend(result.get('domainStats', []))
            page_token = result.get('nextPageToken')
            if not page_token:
                break

        return all_stats
    except Exception as e:
        print(f'Error fetching traffic stats for {domain}: {e}')
        return []


def _stat_value(stat: Dict) -> Optional[float]:
    """Extract the numeric/string value out of a v2 StatisticValue."""
    value = stat.get('value') or {}
    for key in ('doubleValue', 'floatValue', 'intValue', 'stringValue'):
        if value.get(key) is not None:
            try:
                return float(value[key])
            except (TypeError, ValueError):
                return None
    return None


def _v2_date_to_str(date_obj: Optional[Dict]) -> Optional[str]:
    if not date_obj:
        return None
    y, m, d = date_obj.get('year'), date_obj.get('month'), date_obj.get('day')
    if not (y and m and d):
        return None
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def store_domain_data(domain: str, stats: List[Dict]) -> int:
    """Store v2 domain statistics in the database.

    `stats` is the flat v2 DomainStat list (one entry per date x metric) from
    get_traffic_stats(); this pivots it into one gpt_data row per date.

    Reputation-related columns (reputation, reputation_value, ip_reputation,
    spf/dkim/dmarc_success_rate) are intentionally left untouched/NULL for
    these rows -- v2 has no domain/IP reputation equivalent. user_reported_
    spam_rate, auth_success_rate, and tls_rate are also NULL for now (their
    v2 metrics 400 unfiltered -- not in METRIC_MAP, see MIGRATION NOTES at
    the top of this file for both). Rows are tagged data_version='v2'.

    Uses an explicit upsert (not INSERT OR REPLACE) so re-collecting a date
    that already has frozen v1 reputation data can never wipe it out.
    """
    by_date: Dict[str, Dict[str, Optional[float]]] = {}
    for stat in stats:
        data_date = _v2_date_to_str(stat.get('date'))
        if not data_date:
            continue
        metric_name = stat.get('metric')
        by_date.setdefault(data_date, {})[metric_name] = _stat_value(stat)

    if by_date:
        first_date = sorted(by_date)[0]
        print(f"\n=== DEBUG: v2 stats for {domain} on {first_date} ===")
        print(json.dumps(by_date[first_date], indent=2))
        print("=== END DEBUG ===\n")

    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()
    stored_count = 0

    for data_date, metrics in by_date.items():
        def pct(key):
            v = metrics.get(key)
            return v * 100 if v is not None else None

        spam_rate = pct('spam_rate')
        user_spam_rate = pct('feedback_loop_spam_rate')
        auth_success_rate = pct('auth_success_rate')
        tls_rate = pct('tls_encryption_rate')
        delivery_error_rate = pct('delivery_error_rate')

        # EC2's Python links against SQLite 3.7.17 (confirmed) -- ON CONFLICT
        # ... DO UPDATE needs 3.24+, so this is a manual check-then-branch
        # instead of a real upsert. The UPDATE branch deliberately never
        # touches spf/dkim/dmarc_success_rate or reputation columns, so
        # re-collecting a date that already has frozen v1 data can't wipe it.
        cursor.execute('SELECT 1 FROM gpt_data WHERE domain = ? AND data_date = ?', (domain, data_date))
        row_exists = cursor.fetchone() is not None

        if row_exists:
            cursor.execute('''
                UPDATE gpt_data
                SET spam_rate = ?, user_reported_spam_rate = ?, tls_rate = ?,
                    auth_success_rate = ?, delivery_error_rate = ?,
                    data_version = 'v2', collected_at = ?
                WHERE domain = ? AND data_date = ?
            ''', (
                spam_rate, user_spam_rate, tls_rate, auth_success_rate,
                delivery_error_rate, datetime.utcnow(), domain, data_date
            ))
        else:
            # spf/dkim/dmarc_success_rate default to 0 (not NULL) in the schema,
            # so they must be listed explicitly as NULL here -- omitting them
            # would silently apply that 0 default, which reads as "0% auth
            # success" (an alarming, wrong signal) instead of "no v2 data".
            cursor.execute('''
                INSERT INTO gpt_data
                    (domain, data_date, spam_rate, user_reported_spam_rate, tls_rate,
                     spf_success_rate, dkim_success_rate, dmarc_success_rate,
                     auth_success_rate, delivery_error_rate, message_volume,
                     verified, data_version, collected_at)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, 0, 1, 'v2', ?)
            ''', (
                domain, data_date, spam_rate, user_spam_rate, tls_rate,
                auth_success_rate, delivery_error_rate, datetime.utcnow()
            ))

        stored_count += 1

    conn.commit()
    conn.close()

    return stored_count


def get_compliance_status(domain: str) -> Optional[Dict]:
    """Fetch v2 compliance status for a domain (domains.getComplianceStatus).

    Confirmed live 2026-09 -- unlike the metrics deferred in METRIC_MAP, this
    endpoint works cleanly and only needs the postmaster.traffic.readonly
    scope already granted. Returns the raw API response, or None on error
    (e.g. domain not verified) -- callers should not treat None as "no
    compliance issues", just "couldn't fetch".
    """
    try:
        return make_api_request(f'domains/{domain}/complianceStatus')
    except Exception as e:
        print(f'Error fetching compliance status for {domain}: {e}')
        return None


def _verdict(block: Optional[Dict]) -> tuple:
    """Extract (status, reason) from a *Verdict-shaped block, or (None, None)."""
    if not block:
        return None, None
    state = (block.get('state') or block.get('status') or {})
    return state.get('status'), block.get('reason')


def store_compliance_status(domain: str, data: Optional[Dict]) -> bool:
    """Store a compliance status snapshot for one domain. Returns True if stored.

    `data` is get_compliance_status()'s raw response. Silently returns False
    for None (fetch already failed and logged) or a response with neither
    complianceData nor subdomainComplianceData (unexpected shape) -- never
    writes a half-populated row that could look like a real "all clear".
    """
    if not data:
        return False

    root = data.get('complianceData') or {}
    sub = data.get('subdomainComplianceData') or {}
    if not root and not sub:
        return False

    root_status, root_reason = _verdict(root.get('deliverabilityStatusVerdict'))
    sub_status, sub_reason = _verdict(sub.get('deliverabilityStatusVerdict'))
    sub_1click, _ = _verdict(sub.get('oneClickUnsubscribeVerdict'))
    sub_honor, sub_honor_reason = _verdict(sub.get('honorUnsubscribeVerdict'))

    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()
    # SQLite 3.7 on EC2 has no ON CONFLICT...DO UPDATE (see store_domain_data)
    # -- domain is PRIMARY KEY here, so INSERT OR REPLACE is safe (whole-row
    # replace is fine for a single-purpose snapshot table, unlike gpt_data
    # where it would wipe unrelated frozen columns).
    cursor.execute('''
        INSERT OR REPLACE INTO gpt_compliance
            (domain, root_domain, subdomain_deliverability_status, subdomain_deliverability_reason,
             root_deliverability_status, root_deliverability_reason,
             subdomain_requirements_json, root_requirements_json,
             subdomain_one_click_unsub_status, subdomain_honor_unsub_status, subdomain_honor_unsub_reason,
             raw_json, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        domain, root.get('domainId'), sub_status, sub_reason,
        root_status, root_reason,
        json.dumps(sub.get('rowData', [])), json.dumps(root.get('rowData', [])),
        sub_1click, sub_honor, sub_honor_reason,
        json.dumps(data), datetime.utcnow(),
    ))
    conn.commit()
    conn.close()
    return True


def cleanup_old_data(days: int = 365):
    """Delete data older than specified days"""
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    cursor.execute('DELETE FROM gpt_data WHERE data_date < ?', (cutoff_date,))
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted


def collect_and_store_gpt_data(days_back: int = 120) -> Dict:
    """
    Collect GPT data for all domains.

    days_back default (120) carries over from the v1 integration; v2's actual
    historical window wasn't confirmed in Google's docs during migration, so
    treat 120 as unverified rather than a documented guarantee.
    """
    initialize_database()

    print('\n=== Starting GPT Data Collection ===')

    # Get list of domains
    domains = list_domains()

    if not domains:
        return {
            'status': 'error',
            'message': 'No domains found. Please authorize first or check domain verification.',
            'domains_collected': 0
        }

    print(f'Found {len(domains)} domains')

    # Calculate date range (last 120 days)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')

    print(f'Collecting data from {start_date_str} to {end_date_str}')

    total_records = 0
    domains_collected = 0
    compliance_collected = 0

    for domain in domains:
        print(f'Fetching data for {domain}...')
        stats = get_traffic_stats(domain, start_date_str, end_date_str)

        if stats:
            stored = store_domain_data(domain, stats)
            total_records += stored
            domains_collected += 1
            print(f'  Stored {stored} records for {domain}')
        else:
            print(f'  No data available for {domain}')

        # Compliance status is a current-snapshot fetch (not date-ranged),
        # so it's one extra call per domain regardless of stats availability.
        if store_compliance_status(domain, get_compliance_status(domain)):
            compliance_collected += 1

    # Cleanup old data
    deleted = cleanup_old_data(365)
    print(f'Cleaned up {deleted} old records (older than 365 days)')

    print(f'\n✓ Collection completed')
    print(f'  Domains processed: {domains_collected}/{len(domains)}')
    print(f'  Total records stored: {total_records}')
    print(f'  Compliance snapshots stored: {compliance_collected}/{len(domains)}')

    return {
        'status': 'success',
        'total_domains': len(domains),
        'domains_collected': domains_collected,
        'total_records': total_records,
        'compliance_collected': compliance_collected,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'deleted_old_records': deleted
    }


# Initialize database on module import
initialize_database()
