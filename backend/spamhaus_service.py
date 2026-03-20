import sqlite3
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from account_mapping_service import get_all_mappings

DB_PATH = '/Users/pankaj/pani/data/deliverability_history.db'
SPAMHAUS_ZONE = 'wb5dvc223dyd64f5tsbrtxqytm.dbl.dq.spamhaus.net'
RETENTION_DAYS = 365
LOOKUP_TIMEOUT = 2


def init_spamhaus_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS spamhaus_cache (
            domain TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS spamhaus_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            status TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            source TEXT,
            UNIQUE(domain, checked_at)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_spamhaus_history_domain ON spamhaus_history(domain)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_spamhaus_history_date ON spamhaus_history(checked_at)")
    conn.commit()
    conn.close()


def _normalize_domain(domain: str) -> str:
    if not domain:
        return ''
    return domain.strip().lower()


def _lookup_spamhaus(domain: str, timeout: int = LOOKUP_TIMEOUT) -> str:
    fqdn = f"{domain}.{SPAMHAUS_ZONE}"
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.gethostbyname(fqdn)
        return 'listed'
    except socket.gaierror:
        return 'clean'
    except socket.timeout:
        return 'unknown'
    except Exception:
        return 'unknown'
    finally:
        socket.setdefaulttimeout(prev_timeout)


def _today_str() -> str:
    return datetime.utcnow().date().strftime('%Y-%m-%d')


def _expires_str() -> str:
    return (datetime.utcnow().date() + timedelta(days=1)).strftime('%Y-%m-%d')


def get_recent_domains(days: int = 30) -> List[str]:
    cutoff = (datetime.utcnow().date() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT DISTINCT from_domain FROM daily_metrics WHERE report_date >= ?',
        (cutoff,)
    )
    domains = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()
    return sorted({_normalize_domain(d) for d in domains if _normalize_domain(d)})


def refresh_spamhaus_cache(domains: List[str]) -> Dict[str, str]:
    init_spamhaus_tables()
    today = _today_str()
    expires_at = _expires_str()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    updated = {}
    for raw_domain in domains:
        domain = _normalize_domain(raw_domain)
        if not domain:
            continue

        cursor.execute('SELECT checked_at, status FROM spamhaus_cache WHERE domain = ?', (domain,))
        row = cursor.fetchone()
        if row and row[0] == today:
            updated[domain] = row[1]
            continue

        status = _lookup_spamhaus(domain)
        cursor.execute('''
            INSERT INTO spamhaus_cache (domain, status, checked_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                status = excluded.status,
                checked_at = excluded.checked_at,
                expires_at = excluded.expires_at
        ''', (domain, status, today, expires_at))

        cursor.execute('''
            INSERT OR REPLACE INTO spamhaus_history (domain, status, checked_at, source)
            VALUES (?, ?, ?, ?)
        ''', (domain, status, today, 'spamhaus_dbl'))

        updated[domain] = status

    conn.commit()
    conn.close()
    cleanup_spamhaus_history()
    return updated


def cleanup_spamhaus_history():
    cutoff = (datetime.utcnow().date() - timedelta(days=RETENTION_DAYS)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM spamhaus_history WHERE checked_at < ?', (cutoff,))
    conn.commit()
    conn.close()


def get_spamhaus_status_map(domains: List[str]) -> Dict[str, str]:
    if not domains:
        return {}
    init_spamhaus_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    status_map = {}
    for raw_domain in domains:
        domain = _normalize_domain(raw_domain)
        if not domain:
            continue
        cursor.execute('SELECT status FROM spamhaus_cache WHERE domain = ?', (domain,))
        row = cursor.fetchone()
        status_map[domain] = row[0] if row else 'unknown'
    conn.close()
    return status_map


def ensure_daily_refresh(domains: List[str]) -> Dict[str, str]:
    if not domains:
        return {}
    today = _today_str()
    init_spamhaus_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    needs_refresh = []
    for raw_domain in domains:
        domain = _normalize_domain(raw_domain)
        if not domain:
            continue
        cursor.execute('SELECT checked_at FROM spamhaus_cache WHERE domain = ?', (domain,))
        row = cursor.fetchone()
        if not row or row[0] != today:
            needs_refresh.append(domain)
    conn.close()

    if needs_refresh:
        refresh_spamhaus_cache(needs_refresh)

    return get_spamhaus_status_map(domains)


def get_domains_needing_refresh(domains: List[str]) -> List[str]:
    if not domains:
        return []
    today = _today_str()
    init_spamhaus_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    needs_refresh = []
    for raw_domain in domains:
        domain = _normalize_domain(raw_domain)
        if not domain:
            continue
        cursor.execute('SELECT checked_at FROM spamhaus_cache WHERE domain = ?', (domain,))
        row = cursor.fetchone()
        if not row or row[0] != today:
            needs_refresh.append(domain)
    conn.close()
    return needs_refresh


def get_spamhaus_history(domain: str, days: int = 30) -> Dict[str, List]:
    domain = _normalize_domain(domain)
    if not domain:
        return {'dates': [], 'status': []}
    cutoff = (datetime.utcnow().date() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT checked_at, status
        FROM spamhaus_history
        WHERE domain = ? AND checked_at >= ?
        ORDER BY checked_at ASC
    ''', (domain, cutoff))
    rows = cursor.fetchall()
    conn.close()
    dates = [r[0] for r in rows]
    status = [1 if r[1] == 'listed' else 0 for r in rows]
    return {'dates': dates, 'status': status}


def get_spamhaus_account_trend(account_name: str, days: int = 30) -> Dict[str, List]:
    cutoff_date = datetime.utcnow().date() - timedelta(days=days)
    start = cutoff_date.strftime('%Y-%m-%d')
    end = datetime.utcnow().date().strftime('%Y-%m-%d')

    mappings = get_all_mappings(limit=100000).get('mappings', [])
    target = account_name.strip().lower()
    domains = [
        _normalize_domain(m.get('sending_domain', ''))
        for m in mappings
        if (m.get('account_name') or '').strip().lower() == target
    ]
    domains = [d for d in domains if d]

    if not domains:
        return {'dates': [], 'listed_count': []}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT checked_at, domain, status
        FROM spamhaus_history
        WHERE domain IN ({})
          AND checked_at >= ?
        ORDER BY checked_at ASC
    '''.format(','.join(['?'] * len(domains))), (*domains, start))
    rows = cursor.fetchall()
    conn.close()

    by_date = {}
    for checked_at, domain, status in rows:
        by_date.setdefault(checked_at, {})
        by_date[checked_at][domain] = status

    dates = []
    listed_counts = []
    day = cutoff_date
    while day <= datetime.utcnow().date():
        day_str = day.strftime('%Y-%m-%d')
        dates.append(day_str)
        listed = 0
        for d in domains:
            if by_date.get(day_str, {}).get(d) == 'listed':
                listed += 1
        listed_counts.append(listed)
        day += timedelta(days=1)

    return {'dates': dates, 'listed_count': listed_counts}


def get_spamhaus_listing_summary(domains: List[str]) -> List[Dict]:
    domains = [_normalize_domain(d) for d in domains if _normalize_domain(d)]
    if not domains:
        return []
    init_spamhaus_tables()
    status_map = get_spamhaus_status_map(domains)
    listed_domains = [d for d in domains if status_map.get(d) == 'listed']
    if not listed_domains:
        return []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    summary = []
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).strftime('%Y-%m-%d')
    today = _today_str()

    for domain in listed_domains:
        cursor.execute('''
            SELECT checked_at, status
            FROM spamhaus_history
            WHERE domain = ?
            ORDER BY checked_at ASC
        ''', (domain,))
        rows = cursor.fetchall()
        if not rows:
            continue

        first_listed = None
        history_map = {}
        for checked_at, status in rows:
            history_map[checked_at] = status
            if status == 'listed' and not first_listed:
                first_listed = checked_at

        consecutive = 0
        day = datetime.utcnow().date()
        while True:
            day_str = day.strftime('%Y-%m-%d')
            if history_map.get(day_str) == 'listed':
                consecutive += 1
                day -= timedelta(days=1)
                continue
            break

        summary.append({
            'domain': domain,
            'first_listed_date': first_listed,
            'consecutive_days_listed': consecutive,
            'yesterday_status': history_map.get(yesterday, 'unknown'),
            'today_status': history_map.get(today, status_map.get(domain, 'unknown'))
        })

    conn.close()
    return summary
