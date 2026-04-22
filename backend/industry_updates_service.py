import sqlite3
import feedparser
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from data_paths import data_path

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    BeautifulSoup = None

logger = logging.getLogger(__name__)

DB_PATH = data_path('industry_updates.db')

# Trusted RSS feed sources
RSS_SOURCES = {
    'Gmail': {
        'name': 'Google Workspace Updates',
        'url': 'https://workspaceupdates.googleblog.com/feeds/posts/default',
        'type': 'Gmail',
        'preferred_terms': ['gmail', 'postmaster', 'bulk sender', 'sender requirements', 'one-click unsubscribe'],
        'required_any': ['gmail', 'postmaster', 'bulk sender', 'sender requirements'],
        'required_all_groups': [
            ['dmarc', 'dkim', 'spf', 'one-click unsubscribe', 'list-unsubscribe', 'complaint rate', 'bulk sender', 'sender requirements', 'postmaster', 'authentication']
        ]
    },
    'Microsoft_Security': {
        'name': 'Microsoft Security Blog',
        'url': 'https://www.microsoft.com/en-us/security/blog/feed/',
        'type': 'Outlook',
        'preferred_terms': ['outlook', 'exchange online', 'defender for office 365', 'sender requirements'],
        'required_any': ['outlook', 'exchange online', 'defender for office 365', 'hotmail'],
        'required_all_groups': [
            ['dmarc', 'dkim', 'spf', 'bulk sender', 'sender requirements', 'postmaster', 'authentication', 'spam complaint', 'complaint rate', 'unsubscribe']
        ]
    },
    'AWS_Security': {
        'name': 'AWS Security Blog',
        'url': 'https://aws.amazon.com/blogs/security/feed/',
        'type': 'Security',
        'preferred_terms': ['ses', 'email authentication', 'dmarc', 'dkim', 'spf'],
        'required_any': ['ses', 'simple email service', 'amazon ses'],
        'required_all_groups': [
            ['dmarc', 'dkim', 'spf', 'email authentication', 'deliverability', 'bounce', 'reputation']
        ]
    }
}

