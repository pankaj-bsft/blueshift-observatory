"""
Google Postmaster Tools (GPT) Service
Handles OAuth 2.0 authentication and data collection from Google Postmaster API
"""
import os
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# Database path
GPT_DB_PATH = '/Users/pankaj/pani/data/gpt_data.db'

# OAuth 2.0 Configuration (loaded from environment)
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = 'https://developers.google.com/oauthplayground'
SCOPES = ['https://www.googleapis.com/auth/postmaster.readonly']
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_BASE_URL = 'https://gmailpostmastertools.googleapis.com/v1'


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

    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gpt_domain_date ON gpt_data(domain, data_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gpt_date ON gpt_data(data_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gpt_reputation ON gpt_data(reputation)')

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


def make_api_request(endpoint: str, params: Dict = None) -> Dict:
    """Make authenticated API request to Google Postmaster"""
    access_token = get_valid_access_token()

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    url = f"{API_BASE_URL}/{endpoint}"
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        return response.json()
    elif response.status_code == 401:
        # Token might be invalid, try refreshing
        refresh_access_token()
        access_token = get_valid_access_token()
        headers['Authorization'] = f'Bearer {access_token}'
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            return response.json()

    raise Exception(f'API request failed: {response.status_code} - {response.text}')


def list_domains() -> List[str]:
    """List all domains registered in Google Postmaster Tools"""
    try:
        result = make_api_request('domains')
        domains = result.get('domains', [])

        # Extract domain names from full resource names
        domain_names = []
        for domain_info in domains:
            # Format: "domains/example.com"
            name = domain_info.get('name', '')
            if name.startswith('domains/'):
                domain_name = name.replace('domains/', '')
                domain_names.append(domain_name)

        return domain_names
    except Exception as e:
        print(f'Error listing domains: {e}')
        return []


def get_traffic_stats(domain: str, start_date: str, end_date: str) -> List[Dict]:
    """Get traffic statistics for a domain"""
    try:
        endpoint = f'domains/{domain}/trafficStats'
        params = {
            'startDate.year': start_date.split('-')[0],
            'startDate.month': start_date.split('-')[1],
            'startDate.day': start_date.split('-')[2],
            'endDate.year': end_date.split('-')[0],
            'endDate.month': end_date.split('-')[1],
            'endDate.day': end_date.split('-')[2],
        }

        result = make_api_request(endpoint, params)
        return result.get('trafficStats', [])
    except Exception as e:
        print(f'Error fetching traffic stats for {domain}: {e}')
        return []


def reputation_to_value(reputation: str) -> int:
    """Convert reputation string to numeric value"""
    reputation_map = {
        'HIGH': 4,
        'MEDIUM': 3,
        'LOW': 2,
        'BAD': 1,
        'UNKNOWN': 0
    }
    return reputation_map.get(reputation, 0)


def store_domain_data(domain: str, stats: List[Dict]):
    """Store domain statistics in database"""
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    stored_count = 0

    for stat in stats:
        # Debug: Print first stat to see structure
        if stored_count == 0:
            print(f"\n=== DEBUG: First stat for {domain} ===")
            print(json.dumps(stat, indent=2))
            print("=== END DEBUG ===\n")

        # Extract date
        date_info = stat.get('name', '').split('/')[-1]  # Format: domains/example.com/trafficStats/20260101
        if len(date_info) == 8:
            data_date = f"{date_info[0:4]}-{date_info[4:6]}-{date_info[6:8]}"
        else:
            continue

        # Extract reputation
        reputation = stat.get('domainReputation', 'UNKNOWN')
        reputation_value = reputation_to_value(reputation)

        # Extract spam rates (API returns as decimal, convert to percentage)
        # Note: spamRate doesn't exist in API, only userReportedSpamRatio
        spam_rate = 0  # Not provided by API
        user_spam_rate = stat.get('userReportedSpamRatio', 0) * 100 if stat.get('userReportedSpamRatio') is not None else 0

        # Extract authentication rates (API returns as ratio 0-1, convert to percentage)
        # IMPORTANT: Field names are "Ratio" not "Rate"
        spf_rate = stat.get('spfSuccessRatio', 0) * 100 if stat.get('spfSuccessRatio') is not None else 0
        dkim_rate = stat.get('dkimSuccessRatio', 0) * 100 if stat.get('dkimSuccessRatio') is not None else 0
        dmarc_rate = stat.get('dmarcSuccessRatio', 0) * 100 if stat.get('dmarcSuccessRatio') is not None else 0

        # TLS rate (API field is "Ratio" not "Rate")
        tls_rate = stat.get('inboundEncryptionRatio', 0) * 100 if stat.get('inboundEncryptionRatio') is not None else 0

        # IP reputation breakdown (store as JSON with counts per reputation)
        ip_reputations_raw = stat.get('ipReputations', [])
        ip_reputation_breakdown = {}
        sample_ips = {}

        if ip_reputations_raw:
            for ip_rep in ip_reputations_raw:
                reputation = ip_rep.get('reputation')
                count_str = ip_rep.get('ipCount', '0')
                ips = ip_rep.get('sampleIps', [])

                try:
                    count = int(count_str) if count_str else 0
                except (ValueError, TypeError):
                    count = 0

                # Only include reputations that have IPs
                if count > 0 and reputation:
                    ip_reputation_breakdown[reputation] = count
                    if ips:
                        sample_ips[reputation] = ips

        # Store as JSON string
        ip_reputation = json.dumps({
            'breakdown': ip_reputation_breakdown,
            'samples': sample_ips
        }) if ip_reputation_breakdown else None

        # Delivery errors (store as JSON)
        delivery_errors = json.dumps(stat.get('deliveryErrors', []))

        # Message volume (not always available)
        message_volume = 0  # Google doesn't provide exact volume in API

        cursor.execute('''
            INSERT OR REPLACE INTO gpt_data
            (domain, data_date, reputation, reputation_value, spam_rate, user_reported_spam_rate,
             spf_success_rate, dkim_success_rate, dmarc_success_rate, tls_rate,
             message_volume, delivery_errors, ip_reputation, verified, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ''', (
            domain, data_date, reputation, reputation_value, spam_rate, user_spam_rate,
            spf_rate, dkim_rate, dmarc_rate, tls_rate,
            message_volume, delivery_errors, ip_reputation, datetime.utcnow()
        ))

        stored_count += 1

    conn.commit()
    conn.close()

    return stored_count


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
    Collect GPT data for all domains
    Google provides up to 120 days of data
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

    # Cleanup old data
    deleted = cleanup_old_data(365)
    print(f'Cleaned up {deleted} old records (older than 365 days)')

    print(f'\n✓ Collection completed')
    print(f'  Domains processed: {domains_collected}/{len(domains)}')
    print(f'  Total records stored: {total_records}')

    return {
        'status': 'success',
        'total_domains': len(domains),
        'domains_collected': domains_collected,
        'total_records': total_records,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'deleted_old_records': deleted
    }


# Initialize database on module import
initialize_database()
