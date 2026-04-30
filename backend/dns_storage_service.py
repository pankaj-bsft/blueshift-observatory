"""
DNS Storage Service
Handles SQLite persistence for DNS Looker — domain history, custom selectors,
and daily check results.
"""

import sqlite3
import json
from datetime import datetime, date
from typing import Optional
from data_paths import data_path

DNS_DB_PATH = data_path('dns_looker.db')


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_dns_database():
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()

    # Main history table — one row per domain per check_date
    c.execute('''
        CREATE TABLE IF NOT EXISTS dns_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            domain          TEXT NOT NULL,
            account         TEXT,
            esp             TEXT,
            check_date      DATE NOT NULL,
            checked_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Per-record status: 'pass' | 'warn' | 'fail'
            spf_status      TEXT,
            spf_value       TEXT,
            spf_lookups     INTEGER,

            dkim_status     TEXT,
            dkim_selector   TEXT,
            dkim_value      TEXT,

            dmarc_status    TEXT,
            dmarc_policy    TEXT,
            dmarc_policy_level INTEGER,
            dmarc_value     TEXT,

            mx_status       TEXT,
            mx_value        TEXT,         -- JSON list

            bimi_status     TEXT,
            bimi_value      TEXT,

            mta_sts_status  TEXT,
            mta_sts_mode    TEXT,

            tls_rpt_status  TEXT,
            tls_rpt_value   TEXT,

            ptr_status      TEXT,
            ptr_value       TEXT,         -- JSON list

            spf_chain       TEXT,         -- JSON tree

            UNIQUE(domain, check_date)
        )
    ''')

    # Custom selectors — persisted when user manually provides a selector
    c.execute('''
        CREATE TABLE IF NOT EXISTS dns_custom_selectors (
            domain          TEXT PRIMARY KEY,
            selector        TEXT NOT NULL,
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ESP-level DKIM selectors (global, not per-domain)
    c.execute('''
        CREATE TABLE IF NOT EXISTS dns_esp_selectors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            esp         TEXT NOT NULL,
            selector    TEXT NOT NULL,
            added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(esp, selector)
        )
    ''')

    # DNS alert recipient preferences
    c.execute('''
        CREATE TABLE IF NOT EXISTS dns_alert_recipients (
            email       TEXT PRIMARY KEY,
            enabled     INTEGER DEFAULT 1,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Seed default ESP selectors if table is empty
    c.execute('SELECT COUNT(*) FROM dns_esp_selectors')
    if c.fetchone()[0] == 0:
        defaults = [
            ('SparkPost', 'scph0226'),
            ('Mailgun',   'krs'),
            ('Mailgun',   'pdk1'),
            ('Mailgun',   'pdk2'),
            ('SendGrid',  's1'),
            ('SendGrid',  's2'),
        ]
        c.executemany('INSERT OR IGNORE INTO dns_esp_selectors (esp, selector) VALUES (?,?)', defaults)
        print('DNS ESP selectors seeded with defaults.')

    # Indexes
    c.execute('CREATE INDEX IF NOT EXISTS idx_dns_domain_date ON dns_history(domain, check_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_dns_check_date  ON dns_history(check_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_dns_domain      ON dns_history(domain)')

    conn.commit()
    conn.close()
    print(f'DNS database initialized at {DNS_DB_PATH}')


# ─── Save / Upsert ────────────────────────────────────────────────────────────

def save_dns_result(result: dict, account: str = None, esp: str = None, check_date: date = None):
    """
    Persist a run_full_check() result dict to dns_history.
    Upserts on (domain, check_date).
    """
    if check_date is None:
        check_date = date.today()

    domain  = result['domain']
    spf     = result.get('spf', {})
    dkim    = result.get('dkim', {})
    dmarc   = result.get('dmarc', {})
    mx      = result.get('mx', {})
    bimi    = result.get('bimi', {})
    mta     = result.get('mta_sts', {})
    tls     = result.get('tls_rpt', {})
    ptr     = result.get('ptr', {})
    chain   = result.get('spf_chain', {})

    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()

    c.execute('''
        INSERT INTO dns_history (
            domain, account, esp, check_date, checked_at,
            spf_status, spf_value, spf_lookups,
            dkim_status, dkim_selector, dkim_value,
            dmarc_status, dmarc_policy, dmarc_policy_level, dmarc_value,
            mx_status, mx_value,
            bimi_status, bimi_value,
            mta_sts_status, mta_sts_mode,
            tls_rpt_status, tls_rpt_value,
            ptr_status, ptr_value,
            spf_chain
        ) VALUES (
            ?,?,?,?,?,
            ?,?,?,
            ?,?,?,
            ?,?,?,?,
            ?,?,
            ?,?,
            ?,?,
            ?,?,
            ?,?,
            ?
        )
        ON CONFLICT(domain, check_date) DO UPDATE SET
            account         = excluded.account,
            esp             = excluded.esp,
            checked_at      = excluded.checked_at,
            spf_status      = excluded.spf_status,
            spf_value       = excluded.spf_value,
            spf_lookups     = excluded.spf_lookups,
            dkim_status     = excluded.dkim_status,
            dkim_selector   = excluded.dkim_selector,
            dkim_value      = excluded.dkim_value,
            dmarc_status    = excluded.dmarc_status,
            dmarc_policy    = excluded.dmarc_policy,
            dmarc_policy_level = excluded.dmarc_policy_level,
            dmarc_value     = excluded.dmarc_value,
            mx_status       = excluded.mx_status,
            mx_value        = excluded.mx_value,
            bimi_status     = excluded.bimi_status,
            bimi_value      = excluded.bimi_value,
            mta_sts_status  = excluded.mta_sts_status,
            mta_sts_mode    = excluded.mta_sts_mode,
            tls_rpt_status  = excluded.tls_rpt_status,
            tls_rpt_value   = excluded.tls_rpt_value,
            ptr_status      = excluded.ptr_status,
            ptr_value       = excluded.ptr_value,
            spf_chain       = excluded.spf_chain
    ''', (
        domain, account, esp, check_date.isoformat(), datetime.utcnow().isoformat(),
        spf.get('status'), spf.get('value'), spf.get('lookup_count'),
        dkim.get('status'), dkim.get('selector'), dkim.get('value'),
        dmarc.get('status'), dmarc.get('policy'), dmarc.get('policy_level'), dmarc.get('value'),
        mx.get('status'), json.dumps(mx.get('value')),
        bimi.get('status'), bimi.get('value'),
        mta.get('status'), mta.get('mode'),
        tls.get('status'), tls.get('value'),
        ptr.get('status'), json.dumps(ptr.get('value')),
        json.dumps(chain),
    ))

    conn.commit()
    conn.close()


# ─── Fetch latest per domain ──────────────────────────────────────────────────

def get_latest_all_domains() -> list[dict]:
    """
    Return latest DNS check row for every domain in dns_history.
    Used to populate the Domain Monitor table.
    """
    conn = sqlite3.connect(DNS_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute('''
        SELECT h.*
        FROM dns_history h
        INNER JOIN (
            SELECT domain, MAX(check_date) AS max_date
            FROM dns_history
            GROUP BY domain
        ) latest ON h.domain = latest.domain AND h.check_date = latest.max_date
        ORDER BY h.domain
    ''')

    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    # Deserialize JSON columns
    for row in rows:
        row['mx_value']  = _safe_json(row.get('mx_value'))
        row['ptr_value'] = _safe_json(row.get('ptr_value'))
        row['spf_chain'] = _safe_json(row.get('spf_chain'))

    return rows


def get_domain_latest(domain: str) -> Optional[dict]:
    """Return the most recent check row for a single domain."""
    conn = sqlite3.connect(DNS_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute('''
        SELECT * FROM dns_history
        WHERE domain = ?
        ORDER BY check_date DESC
        LIMIT 1
    ''', (domain,))

    row = c.fetchone()
    conn.close()

    if not row:
        return None

    d = dict(row)
    d['mx_value']  = _safe_json(d.get('mx_value'))
    d['ptr_value'] = _safe_json(d.get('ptr_value'))
    d['spf_chain'] = _safe_json(d.get('spf_chain'))
    return d


# ─── History for charts ───────────────────────────────────────────────────────

def get_domain_history(domain: str, days: int = 90) -> list[dict]:
    """
    Return daily DNS check rows for a domain (last N days).
    Used for historical trend charts.
    """
    conn = sqlite3.connect(DNS_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute('''
        SELECT check_date,
               spf_status, dkim_status, dmarc_status,
               mx_status, bimi_status, mta_sts_status,
               tls_rpt_status, ptr_status,
               dmarc_policy, dmarc_policy_level,
               dkim_selector, spf_lookups
        FROM dns_history
        WHERE domain = ?
          AND check_date >= date('now', ? || ' days')
        ORDER BY check_date ASC
    ''', (domain, f'-{days}'))

    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ─── Custom selectors ─────────────────────────────────────────────────────────

def save_custom_selector(domain: str, selector: str):
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO dns_custom_selectors (domain, selector, added_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            selector   = excluded.selector,
            updated_at = excluded.updated_at
    ''', (domain, selector, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_custom_selector(domain: str) -> Optional[str]:
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT selector FROM dns_custom_selectors WHERE domain = ?', (domain,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def get_all_custom_selectors() -> dict:
    """Return {domain: selector} mapping for all saved selectors."""
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT domain, selector FROM dns_custom_selectors')
    rows = c.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ─── ESP Selectors (global) ──────────────────────────────────────────────────

def get_esp_selectors() -> dict:
    """Return {esp: [selector, ...]} grouped dict."""
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT esp, selector FROM dns_esp_selectors ORDER BY esp, id')
    rows = c.fetchall()
    conn.close()
    result = {}
    for esp, selector in rows:
        result.setdefault(esp, []).append(selector)
    return result


def add_esp_selector(esp: str, selector: str) -> bool:
    """Add a new selector for an ESP. Returns True if added, False if duplicate."""
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            'INSERT INTO dns_esp_selectors (esp, selector) VALUES (?, ?)',
            (esp.strip(), selector.strip())
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate
    finally:
        conn.close()


def delete_esp_selector(esp: str, selector: str):
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM dns_esp_selectors WHERE esp = ? AND selector = ?', (esp, selector))
    conn.commit()
    conn.close()


def get_all_esp_selectors_flat() -> list:
    """Return flat list of all selectors for use in DNS checks."""
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT selector FROM dns_esp_selectors')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_selectors_for_esp(esp: str) -> list:
    """Return list of selectors for a specific ESP."""
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT selector FROM dns_esp_selectors WHERE esp = ? ORDER BY id', (esp,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


# ─── Alert Recipients ─────────────────────────────────────────────────────────

def sync_alert_recipients(mbr_recipients: list):
    """
    Sync MBR recipients into dns_alert_recipients.
    New recipients added as enabled=1.
    Existing preferences preserved.
    """
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    for email in mbr_recipients:
        c.execute('''
            INSERT INTO dns_alert_recipients (email, enabled, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(email) DO NOTHING
        ''', (email, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_alert_recipients() -> list:
    """Return all recipients with their enabled status."""
    conn = sqlite3.connect(DNS_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT email, enabled FROM dns_alert_recipients ORDER BY email')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def set_alert_recipient_enabled(email: str, enabled: bool):
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO dns_alert_recipients (email, enabled, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            enabled = excluded.enabled,
            updated_at = excluded.updated_at
    ''', (email, 1 if enabled else 0, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_enabled_alert_recipients() -> list:
    """Return only enabled recipient emails — used when sending alerts."""
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT email FROM dns_alert_recipients WHERE enabled = 1')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_json(val):
    if val is None:
        return None
    try:
        return json.loads(val)
    except Exception:
        return val


# ─── Init on import ───────────────────────────────────────────────────────────

init_dns_database()