HTML_SOURCES = {
    'Mailgun_Deliverability': {
        'name': 'Mailgun Deliverability',
        'url': 'https://www.mailgun.com/blog/deliverability/',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'sender reputation', 'authentication', 'blocklist'],
        'required_any': ['deliverability', 'sender reputation', 'email authentication', 'blocklist', 'gmail', 'spam'],
        'required_all_groups': [
            ['deliverability', 'dmarc', 'dkim', 'spf', 'reputation', 'blocklist', 'bounce', 'gmail', 'postmaster']
        ],
        'allowed_hosts': ['www.mailgun.com', 'mailgun.com'],
        'allowed_path_terms': ['/blog/deliverability/'],
        'listing_limit': 15,
        'article_limit': 10
    },
    'SparkPost_Deliverability': {
        'name': 'SparkPost Deliverability',
        'url': 'https://support.sparkpost.com/docs/deliverability',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'ip warm-up', 'gmail', 'bounce classification'],
        'required_any': ['deliverability', 'gmail', 'bounce', 'warm-up', 'reputation', 'dedicated ip'],
        'required_all_groups': [
            ['deliverability', 'bounce', 'reputation', 'ip warm', 'gmail', 'authentication']
        ],
        'allowed_hosts': ['support.sparkpost.com'],
        'allowed_path_terms': ['/docs/deliverability'],
        'listing_limit': 20,
        'article_limit': 12
    },
    'SendGrid_Deliverability': {
        'name': 'Twilio SendGrid Deliverability',
        'url': 'https://www.twilio.com/docs/sendgrid/ui/sending-email/deliverability',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'sender authentication', 'sender reputation', 'spam folder'],
        'required_any': ['deliverability', 'sendgrid', 'sender authentication', 'sender reputation', 'spam folder', 'unsubscribe'],
        'required_all_groups': [
            ['deliverability', 'authentication', 'reputation', 'spam folder', 'gmail', 'dmarc', 'dkim', 'spf']
        ],
        'allowed_hosts': ['www.twilio.com', 'twilio.com'],
        'allowed_path_terms': ['/docs/sendgrid/ui/sending-email/deliverability'],
        'listing_limit': 20,
        'article_limit': 12
    },
    'CustomerIO_Deliverability': {
        'name': 'Customer.io Deliverability',
        'url': 'https://customer.io/learn/deliverability',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'spam complaints', 'sender reputation', 'authentication'],
        'required_any': ['deliverability', 'sender reputation', 'spam complaint', 'authentication', 'bounce'],
        'required_all_groups': [
            ['deliverability', 'dmarc', 'dkim', 'spf', 'reputation', 'bounce', 'complaint rate', 'unsubscribe']
        ],
        'allowed_hosts': ['customer.io'],
        'allowed_path_terms': ['/learn/deliverability', '/docs/journeys/'],
        'listing_limit': 20,
        'article_limit': 12
    },
    'Postmark_Deliverability': {
        'name': 'Postmark Deliverability',
        'url': 'https://postmarkapp.com/glossary/email-deliverability',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'reputation', 'dedicated ip', 'bounce'],
        'required_any': ['deliverability', 'reputation', 'bounce', 'dedicated ip', 'gmail'],
        'required_all_groups': [
            ['deliverability', 'reputation', 'bounce', 'dedicated ip', 'dmarc', 'dkim', 'spf']
        ],
        'allowed_hosts': ['postmarkapp.com'],
        'allowed_path_terms': ['/glossary/', '/support/article/'],
        'listing_limit': 20,
        'article_limit': 12
    },
    'Mailchimp_Deliverability': {
        'name': 'Mailchimp Deliverability',
        'url': 'https://mailchimp.com/resources/deliverability-101/',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'sender reputation', 'authentication', 'spam complaints'],
        'required_any': ['deliverability', 'sender reputation', 'authentication', 'spam complaint', 'bounce'],
        'required_all_groups': [
            ['deliverability', 'dmarc', 'dkim', 'spf', 'reputation', 'unsubscribe', 'spam complaint']
        ],
        'allowed_hosts': ['mailchimp.com'],
        'allowed_path_terms': ['/resources/'],
        'listing_limit': 20,
        'article_limit': 12
    },
    'Mailtrap_Blog': {
        'name': 'Mailtrap Blog',
        'url': 'https://mailtrap.io/blog/',
        'type': 'ESP',
        'preferred_terms': ['deliverability', 'smtp', 'dmarc', 'dkim', 'spf'],
        'required_any': ['deliverability', 'smtp', 'sender reputation', 'dmarc', 'dkim', 'spf', 'bounce'],
        'required_all_groups': [
            ['deliverability', 'dmarc', 'dkim', 'spf', 'reputation', 'bounce', 'gmail', 'unsubscribe']
        ],
        'allowed_hosts': ['mailtrap.io'],
        'allowed_path_terms': ['/blog/'],
        'listing_limit': 20,
        'article_limit': 12
    }
}

DELIVERABILITY_STRONG_TERMS = {
    'deliverability': 8,
    'inbox placement': 8,
    'bulk sender': 9,
    'sender requirements': 9,
    'one-click unsubscribe': 9,
    'list-unsubscribe': 8,
    'postmaster': 7,
    'spam complaint': 8,
    'complaint rate': 8,
    'blocklist': 8,
    'blacklist': 8,
    'sender reputation': 8,
    'domain reputation': 8,
    'ip reputation': 8,
    'feedback loop': 7,
    'bounce': 7,
    'deferred': 7,
    'rejected': 7,
    'spam folder': 7,
    'mailbox provider': 7,
    'gmail': 5,
    'outlook': 5,
    'yahoo': 5,
    'exchange online': 6,
    'google postmaster': 8,
    'spamhaus': 8,
    'ses': 5,
    'authentication': 4,
    'email authentication': 7,
    'dmarc': 8,
    'dkim': 8,
    'spf': 8,
    'bimi': 8,
    'arc': 6
}

DELIVERABILITY_SUPPORT_TERMS = {
    'email sender': 4,
    'sending domain': 4,
    'outbound mail': 4,
    'email policy': 4,
    'message hygiene': 4,
    'email compliance': 4,
    'unsubscribe': 5,
    'suppression': 4,
    'recipient requirements': 4,
    'email best practices': 4
}

