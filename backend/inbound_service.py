"""
Inbound Email Service
Handles test address generation, Mailgun polling, and result storage.
"""
import sqlite3
import secrets
import requests
from datetime import datetime, timedelta
from data_paths import data_path
from config import MAILGUN_API_KEY, MAILGUN_US_BASE_URL

DNS_DB_PATH = data_path('dns_looker.db')
INBOUND_DOMAIN = 'robert-local.blueshiftimp.com'
TOKEN_EXPIRY_MINUTES = 30


def init_inbound_table():
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS inbound_test_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT UNIQUE NOT NULL,
            email       TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  TIMESTAMP NOT NULL,
            status      TEXT DEFAULT 'pending',
            storage_url TEXT,
            result_json TEXT
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_inbound_token ON inbound_test_sessions(token)')
    conn.commit()
    conn.close()


def generate_test_address():
    token = secrets.token_urlsafe(8).lower().replace('-', '').replace('_', '')[:8]
    email = "test-" + token + "@" + INBOUND_DOMAIN
    expires_at = (datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRY_MINUTES)).isoformat()
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT INTO inbound_test_sessions (token, email, expires_at, status) VALUES (?, ?, ?, ?)',
        (token, email, expires_at, 'pending')
    )
    conn.commit()
    conn.close()
    return {"token": token, "email": email, "expires_at": expires_at}


def poll_for_email(token):
    import json
    conn = sqlite3.connect(DNS_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM inbound_test_sessions WHERE token = ?', (token,))
    session = c.fetchone()
    conn.close()

    if not session:
        return {"status": "not_found", "message": "Token not found."}

    session = dict(session)

    if session['status'] == 'completed' and session['result_json']:
        return {"status": "completed", "result": json.loads(session['result_json'])}

    if datetime.utcnow().isoformat() > session['expires_at']:
        _update_session(token, 'expired')
        return {"status": "expired", "message": "Test address expired. Generate a new one."}

    target_email = session['email']
    resp = requests.get(
        MAILGUN_US_BASE_URL + '/' + INBOUND_DOMAIN + '/events',
        auth=('api', MAILGUN_API_KEY),
        params={'event': 'stored', 'limit': 25},
        timeout=10,
    )

    if resp.status_code != 200:
        return {"status": "pending", "message": "Waiting for email..."}

    items = resp.json().get('items', [])
    for item in items:
        recipients = item.get('message', {}).get('recipients', [])
        if target_email in recipients:
            storage_url = item.get('storage', {}).get('url', '')
            if storage_url:
                result = _fetch_and_analyze(storage_url, token)
                return {"status": "completed", "result": result}

    return {"status": "pending", "message": "Waiting for your email to arrive..."}


def _fetch_and_analyze(storage_url, token):
    import json
    from email_analyzer import analyze_headers

    resp = requests.get(
        storage_url,
        auth=('api', MAILGUN_API_KEY),
        headers={'Accept': 'message/rfc2822'},
        timeout=15,
    )

    if resp.status_code != 200:
        return {"error": "Failed to fetch email from Mailgun."}

    mime_content = resp.json().get('body-mime', '')
    if not mime_content:
        return {"error": "Empty email content."}

    result = analyze_headers(mime_content)
    result['storage_url'] = storage_url

    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute(
        'UPDATE inbound_test_sessions SET status = ?, storage_url = ?, result_json = ? WHERE token = ?',
        ('completed', storage_url, json.dumps(result, default=str), token)
    )
    conn.commit()
    conn.close()

    return result


def _update_session(token, status):
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE inbound_test_sessions SET status = ? WHERE token = ?', (status, token))
    conn.commit()
    conn.close()


init_inbound_table()
