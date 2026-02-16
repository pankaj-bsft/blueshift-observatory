import sqlite3
import feedparser
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

DB_PATH = '/Users/pankaj/pani/data/industry_updates.db'

# Trusted RSS feed sources
RSS_SOURCES = {
    'Gmail': {
        'name': 'Google Workspace Updates',
        'url': 'https://workspaceupdates.googleblog.com/feeds/posts/default',
        'type': 'Gmail',
        'keywords': ['gmail', 'email', 'deliverability', 'sender', 'authentication', 'spam', 'dmarc', 'spf', 'dkim']
    },
    'Barracuda': {
        'name': 'Barracuda Blog',
        'url': 'https://blog.barracuda.com/feed',
        'type': 'Security',
        'keywords': ['email', 'security', 'spam', 'phishing', 'deliverability', 'threat']
    },
    'Microsoft_Security': {
        'name': 'Microsoft Security Blog',
        'url': 'https://www.microsoft.com/en-us/security/blog/feed/',
        'type': 'Outlook',
        'keywords': ['email', 'outlook', 'exchange', 'security', 'phishing', 'spam', 'authentication']
    },
    'AWS_Security': {
        'name': 'AWS Security Blog',
        'url': 'https://aws.amazon.com/blogs/security/feed/',
        'type': 'Security',
        'keywords': ['email', 'ses', 'authentication', 'security', 'phishing', 'spam']
    },
    'Cloudflare': {
        'name': 'Cloudflare Blog',
        'url': 'https://blog.cloudflare.com/rss/',
        'type': 'Security',
        'keywords': ['email', 'security', 'ddos', 'phishing', 'spam', 'dns', 'dmarc']
    },
    'Cisco_Talos': {
        'name': 'Cisco Talos Intelligence',
        'url': 'https://blog.talosintelligence.com/feeds/posts/default',
        'type': 'Security',
        'keywords': ['email', 'threat', 'spam', 'phishing', 'malware', 'security']
    },
    'SANS_ISC': {
        'name': 'SANS Internet Storm Center',
        'url': 'https://isc.sans.edu/rssfeed.xml',
        'type': 'Security',
        'keywords': ['email', 'phishing', 'spam', 'malware', 'threat', 'security']
    },
    'Krebs_Security': {
        'name': 'Krebs on Security',
        'url': 'https://krebsonsecurity.com/feed/',
        'type': 'Security',
        'keywords': ['email', 'phishing', 'cybercrime', 'fraud', 'spam', 'breach']
    }
}