STRICT_DELIVERABILITY_TERMS = {
    'deliverability',
    'bulk sender',
    'sender requirements',
    'one-click unsubscribe',
    'list-unsubscribe',
    'postmaster',
    'spam complaint',
    'complaint rate',
    'blocklist',
    'blacklist',
    'sender reputation',
    'domain reputation',
    'ip reputation',
    'feedback loop',
    'bounce',
    'deferred',
    'rejected',
    'spam folder',
    'mailbox provider',
    'google postmaster',
    'email authentication',
    'dmarc',
    'dkim',
    'spf',
    'bimi',
    'arc',
    'unsubscribe',
    'suppression'
}

LINK_DISCOVERY_TERMS = {
    'deliverability', 'sender', 'reputation', 'authentication', 'dmarc', 'dkim', 'spf',
    'gmail', 'outlook', 'unsubscribe', 'feedback loop', 'bounce', 'spam', 'postmaster',
    'blocklist', 'warm-up', 'dedicated ip'
}

NEGATIVE_CONTEXT_TERMS = {
    'ransomware': -8,
    'zero-day': -8,
    'endpoint': -6,
    'container security': -7,
    'kubernetes': -7,
    'ddos': -6,
    'supply chain': -6,
    'vpn': -6,
    'firewall': -6,
    'xdr': -6,
    'edr': -6,
    'iam': -5,
    'cloud workload': -6,
    'patch tuesday': -6,
    'malware': -5,
    'threat actor': -5,
    'cyber resilience': -5
}


