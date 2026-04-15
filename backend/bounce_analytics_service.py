from data_paths import data_path
"""
Bounce Analytics Service
Collects and analyzes email bounce data from Mailgun, SparkPost, and SendGrid
Automatically discovers sending domains from each ESP
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parseaddr
from urllib.parse import urlparse, parse_qsl, urljoin

import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from config import (
    MAILGUN_API_KEY, MAILGUN_US_BASE_URL, MAILGUN_EU_BASE_URL,
    SPARKPOST_API_KEY, SPARKPOST_BASE_URL,
    SENDGRID_API_KEY, SENDGRID_BASE_URL
)

DB_PATH = data_path('bounce_analytics.db')
MAILGUN_MAX_WORKERS = 6

# ISP Domain Mapping
ISP_MAPPING = {
    'gmail.com': 'Gmail',
    'googlemail.com': 'Gmail',
    'yahoo.com': 'Yahoo',
    'yahoo.co.uk': 'Yahoo',
    'yahoo.co.jp': 'Yahoo',
    'yahoo.fr': 'Yahoo',
    'yahoo.de': 'Yahoo',
    'yahoo.ca': 'Yahoo',
    'yahoo.com.au': 'Yahoo',
    'ymail.com': 'Yahoo',
    'rocketmail.com': 'Yahoo',
    'hotmail.com': 'Microsoft',
    'hotmail.co.uk': 'Microsoft',
    'hotmail.fr': 'Microsoft',
    'hotmail.de': 'Microsoft',
    'live.com': 'Microsoft',
    'live.co.uk': 'Microsoft',
    'live.fr': 'Microsoft',
    'msn.com': 'Microsoft',
    'outlook.com': 'Microsoft',
    'outlook.fr': 'Microsoft',
    'outlook.de': 'Microsoft',
    'aol.com': 'AOL',
    'aim.com': 'AOL',
    'verizon.net': 'AOL',
    'icloud.com': 'Apple',
    'me.com': 'Apple',
    'mac.com': 'Apple',
    'protonmail.com': 'ProtonMail',
    'proton.me': 'ProtonMail',
    'pm.me': 'ProtonMail',
    'mail.ru': 'Mail.ru',
    'inbox.ru': 'Mail.ru',
    'list.ru': 'Mail.ru',
    'bk.ru': 'Mail.ru',
    'gmx.com': 'GMX',
    'gmx.de': 'GMX',
    'gmx.net': 'GMX',
    'zoho.com': 'Zoho',
    'zohomail.com': 'Zoho',
    'fastmail.com': 'Fastmail',
    'fastmail.fm': 'Fastmail',
    'comcast.net': 'Comcast',
    'att.net': 'AT&T',
    'sbcglobal.net': 'AT&T',
    'bellsouth.net': 'AT&T',
    'cox.net': 'Cox',
    'charter.net': 'Charter',
    'earthlink.net': 'EarthLink',
    'qq.com': 'QQ',
    '163.com': '163.com',
    '126.com': '126.com',
    'sina.com': 'Sina',
    'sohu.com': 'Sohu',
    'rediffmail.com': 'Rediff',
    'naver.com': 'Naver',
    'daum.net': 'Daum',
    'hanmail.net': 'Daum',
    'nate.com': 'Nate',
}


def map_domain_to_isp(domain: str) -> str:
    """Map recipient domain to ISP name"""
    domain = domain.lower().strip()
    return ISP_MAPPING.get(domain, 'Other')


def extract_domain_from_email(email: str) -> str:
    """Extract domain from email address"""
    if '@' in email:
        return email.split('@')[1].lower().strip()
    return email.lower().strip()


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_bounce_database():
    """Create Bounce Analytics tables/indexes if they do not exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bounce_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                esp TEXT NOT NULL,
                event_date DATE NOT NULL,
                sending_domain TEXT NOT NULL,
                sending_ip TEXT,
                recipient_domain TEXT NOT NULL,
                isp TEXT NOT NULL,
                bounce_type TEXT NOT NULL,
                bounce_reason TEXT,
                bounce_code TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_bounce_events_esp_date ON bounce_events (esp, event_date)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_bounce_events_esp_domain ON bounce_events (esp, sending_domain)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_bounce_events_event_date ON bounce_events (event_date)'
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        if 'readonly' not in str(exc).lower():
            conn.close()
            raise

        table_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bounce_events'"
        ).fetchone()
        if not table_exists:
            conn.close()
            raise
        print('[Bounce Analytics] Existing bounce database is read-only; skipping schema bootstrap.')
    finally:
        conn.close()