def init_database():
    """Initialize the industry updates database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS industry_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            published_date TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            tags TEXT,
            is_outage INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(url)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_published_date
        ON industry_updates(published_date DESC)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_source_type
        ON industry_updates(source_type)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_severity
        ON industry_updates(severity)
    ''')

    conn.commit()
    conn.close()

    logger.info(f'Industry updates database initialized at {DB_PATH}')

def calculate_severity(title: str, description: str) -> str:
    """Calculate severity based on keywords in title and description"""
    text = (title + ' ' + description).lower()

    critical_keywords = ['outage', 'down', 'breach', 'critical', 'urgent', 'blocked', 'blacklist', 'emergency']
    high_keywords = ['update required', 'compliance', 'deadline', 'change', 'requirement', 'mandatory']
    medium_keywords = ['update', 'new feature', 'enhancement', 'improve']

    if any(keyword in text for keyword in critical_keywords):
        return 'critical'
    elif any(keyword in text for keyword in high_keywords):
        return 'high'
    elif any(keyword in text for keyword in medium_keywords):
        return 'medium'
    else:
        return 'info'

def is_relevant(title: str, description: str, keywords: List[str]) -> bool:
    """Check if article is relevant based on keywords"""
    text = (title + ' ' + description).lower()
    return any(keyword.lower() in text for keyword in keywords)

def parse_rss_feed(source_key: str, source_config: Dict) -> List[Dict]:
    """Parse RSS feed and extract relevant articles"""
    updates = []

    try:
        logger.info(f"Fetching RSS feed from {source_config['name']}")
        # Fetch RSS feed with requests library (more reliable than feedparser alone)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(source_config['url'], headers=headers, timeout=10)
        response.raise_for_status()

        # Parse the fetched content
        feed = feedparser.parse(response.content)

        for entry in feed.entries[:20]:  # Limit to 20 most recent
            title = entry.get('title', '')
            description = entry.get('summary', entry.get('description', ''))
            link = entry.get('link', '')

            # Check if relevant
            if not is_relevant(title, description, source_config['keywords']):
                continue

            # Parse published date
            published = entry.get('published_parsed') or entry.get('updated_parsed')
            if published:
                published_date = datetime(*published[:6]).strftime('%Y-%m-%d')
            else:
                published_date = datetime.now().strftime('%Y-%m-%d')

            # Only include articles from last 30 days
            pub_datetime = datetime.strptime(published_date, '%Y-%m-%d')
            if datetime.now() - pub_datetime > timedelta(days=30):
                continue

            severity = calculate_severity(title, description)

            updates.append({
                'title': title[:500],
                'description': description[:1000],
                'source': source_config['name'],
                'source_type': source_config['type'],
                'url': link,
                'published_date': published_date,
                'severity': severity,
                'tags': ','.join(source_config['keywords'][:3]),
                'is_outage': 0
            })

        logger.info(f"Found {len(updates)} relevant articles from {source_config['name']}")

    except Exception as e:
        logger.error(f"Error parsing RSS feed {source_config['name']}: {str(e)}")

    return updates

def fetch_downdetector_status() -> List[Dict]:
    """Fetch outage information from Downdetector"""
    updates = []

    # Downdetector doesn't have a public API, so we'll skip this for now
    # In production, you could scrape or use a paid API

    return updates

def store_updates(updates: List[Dict]) -> int:
    """Store updates in database, avoiding duplicates"""
    if not updates:
        return 0

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    inserted = 0
    for update in updates:
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO industry_updates
                (title, description, source, source_type, url, published_date, severity, tags, is_outage, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                update['title'],
                update['description'],
                update['source'],
                update['source_type'],
                update['url'],
                update['published_date'],
                update['severity'],
                update['tags'],
                update['is_outage'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))

            if cursor.rowcount > 0:
                inserted += 1

        except sqlite3.IntegrityError:
            # Duplicate URL, skip
            continue

    conn.commit()
    conn.close()

    return inserted

def refresh_all_updates() -> Dict:
    """Fetch and store updates from all sources"""
    all_updates = []

    # Fetch from RSS sources
    for source_key, source_config in RSS_SOURCES.items():
        updates = parse_rss_feed(source_key, source_config)
        all_updates.extend(updates)

    # Fetch from Downdetector
    outage_updates = fetch_downdetector_status()
    all_updates.extend(outage_updates)

    # Store in database
    inserted = store_updates(all_updates)

    return {
        'total_fetched': len(all_updates),
        'total_inserted': inserted,
        'sources_checked': len(RSS_SOURCES)
    }

def get_updates(
    limit: int = 50,
    source_type: Optional[str] = None,
    severity: Optional[str] = None,
    days: int = 30
) -> List[Dict]:
    """Get industry updates from database with filters"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = '''
        SELECT * FROM industry_updates
        WHERE published_date >= date('now', '-' || ? || ' days')
    '''
    params = [days]

    if source_type:
        query += ' AND source_type = ?'
        params.append(source_type)

    if severity:
        query += ' AND severity = ?'
        params.append(severity)

    query += ' ORDER BY published_date DESC, created_at DESC LIMIT ?'
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_sources() -> List[str]:
    """Get list of unique source types"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT DISTINCT source_type FROM industry_updates ORDER BY source_type')
    sources = [row[0] for row in cursor.fetchall()]

    conn.close()
    return sources

def cleanup_old_updates(days: int = 90):
    """Remove updates older than specified days"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        DELETE FROM industry_updates
        WHERE published_date < date('now', '-' || ? || ' days')
    ''', (days,))

    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    logger.info(f'Cleaned up {deleted} old industry updates')
    return deleted