def clean_text(value: str) -> str:
    """Normalize RSS text for relevance scoring and display."""
    if not value:
        return ''
    value = re.sub(r'<[^>]+>', ' ', value)
    value = unescape(value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def get_meta_content(soup: BeautifulSoup, attr: str, value: str) -> str:
    tag = soup.find('meta', attrs={attr: value})
    if tag and tag.get('content'):
        return clean_text(tag['content'])
    return ''


class DeliverabilityLinkParser(HTMLParser):
    """Fallback HTML parser for link discovery when BeautifulSoup is unavailable."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._current_href = None
        self._current_title = ''
        self._text_chunks = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        attrs_dict = dict(attrs)
        self._current_href = attrs_dict.get('href')
        self._current_title = attrs_dict.get('title', '')
        self._text_chunks = []

    def handle_data(self, data):
        if self._current_href:
            self._text_chunks.append(data)

    def handle_endtag(self, tag):
        if tag != 'a' or not self._current_href:
            return
        self.links.append({
            'href': self._current_href,
            'title': self._current_title,
            'text': ''.join(self._text_chunks)
        })
        self._current_href = None
        self._current_title = ''
        self._text_chunks = []


def parse_published_date(raw_value: str) -> str:
    """Convert common published date formats to YYYY-MM-DD."""
    if not raw_value:
        return datetime.now().strftime('%Y-%m-%d')

    raw_value = raw_value.strip()
    patterns = [
        '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S.%f%z',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%b %d, %Y',
        '%B %d, %Y'
    ]

    for pattern in patterns:
        try:
            return datetime.strptime(raw_value, pattern).strftime('%Y-%m-%d')
        except ValueError:
            continue

    matched = re.search(r'(\d{4}-\d{2}-\d{2})', raw_value)
    if matched:
        return matched.group(1)

    return datetime.now().strftime('%Y-%m-%d')

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
    text = clean_text(title + ' ' + description).lower()

    critical_keywords = [
        'sender requirements',
        'bulk sender',
        'one-click unsubscribe',
        'blocked',
        'rejected',
        'blocklist',
        'blacklist',
        'outage'
    ]
    high_keywords = [
        'dmarc',
        'dkim',
        'spf',
        'authentication',
        'complaint rate',
        'feedback loop',
        'unsubscribe',
        'postmaster'
    ]
    medium_keywords = ['deliverability', 'email policy', 'sender reputation', 'inbox placement', 'bounce']

    if any(keyword in text for keyword in critical_keywords):
        return 'critical'
    elif any(keyword in text for keyword in high_keywords):
        return 'high'
    elif any(keyword in text for keyword in medium_keywords):
        return 'medium'
    else:
        return 'info'

def score_article_relevance(title: str, description: str, source_config: Dict) -> Dict:
    """Score whether an article is truly about email deliverability."""
    text = clean_text(title + ' ' + description).lower()

    score = 0
    strong_matches = []
    support_matches = []
    negative_matches = []
    preferred_matches = []

    for term, weight in DELIVERABILITY_STRONG_TERMS.items():
        if term in text:
            score += weight
            strong_matches.append(term)

    for term, weight in DELIVERABILITY_SUPPORT_TERMS.items():
        if term in text:
            score += weight
            support_matches.append(term)

    for term in source_config.get('preferred_terms', []):
        term_lower = term.lower()
        if term_lower in text:
            score += 4
            preferred_matches.append(term_lower)

    for term, weight in NEGATIVE_CONTEXT_TERMS.items():
        if term in text:
            score += weight
            negative_matches.append(term)

    strict_matches = [term for term in STRICT_DELIVERABILITY_TERMS if term in text]
    required_any = source_config.get('required_any', [])
    required_any_matched = [term for term in required_any if term.lower() in text]
    required_groups = source_config.get('required_all_groups', [])
    groups_satisfied = []
    for group in required_groups:
        matched_group_terms = [term for term in group if term.lower() in text]
        groups_satisfied.append(bool(matched_group_terms))

    matched_terms = strong_matches + preferred_matches + support_matches
    has_deliverability_signal = bool(strict_matches)
    meets_source_requirements = bool(required_any_matched) and all(groups_satisfied or [True])
    is_relevant = has_deliverability_signal and meets_source_requirements and score >= 10

    if negative_matches and score < 12:
        is_relevant = False

    return {
        'is_relevant': is_relevant,
        'score': score,
        'matched_terms': matched_terms[:5],
        'negative_terms': negative_matches[:5],
        'strict_terms': strict_matches[:5],
        'required_any_matched': required_any_matched[:5]
    }


def discover_html_links(source_config: Dict, html: str) -> List[Dict]:
    """Discover candidate article URLs from a deliverability-focused HTML listing page."""
    candidates = []
    seen_urls = set()

    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, 'html.parser')
        raw_links = [
            {
                'href': anchor.get('href', '').strip(),
                'title': anchor.get('title', ''),
                'text': anchor.get_text(' ', strip=True)
            }
            for anchor in soup.find_all('a', href=True)
        ]
    else:
        parser = DeliverabilityLinkParser()
        parser.feed(html)
        raw_links = parser.links

    for raw_link in raw_links:
        href = (raw_link.get('href') or '').strip()
        if not href or href.startswith('#'):
            continue

        full_url = urljoin(source_config['url'], href)
        parsed = urlparse(full_url)
        if parsed.scheme not in ('http', 'https'):
            continue
        if parsed.netloc not in source_config.get('allowed_hosts', []):
            continue

        link_text = clean_text(raw_link.get('text', ''))
        title_attr = clean_text(raw_link.get('title', ''))
        combined = f'{link_text} {title_attr} {full_url}'.lower()

        if not any(term in combined for term in LINK_DISCOVERY_TERMS):
            continue

        allowed_paths = source_config.get('allowed_path_terms', [])
        if allowed_paths and not any(term in parsed.path for term in allowed_paths):
            continue

        normalized_url = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)

        candidates.append({
            'url': normalized_url,
            'link_text': link_text or title_attr
        })

        if len(candidates) >= source_config.get('listing_limit', 15):
            break

    return candidates


def fetch_html_article(candidate: Dict, source_config: Dict) -> Optional[Dict]:
    """Fetch and normalize one article discovered from a deliverability HTML source."""
    response = requests.get(candidate['url'], timeout=15, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    response.raise_for_status()

    if BeautifulSoup is not None:
        soup = BeautifulSoup(response.text, 'html.parser')
        title = (
            get_meta_content(soup, 'property', 'og:title')
            or get_meta_content(soup, 'name', 'twitter:title')
            or (clean_text(soup.title.get_text()) if soup.title else '')
        )
        if not title:
            h1 = soup.find('h1')
            title = clean_text(h1.get_text(' ', strip=True)) if h1 else clean_text(candidate.get('link_text', ''))

        description = (
            get_meta_content(soup, 'property', 'og:description')
            or get_meta_content(soup, 'name', 'description')
        )
        if not description:
            first_paragraph = soup.find('p')
            description = clean_text(first_paragraph.get_text(' ', strip=True)) if first_paragraph else ''

        published_raw = (
            get_meta_content(soup, 'property', 'article:published_time')
            or get_meta_content(soup, 'name', 'article:published_time')
            or get_meta_content(soup, 'name', 'date')
        )
        if not published_raw:
            time_tag = soup.find('time')
            if time_tag:
                published_raw = time_tag.get('datetime') or time_tag.get_text(' ', strip=True)
    else:
        html = response.text
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, flags=re.IGNORECASE | re.DOTALL)
        og_desc_match = re.search(
            r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=["\'](.*?)["\']',
            html,
            flags=re.IGNORECASE | re.DOTALL
        )
        time_match = re.search(
            r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date)["\'][^>]+content=["\'](.*?)["\']',
            html,
            flags=re.IGNORECASE | re.DOTALL
        )
        p_match = re.search(r'<p[^>]*>(.*?)</p>', html, flags=re.IGNORECASE | re.DOTALL)

        title = clean_text(title_match.group(1)) if title_match else clean_text(candidate.get('link_text', ''))
        description = clean_text(og_desc_match.group(1)) if og_desc_match else clean_text(p_match.group(1)) if p_match else ''
        published_raw = clean_text(time_match.group(1)) if time_match else ''

    published_date = parse_published_date(published_raw)
    pub_datetime = datetime.strptime(published_date, '%Y-%m-%d')
    if datetime.now() - pub_datetime > timedelta(days=30):
        return None

    relevance = score_article_relevance(title, description, source_config)
    if not relevance['is_relevant']:
        return None

    return {
        'title': title[:500],
        'description': description[:1000],
        'source': source_config['name'],
        'source_type': source_config['type'],
        'url': candidate['url'],
        'published_date': published_date,
        'severity': calculate_severity(title, description),
        'tags': ','.join(relevance['matched_terms']),
        'is_outage': 0
    }


def parse_html_source(source_key: str, source_config: Dict) -> List[Dict]:
    """Crawl a curated HTML listing page for deliverability-focused articles."""
    updates = []
    try:
        logger.info(f"Crawling deliverability source {source_config['name']}")
        response = requests.get(source_config['url'], timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        response.raise_for_status()

        candidates = discover_html_links(source_config, response.text)
        for candidate in candidates[:source_config.get('article_limit', 10)]:
            try:
                article = fetch_html_article(candidate, source_config)
                if article:
                    updates.append(article)
            except Exception as article_error:
                logger.error(f"Error crawling article {candidate['url']}: {article_error}")

        logger.info(f"Found {len(updates)} relevant articles from {source_config['name']}")
    except Exception as e:
        logger.error(f"Error crawling source {source_config['name']}: {str(e)}")

    return updates

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
            description = clean_text(entry.get('summary', entry.get('description', '')))
            link = entry.get('link', '')

            # Check if relevant
            relevance = score_article_relevance(title, description, source_config)
            if not relevance['is_relevant']:
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
                'tags': ','.join(relevance['matched_terms']),
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

    # Crawl curated deliverability HTML sources
    for source_key, source_config in HTML_SOURCES.items():
        updates = parse_html_source(source_key, source_config)
        all_updates.extend(updates)

    # Fetch from Downdetector
    outage_updates = fetch_downdetector_status()
    all_updates.extend(outage_updates)

    # Store in database
    inserted = store_updates(all_updates)
    pruned = prune_irrelevant_updates()

    return {
        'total_fetched': len(all_updates),
        'total_inserted': inserted,
        'sources_checked': len(RSS_SOURCES) + len(HTML_SOURCES),
        'total_pruned': pruned
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


def prune_irrelevant_updates() -> int:
    """Remove stored rows that do not meet the current deliverability relevance rules."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    source_config_by_name = {
        **{config['name']: config for config in RSS_SOURCES.values()},
        **{config['name']: config for config in HTML_SOURCES.values()}
    }
    rows = cursor.execute(
        'SELECT id, title, description, source FROM industry_updates'
    ).fetchall()

    ids_to_delete = []
    for row in rows:
        source_config = source_config_by_name.get(row['source'])
        if not source_config:
            ids_to_delete.append(row['id'])
            continue

        relevance = score_article_relevance(row['title'], row['description'] or '', source_config)
        if not relevance['is_relevant']:
            ids_to_delete.append(row['id'])

    deleted = 0
    if ids_to_delete:
        placeholders = ','.join('?' for _ in ids_to_delete)
        cursor.execute(f'DELETE FROM industry_updates WHERE id IN ({placeholders})', ids_to_delete)
        deleted = cursor.rowcount

    conn.commit()
    conn.close()
    return deleted