def delete_bounces_for_esp_date(esp: str, date: str) -> int:
    """Delete bounce rows for one ESP/date so a recollect can replace them cleanly."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bounce_events WHERE esp = ? AND event_date = ?', (esp, date))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def normalize_domain(value: Optional[str]) -> str:
    """Normalize a domain/email-like string down to a lowercase domain."""
    if not value:
        return ''

    parsed_name, parsed_addr = parseaddr(str(value))
    candidate = parsed_addr or str(value)
    candidate = candidate.strip().strip('<>').strip('"').lower()

    if '@' in candidate:
        candidate = candidate.split('@')[-1]

    return candidate.strip().strip('>;,"\'')


def get_mailgun_domains() -> List[str]:
    """
    Fetch all sending domains from Mailgun (both US and EU regions)
    Returns list of domain names
    """
    domains = []

    # Fetch from both US and EU regions
    for region, base_url in [('US', MAILGUN_US_BASE_URL), ('EU', MAILGUN_EU_BASE_URL)]:
        try:
            # Use pagination to get all domains (Mailgun API has 100 item default limit)
            skip = 0
            limit = 100
            region_domains = []

            while True:
                response = requests.get(
                    f'{base_url}/domains',
                    auth=('api', MAILGUN_API_KEY),
                    params={'limit': limit, 'skip': skip},
                    timeout=10
                )

                if response.status_code == 200:
                    data = response.json()
                    domain_items = data.get('items', [])

                    if not domain_items:
                        break  # No more domains

                    for domain_obj in domain_items:
                        domain_name = domain_obj.get('name')
                        if domain_name:
                            region_domains.append(domain_name)

                    # Check if there are more domains
                    total_count = data.get('total_count', 0)
                    if len(region_domains) >= total_count:
                        break

                    skip += limit
                else:
                    print(f'Mailgun {region} API error: {response.status_code}')
                    break

            domains.extend(region_domains)
            print(f'Found {len(region_domains)} Mailgun {region} domains')

        except Exception as e:
            print(f'Error fetching Mailgun {region} domains: {e}')

    return domains


def get_sparkpost_domains() -> List[str]:
    """
    Fetch all sending domains from SparkPost
    Returns list of domain names
    """
    domains = []

    try:
        response = requests.get(
            f'{SPARKPOST_BASE_URL}/sending-domains',
            headers={'Authorization': SPARKPOST_API_KEY},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            domain_items = data.get('results', [])
            for domain_obj in domain_items:
                domain_name = domain_obj.get('domain')
                if domain_name:
                    domains.append(domain_name)
            print(f'Found {len(domain_items)} SparkPost domains')
        else:
            print(f'SparkPost API error: {response.status_code}')

    except Exception as e:
        print(f'Error fetching SparkPost domains: {e}')

    return domains


def get_sendgrid_domains() -> List[str]:
    """
    Fetch all authenticated domains from SendGrid
    Returns list of domain names
    """
    domains = []

    try:
        response = requests.get(
            f'{SENDGRID_BASE_URL}/whitelabel/domains',
            headers={
                'Authorization': f'Bearer {SENDGRID_API_KEY}',
                'Content-Type': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            domain_items = response.json()
            for domain_obj in domain_items:
                domain_name = domain_obj.get('domain')
                if domain_name:
                    domains.append(domain_name)
            print(f'Found {len(domain_items)} SendGrid domains')
        else:
            print(f'SendGrid API error: {response.status_code}')

    except Exception as e:
        print(f'Error fetching SendGrid domains: {e}')

    return domains


def collect_mailgun_bounces(date: str = None) -> Dict:
    """
    Collect bounce events from Mailgun for a specific date
    Automatically discovers all sending domains from Mailgun API
    date format: YYYY-MM-DD (defaults to yesterday)
    """
    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # Automatically fetch all domains from Mailgun (US + EU)
    mailgun_domains = get_mailgun_domains()

    if not mailgun_domains:
        print('No Mailgun domains found')
        return {'esp': 'Mailgun', 'date': date, 'bounces_collected': 0}

    print(f'Collecting bounces for {len(mailgun_domains)} Mailgun domains')

    all_bounces = []

    # Convert date string to datetime objects and then to RFC2822 format
    date_obj = datetime.strptime(date, '%Y-%m-%d')
    begin_dt = date_obj.replace(hour=0, minute=0, second=0)
    end_dt = date_obj.replace(hour=23, minute=59, second=59)

    # Format as RFC2822 (required by Mailgun Events API)
    begin_time = begin_dt.strftime('%a, %d %b %Y %H:%M:%S +0000')
    end_time = end_dt.strftime('%a, %d %b %Y %H:%M:%S +0000')

    domains_with_bounces = 0
    max_workers = min(MAILGUN_MAX_WORKERS, max(len(mailgun_domains), 1))
    print(f'Mailgun collection using {max_workers} parallel workers')

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_domain = {
            executor.submit(
                fetch_mailgun_domain_bounces,
                domain,
                date,
                begin_time,
                end_time,
                idx,
                len(mailgun_domains)
            ): domain
            for idx, domain in enumerate(mailgun_domains, 1)
        }

        for future in as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                result = future.result()
                total_domain_bounces = result['count']
                if total_domain_bounces > 0:
                    domains_with_bounces += 1
                all_bounces.extend(result['bounces'])
            except Exception as e:
                print(f"Error collecting Mailgun bounces for {domain}: {str(e)}")

    print(f'\nMailgun collection summary:')
    print(f'  Total domains checked: {len(mailgun_domains)}')
    print(f'  Domains with bounces: {domains_with_bounces}')
    print(f'  Total bounces collected: {len(all_bounces)}')

    # Store in database
    if all_bounces:
        store_bounces(all_bounces)

    return {
        'esp': 'Mailgun',
        'date': date,
        'bounces_collected': len(all_bounces)
    }


def fetch_mailgun_domain_bounces(
    domain: str,
    date: str,
    begin_time: str,
    end_time: str,
    idx: int,
    total_domains: int
) -> Dict:
    """Fetch Mailgun bounce pages for one sending domain with progress logging."""
    url = f'https://api.mailgun.net/v3/{domain}/events'
    params = {
        'begin': begin_time,
        'end': end_time,
        'event': 'failed',
        'limit': 300
    }

    page_url = url
    page_params = params
    page_count = 0
    total_domain_bounces = 0
    domain_bounces = []
    seen_pages = set()

    print(f'[Mailgun] Domain {idx}/{total_domains} start: {domain}')

    while True:
        page_count += 1
        response = requests.get(
            page_url,
            auth=('api', MAILGUN_API_KEY),
            params=page_params,
            timeout=30
        )

        if response.status_code != 200:
            raise RuntimeError(f'Mailgun API error {response.status_code} on page {page_count}')

        data = response.json()
        items = data.get('items', [])
        print(f'[Mailgun] Domain {idx}/{total_domains} page {page_count}: {domain} -> {len(items)} items')

        if items:
            total_domain_bounces += len(items)

        for item in items:
            recipient = item.get('recipient', '')
            recipient_domain = extract_domain_from_email(recipient)

            delivery_status = item.get('delivery-status', {})
            error_code = delivery_status.get('code', '')
            error_message = delivery_status.get('message', '') or delivery_status.get('description', '')

            severity = item.get('severity', 'temporary')
            bounce_type = 'hard' if severity == 'permanent' else 'soft'
            sending_ip = item.get('ip', None)

            domain_bounces.append({
                'esp': 'Mailgun',
                'event_date': date,
                'sending_domain': domain,
                'sending_ip': sending_ip,
                'recipient_domain': recipient_domain,
                'isp': map_domain_to_isp(recipient_domain),
                'bounce_type': bounce_type,
                'bounce_reason': error_message,
                'bounce_code': str(error_code) if error_code else None
            })

        next_url = data.get('paging', {}).get('next')
        if not next_url:
            break
        if next_url in seen_pages:
            print(f'[Mailgun] Domain {idx}/{total_domains} duplicate page guard hit: {domain}')
            break

        seen_pages.add(next_url)
        page_url = next_url
        page_params = None

    print(
        f'[Mailgun] Domain {idx}/{total_domains} done: {domain} '
        f'pages={page_count} bounces={total_domain_bounces}'
    )
    return {
        'domain': domain,
        'count': total_domain_bounces,
        'bounces': domain_bounces
    }


def fetch_sparkpost_bounce_events(date: str) -> List[Dict]:
    """Fetch all SparkPost bounce-like events for a specific UTC date."""
    url = f'{SPARKPOST_BASE_URL}/events/message'
    headers = {
        'Authorization': SPARKPOST_API_KEY,
        'Accept': 'application/json'
    }
    params = {
        'events': 'bounce,out_of_band',
        'from': f'{date}T00:00:00Z',
        'to': f'{date}T23:59:59Z',
        'per_page': 10000,
        'cursor': 'initial'
    }

    all_events = []
    next_url = url
    next_params = params
    seen_requests = set()

    while next_url:
        request_key = (next_url, tuple(sorted((next_params or {}).items())))
        if request_key in seen_requests:
            break
        seen_requests.add(request_key)

        response = requests.get(next_url, headers=headers, params=next_params, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f'SparkPost API error: {response.status_code} {response.text[:300]}')

        data = response.json()
        results = data.get('results', [])
        all_events.extend(results)

        links = data.get('links') or {}
        next_link = links.get('next')
        if not next_link:
            break

        absolute_next_link = urljoin(SPARKPOST_BASE_URL + '/', next_link)
        parsed = urlparse(absolute_next_link)
        next_url = f'{parsed.scheme}://{parsed.netloc}{parsed.path}' if parsed.scheme and parsed.netloc else absolute_next_link
        next_params = dict(parse_qsl(parsed.query)) if parsed.query else None

    return all_events


def extract_sparkpost_sending_domain(event: Dict) -> str:
    """Extract a clean sending domain from a SparkPost event payload."""
    candidates = [
        event.get('mailfrom'),
        event.get('sending_domain'),
        event.get('from'),
        event.get('from_email'),
        event.get('friendly_from')
    ]

    for candidate in candidates:
        normalized = normalize_domain(candidate)
        if normalized:
            return normalized

    return 'unknown'


def extract_sparkpost_sending_ip(event: Dict) -> Optional[str]:
    """Extract SparkPost sending IP if present."""
    for key in ('sending_ip', 'ip_address', 'ip_pool', 'ip'):
        value = event.get(key)
        if value:
            return str(value)
    return None


def map_sparkpost_bounce_type(bounce_class: Optional[str], event: Dict) -> str:
    """Map SparkPost bounce classes/events to the shared bounce type model."""
    bounce_class = str(bounce_class or '').strip()
    event_type = str(event.get('type') or event.get('event') or '').lower()

    if bounce_class in {'50', '51', '52', '53', '54'}:
        return 'block'
    if bounce_class in {'10', '30', '40', '60'}:
        return 'hard'
    if bounce_class in {'20', '25', '70', '80', '90', '100'}:
        return 'soft'

    if event_type == 'out_of_band':
        return 'block'
    if event_type == 'bounce':
        return 'hard'

    return 'soft'


def normalize_sparkpost_bounce_event(event: Dict, date: str) -> Optional[Dict]:
    """Normalize a SparkPost event into the same schema used by Mailgun."""
    recipient_domain = normalize_domain(event.get('rcpt_to') or event.get('recipient'))
    sending_domain = extract_sparkpost_sending_domain(event)

    if not recipient_domain or not sending_domain:
        return None

    bounce_class = event.get('bounce_class')
    reason = event.get('reason') or event.get('raw_reason') or event.get('raw_rcpt_to') or ''

    return {
        'esp': 'Sparkpost',
        'event_date': date,
        'sending_domain': sending_domain,
        'sending_ip': extract_sparkpost_sending_ip(event),
        'recipient_domain': recipient_domain,
        'isp': map_domain_to_isp(recipient_domain),
        'bounce_type': map_sparkpost_bounce_type(bounce_class, event),
        'bounce_reason': reason,
        'bounce_code': str(bounce_class) if bounce_class not in (None, '') else None
    }


def collect_sparkpost_bounces(date: str = None) -> Dict:
    """
    Collect bounce events from SparkPost for a specific date
    Automatically collects bounces for all sending domains via message-events API
    """
    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f'Collecting SparkPost bounces for {date} (all domains)')

    all_bounces = []

    try:
        raw_events = fetch_sparkpost_bounce_events(date)
        for event in raw_events:
            normalized = normalize_sparkpost_bounce_event(event, date)
            if normalized:
                all_bounces.append(normalized)
    except Exception as e:
        print(f"Error collecting SparkPost bounces: {str(e)}")
        raise

    # Store in database
    delete_bounces_for_esp_date('Sparkpost', date)
    if all_bounces:
        store_bounces(all_bounces)

    return {
        'esp': 'Sparkpost',
        'date': date,
        'bounces_collected': len(all_bounces)
    }


def collect_sendgrid_bounces(date: str = None) -> Dict:
    """
    Collect bounce events from SendGrid for a specific date
    Automatically collects bounces for all sending domains via messages API
    """
    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f'Collecting SendGrid bounces for {date} (all domains)')

    url = 'https://api.sendgrid.com/v3/messages'

    headers = {
        'Authorization': f'Bearer {SENDGRID_API_KEY}',
        'Content-Type': 'application/json'
    }

    # Query for bounce events
    query = f'status="bounce" OR status="blocked" OR status="dropped"'

    params = {
        'query': query,
        'limit': 1000
    }

    all_bounces = []

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            messages = data.get('messages', [])

            for msg in messages:
                # Filter by date
                msg_date = msg.get('last_event_time', '')[:10]  # Get YYYY-MM-DD
                if msg_date != date:
                    continue

                # Extract details
                recipient = msg.get('to_email', '')
                recipient_domain = extract_domain_from_email(recipient)

                # Get sending domain
                from_email = msg.get('from_email', '')
                sending_domain = extract_domain_from_email(from_email)

                # Get sending IP
                sending_ip = msg.get('originating_ip', None)

                # Bounce details
                status = msg.get('status', '')
                reason = msg.get('reason', '') or msg.get('bounce_reason', '')

                # Determine bounce type
                if status == 'bounce':
                    bounce_type = 'hard'
                elif status == 'blocked':
                    bounce_type = 'block'
                elif status == 'dropped':
                    bounce_type = 'soft'
                else:
                    bounce_type = 'unknown'

                bounce = {
                    'esp': 'Sendgrid',
                    'event_date': date,
                    'sending_domain': sending_domain,
                    'sending_ip': sending_ip,
                    'recipient_domain': recipient_domain,
                    'isp': map_domain_to_isp(recipient_domain),
                    'bounce_type': bounce_type,
                    'bounce_reason': reason,
                    'bounce_code': None
                }

                all_bounces.append(bounce)

    except Exception as e:
        print(f"Error collecting SendGrid bounces: {str(e)}")

    # Store in database
    if all_bounces:
        store_bounces(all_bounces)

    return {
        'esp': 'Sendgrid',
        'date': date,
        'bounces_collected': len(all_bounces)
    }


def store_bounces(bounces: List[Dict]) -> int:
    """Store bounce events in database"""
    conn = get_db_connection()
    cursor = conn.cursor()

    inserted = 0
    for bounce in bounces:
        try:
            cursor.execute('''
                INSERT INTO bounce_events
                (esp, event_date, sending_domain, sending_ip, recipient_domain, isp, bounce_type, bounce_reason, bounce_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                bounce['esp'],
                bounce['event_date'],
                bounce['sending_domain'],
                bounce['sending_ip'],
                bounce['recipient_domain'],
                bounce['isp'],
                bounce['bounce_type'],
                bounce['bounce_reason'],
                bounce['bounce_code']
            ))
            inserted += 1
        except Exception as e:
            print(f"Error inserting bounce: {str(e)}")

    conn.commit()
    conn.close()

    return inserted


def get_bounces(esp: str, start_date: str, end_date: str, sending_domain: str = None) -> List[Dict]:
    """
    Get bounce events for a specific ESP and date range
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = '''
        SELECT
            event_date,
            sending_domain,
            sending_ip,
            recipient_domain,
            isp,
            bounce_type,
            bounce_reason,
            bounce_code,
            COUNT(*) as count
        FROM bounce_events
        WHERE esp = ? AND event_date >= ? AND event_date <= ?
    '''

    params = [esp, start_date, end_date]

    if sending_domain and sending_domain != 'all':
        query += ' AND sending_domain = ?'
        params.append(sending_domain)

    query += '''
        GROUP BY event_date, sending_domain, sending_ip, recipient_domain, isp, bounce_type, bounce_reason, bounce_code
        ORDER BY event_date DESC, count DESC
    '''

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    bounces = []
    for row in rows:
        bounces.append({
            'event_date': row['event_date'],
            'sending_domain': row['sending_domain'],
            'sending_ip': row['sending_ip'] or 'N/A',
            'recipient_domain': row['recipient_domain'],
            'isp': row['isp'],
            'bounce_type': row['bounce_type'],
            'bounce_reason': row['bounce_reason'] or 'N/A',
            'bounce_code': row['bounce_code'] or 'N/A',
            'count': row['count']
        })

    return bounces


def get_sending_domains(esp: str) -> List[str]:
    """Get list of unique sending domains for an ESP"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT DISTINCT sending_domain
        FROM bounce_events
        WHERE esp = ?
        ORDER BY sending_domain
    ''', (esp,))

    domains = [row['sending_domain'] for row in cursor.fetchall()]
    conn.close()

    return domains


def cleanup_old_data(days: int = 120) -> int:
    """Delete bounce events older than specified days"""
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM bounce_events WHERE event_date < ?', (cutoff_date,))
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted


def collect_all_esps(date: str = None) -> Dict:
    """Collect bounces from all ESPs for a specific date"""
    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    results = {
        'date': date,
        'esps': []
    }

    # Collect from each ESP
    mailgun_result = collect_mailgun_bounces(date)
    results['esps'].append(mailgun_result)

    sparkpost_result = collect_sparkpost_bounces(date)
    results['esps'].append(sparkpost_result)

    sendgrid_result = collect_sendgrid_bounces(date)
    results['esps'].append(sendgrid_result)

    # Cleanup old data
    deleted = cleanup_old_data(120)
    results['old_records_deleted'] = deleted

    return results
