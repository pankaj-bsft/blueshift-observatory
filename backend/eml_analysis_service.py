"""
EML Deliverability Analysis Service
"""
import os
import re
import socket
import sqlite3
import json
import unicodedata
import quopri
from urllib.parse import urlparse, parse_qs, unquote
import html as html_module
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from spamhaus_service import _lookup_spamhaus, SPAMHAUS_ZONE

DB_PATH = '/Users/pankaj/pani/data/deliverability_analysis.db'
UPLOAD_DIR = '/Users/pankaj/pani/data/eml_uploads'
ATTACH_DIR = '/Users/pankaj/pani/data/eml_attachments'
RETENTION_DAYS = 90

SPAM_KEYWORDS = [
    'free', 'urgent', 'act now', 'limited time', 'winner', 'prize', 'click here',
    'guarantee', 'risk-free', 'credit', 'cheap', 'buy now', 'exclusive',
    'congratulations', 'offer expires', 'verify', 'password', 'account locked'
]

HIDDEN_CHAR_PATTERN = re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060\uFEFF\u00ad\u00a0\u2000-\u200a\u202f\u205f\u3000]')
URL_PATTERN = re.compile(r'(https?://[^\s"\'<>]+)', re.IGNORECASE)
MAX_BLOCKLIST_DOMAINS = 20
SPAMHAUS_IP_LOOKUP_ENABLED = os.getenv("BS_ENABLE_SPAMHAUS_IP", "true").lower() == "true"
SPAMHAUS_IP_ZONE = SPAMHAUS_ZONE.replace('dbl', 'zen', 1) if 'dbl' in SPAMHAUS_ZONE else SPAMHAUS_ZONE


def _clean_domain(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    val = value.strip().lower()
    if '@' in val:
        val = val.split('@')[-1]
    if '//' in val:
        val = urlparse(val).netloc or ''
    elif '/' in val:
        val = val.split('/')[0]
    val = val.split(':')[0]
    val = val.strip('[]()<>. ')
    if val.startswith('www.'):
        val = val[4:]
    if '.' not in val:
        return None
    return val


def _reverse_ip(ip: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    parts = ip.strip().split('.')
    if len(parts) != 4:
        return None
    if not all(p.isdigit() for p in parts):
        return None
    return '.'.join(reversed(parts))


def _dnsbl_query(hostname: Optional[str]) -> Dict:
    if not hostname:
        return {'listed': False, 'error': 'no-host'}
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2)
    try:
        ip = socket.gethostbyname(hostname)
        return {'listed': True, 'response': ip}
    except socket.gaierror:
        return {'listed': False}
    except Exception as e:
        return {'listed': False, 'error': str(e)}
    finally:
        socket.setdefaulttimeout(prev_timeout)




def _lookup_spamhaus_ip(rev_ip: str) -> str:
    fqdn = f"{rev_ip}.{SPAMHAUS_IP_ZONE}"
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2)
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
def _check_ip_blocklists(ip: Optional[str]) -> Dict:
    rev = _reverse_ip(ip)
    if not rev:
        return {}
    spamhaus_result = None
    if SPAMHAUS_IP_LOOKUP_ENABLED:
        status = _lookup_spamhaus_ip(rev)
        if status != 'unknown':
            spamhaus_result = {'listed': status == 'listed'}
    return {
        'spamhaus': spamhaus_result,
        'barracuda': _dnsbl_query(f'{rev}.b.barracudacentral.org'),
        'sorbs': _dnsbl_query(f'{rev}.dnsbl.sorbs.net'),
    }


def _check_domain_blocklists(domain: Optional[str]) -> Dict:
    d = _clean_domain(domain)
    if not d:
        return {}
    status = _lookup_spamhaus(d)
    spamhaus_result = {'listed': status == 'listed'}
    if status == 'unknown':
        spamhaus_result = None
    return {
        'spamhaus': spamhaus_result,
        'barracuda': _dnsbl_query(f'{d}.b.barracudacentral.org'),
        'sorbs': _dnsbl_query(f'{d}.dnsbl.sorbs.net'),
    }


def _blocklist_checks(sending_ip: Optional[str], sending_domain: Optional[str], link_domains: List[str], redirect_domains: List[str]) -> Dict:
    result = {
        'sending_ip': {'value': sending_ip, 'results': _check_ip_blocklists(sending_ip)},
        'sending_domain': {'value': sending_domain, 'results': _check_domain_blocklists(sending_domain)},
        'link_domains': [],
        'redirect_domains': [],
        'any_listed': False
    }

    seen = set()
    for domain in link_domains:
        d = _clean_domain(domain)
        if not d or d in seen:
            continue
        seen.add(d)
        result['link_domains'].append({'domain': d, 'results': _check_domain_blocklists(d)})
        if len(result['link_domains']) >= MAX_BLOCKLIST_DOMAINS:
            break

    seen = set()
    for domain in redirect_domains:
        d = _clean_domain(domain)
        if not d or d in seen:
            continue
        seen.add(d)
        result['redirect_domains'].append({'domain': d, 'results': _check_domain_blocklists(d)})
        if len(result['redirect_domains']) >= MAX_BLOCKLIST_DOMAINS:
            break

    def _any_listed(checks: Dict) -> bool:
        return any(v.get('listed') for v in (checks or {}).values())

    if _any_listed(result['sending_ip'].get('results')) or _any_listed(result['sending_domain'].get('results')):
        result['any_listed'] = True
    for item in result['link_domains'] + result['redirect_domains']:
        if _any_listed(item.get('results')):
            result['any_listed'] = True
            break

    return result



def init_eml_database() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(ATTACH_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            subject TEXT,
            from_address TEXT,
            to_address TEXT,
            received_at TEXT,
            upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            eml_path TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER NOT NULL,
            filename TEXT,
            content_type TEXT,
            file_path TEXT,
            size_bytes INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER NOT NULL,
            score INTEGER,
            risk_level TEXT,
            authentication TEXT,
            issues TEXT,
            recommendations TEXT,
            metrics TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()


def cleanup_old_records() -> int:
    cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT id, eml_path FROM emails WHERE upload_time < ?', (cutoff,))
    old_emails = cursor.fetchall()

    removed = 0
    for email_id, eml_path in old_emails:
        cursor.execute('SELECT file_path FROM attachments WHERE email_id = ?', (email_id,))
        attachments = [row[0] for row in cursor.fetchall() if row[0]]
        for file_path in attachments:
            try:
                os.remove(file_path)
            except FileNotFoundError:
                pass
        if eml_path:
            try:
                os.remove(eml_path)
            except FileNotFoundError:
                pass
        cursor.execute('DELETE FROM emails WHERE id = ?', (email_id,))
        removed += 1

    conn.commit()
    conn.close()
    return removed


def _save_eml_file(filename: str, content: bytes) -> str:
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', filename)
    file_path = os.path.join(UPLOAD_DIR, f'{timestamp}_{safe_name}')
    with open(file_path, 'wb') as f:
        f.write(content)
    return file_path

def save_eml_file(filename: str, content: bytes) -> str:
    return _save_eml_file(filename, content)


def _parse_eml(content: bytes) -> Dict:
    msg = BytesParser(policy=policy.default).parsebytes(content)
    raw_headers = b''
    if b'\r\n\r\n' in content:
        raw_headers = content.split(b'\r\n\r\n', 1)[0]
    elif b'\n\n' in content:
        raw_headers = content.split(b'\n\n', 1)[0]
    raw_headers_text = raw_headers.decode('utf-8', errors='ignore').lower()
    full_content_text = content.decode('utf-8', errors='ignore').lower()

    subject = msg.get('Subject', '')
    from_name, from_addr = parseaddr(msg.get('From', ''))
    to_name, to_addr = parseaddr(msg.get('To', ''))
    return_path = parseaddr(msg.get('Return-Path', ''))[1]
    reply_to_name, reply_to = parseaddr(msg.get('Reply-To', ''))
    list_unsubscribe = '; '.join([str(h) for h in msg.get_all('List-Unsubscribe', [])])
    list_unsubscribe_post = '; '.join([str(h) for h in msg.get_all('List-Unsubscribe-Post', [])])
    raw_list_unsubscribe = ('list-unsubscribe:' in raw_headers_text) or ('list-unsubscribe:' in full_content_text)
    raw_list_unsubscribe_post = ('list-unsubscribe-post:' in raw_headers_text) or ('list-unsubscribe-post:' in full_content_text)

    auth_results = msg.get_all('Authentication-Results', [])
    received_spf = msg.get_all('Received-SPF', [])
    dkim_sig = msg.get_all('DKIM-Signature', [])
    received_headers = msg.get_all('Received', [])

    text_body = ''
    html_body = ''
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get_content_disposition()
            if disp == 'attachment':
                attachments.append({
                    'filename': part.get_filename() or 'attachment',
                    'content_type': ctype,
                    'payload': part.get_payload(decode=True) or b''
                })
            elif ctype == 'text/plain':
                text_body += part.get_content()
            elif ctype == 'text/html':
                html_body += part.get_content()
    else:
        ctype = msg.get_content_type()
        if ctype == 'text/plain':
            text_body = msg.get_content()
        elif ctype == 'text/html':
            html_body = msg.get_content()

    return {
        'subject': subject,
        'from_name': from_name,
        'from_address': from_addr,
        'to_name': to_name,
        'to_address': to_addr,
        'return_path': return_path,
        'reply_to_name': reply_to_name,
        'reply_to': reply_to,
        'list_unsubscribe': list_unsubscribe,
        'list_unsubscribe_post': list_unsubscribe_post,
        'raw_list_unsubscribe': raw_list_unsubscribe,
        'raw_list_unsubscribe_post': raw_list_unsubscribe_post,
        'raw_content_text': full_content_text,
        'auth_results': auth_results,
        'received_spf': received_spf,
        'dkim_signature': dkim_sig,
        'received_headers': received_headers,
        'text_body': text_body or '',
        'html_body': html_body or '',
        'attachments': attachments
    }


def _extract_auth_results(parsed: Dict) -> Dict:
    auth_header = ' '.join(parsed['auth_results']).lower()
    spf = 'pass' if 'spf=pass' in auth_header else 'fail' if 'spf=fail' in auth_header else 'unknown'
    dkim = 'pass' if 'dkim=pass' in auth_header else 'fail' if 'dkim=fail' in auth_header else 'unknown'
    dmarc = 'pass' if 'dmarc=pass' in auth_header else 'fail' if 'dmarc=fail' in auth_header else 'unknown'

    if spf == 'unknown':
        spf_header = ' '.join(parsed['received_spf']).lower()
        if 'pass' in spf_header:
            spf = 'pass'
        elif 'fail' in spf_header:
            spf = 'fail'

    if dkim == 'unknown' and parsed['dkim_signature']:
        dkim = 'present'

    return {'spf': spf, 'dkim': dkim, 'dmarc': dmarc}





def _extract_sending_ip(parsed: dict) -> str:
    received_headers = parsed.get('received_headers') or []
    # First hop is usually the last Received header
    for header in reversed(received_headers):
        if not header:
            continue
        match = re.search(r'\[(\d{1,3}(?:\.\d{1,3}){3})\]', header)
        if match:
            return match.group(1)
        match = re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', header)
        if match:
            return match.group(1)

    # Fall back to Received-SPF / raw headers
    for spf in (parsed.get('received_spf') or []):
        match = re.search(r'client-ip=([0-9.]+)', spf)
        if match:
            return match.group(1)

    raw = parsed.get('raw_content_text') or ''
    for rx in [r'client-ip=([0-9.]+)', r'x-originating-ip[: ]*\[?([0-9.]+)\]?', r'\[(\d{1,3}(?:\.\d{1,3}){3})\]']:
        match = re.search(rx, raw)
        if match:
            return match.group(1)

    return ''


def _check_rdns(ip: str) -> dict:
    if not ip:
        return {
            'rdns_hostname': '',
            'rdns_valid': False,
            'rdns_note': 'No sending IP found'
        }
    try:
        host, _, _ = socket.gethostbyaddr(ip)
    except Exception as e:
        return {
            'rdns_hostname': '',
            'rdns_valid': False,
            'rdns_note': f'PTR lookup failed ({e.__class__.__name__})'
        }
    # Forward-confirmed reverse DNS (FCrDNS)
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
        valid = ip in addrs
    except Exception:
        valid = False
    note = 'Valid PTR/rDNS' if valid else 'PTR found but forward lookup mismatch'
    return {
        'rdns_hostname': host,
        'rdns_valid': valid,
        'rdns_note': note
    }


def _detect_spam_keywords(text: str) -> List[str]:
    lower = text.lower()
    hits = [kw for kw in SPAM_KEYWORDS if kw in lower]
    return hits


def _detect_hidden_chars(text: str) -> int:
    return len(HIDDEN_CHAR_PATTERN.findall(text))


def _check_html_quality(html: str) -> List[str]:
    issues = []
    if not html:
        return issues

    if html.count('<') != html.count('>'):
        issues.append('Broken HTML tags detected')

    img_tags = re.findall(r'<img\b[^>]*>', html, flags=re.IGNORECASE)
    for tag in img_tags:
        if re.search(r'\balt=', tag, flags=re.IGNORECASE) is None:
            issues.append('Image missing alt text')
            break

    inline_styles = len(re.findall(r'style=', html, flags=re.IGNORECASE))
    if inline_styles > 20:
        issues.append('Excessive inline styles detected')

    return issues


def _extract_links(text: str) -> List[str]:
    return URL_PATTERN.findall(text)


def _normalize_text(value: str) -> str:
    if not value:
        return ''
    raw = value
    if '=\r\n' in raw or '=\n' in raw or '=3D' in raw:
        try:
            raw = quopri.decodestring(raw.encode('utf-8', 'ignore')).decode('utf-8', 'ignore')
        except Exception:
            raw = raw.replace('=\r\n', '').replace('=\n', '')
            raw = raw.replace('=3D', '=')
    return html_module.unescape(raw)
def _extract_preheader(html: str) -> str:
    if not html:
        return ''
    # Normalize html content for extraction
    normalized = html_module.unescape(html)
    normalized = normalized.replace('=\r\n', '').replace('=\n', '')

    patterns = [
        r"<span[^>]+id=\"?preheader\"?[^>]*>(.*?)</span>",
        r"<span[^>]+class=\"?preheader\"?[^>]*>(.*?)</span>",
        r"<div[^>]+id=\"?preheader\"?[^>]*>(.*?)</div>",
        r"<div[^>]+class=\"?preheader\"?[^>]*>(.*?)</div>"
    ]
    for pat in patterns:
        m = re.search(pat, normalized, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r'<[^>]+>', ' ', m.group(1)).strip()

    hidden_block = re.search(
        r"<(span|div)[^>]*style=\"[^\"]*(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|max-height\s*:\s*0|font-size\s*:\s*0|mso-hide\s*:\s*all)[^\"]*\"[^>]*>(.*?)</(span|div)>",
        normalized,
        flags=re.IGNORECASE | re.DOTALL
    )
    if hidden_block:
        return re.sub(r'<[^>]+>', ' ', hidden_block.group(3)).strip()

    return ''

def _describe_char(ch: str) -> Dict:
    code = ord(ch)
    name = unicodedata.name(ch, 'UNKNOWN')
    category = unicodedata.category(ch)
    is_hidden = bool(HIDDEN_CHAR_PATTERN.search(ch))
    return {
        'char': ch,
        'codepoint': f"U+{code:04X}",
        'name': name,
        'category': category,
        'hidden': is_hidden
    }


def _analyze_string_chars(text: str) -> Dict:
    result = {
        'length': len(text),
        'hidden_chars': [],
        'char_map': []
    }
    for idx, ch in enumerate(text):
        info = _describe_char(ch)
        info['index'] = idx
        result['char_map'].append(info)
        if info['hidden']:
            result['hidden_chars'].append(info)
    return result


def _subject_quality(subject: str) -> List[str]:
    issues = []
    if not subject:
        return issues
    if len(subject) > 78:
        issues.append('Subject line is too long')
    if subject.isupper() and len(subject) > 8:
        issues.append('Subject line is all caps')
    if subject.count('!') >= 3 or subject.count('?') >= 3:
        issues.append('Excessive punctuation in subject line')
    return issues


def _image_text_ratio(html: str, text: str) -> Optional[float]:
    if not html:
        return None
    img_count = len(re.findall(r'<img[^>]*>', html, flags=re.IGNORECASE))
    text_len = len(re.sub(r'<[^>]+>', ' ', html)) + len(text)
    if text_len == 0:
        return None
    return img_count / max(1, text_len / 100.0)


def _unsubscribe_present(html: str, text: str) -> bool:
    combined = f"{html} {text}"
    cleaned = HIDDEN_CHAR_PATTERN.sub('', html_module.unescape(combined)).lower()
    if 'unsubscribe' in cleaned or 'optout' in cleaned or 'opt-out' in cleaned or 'manage preferences' in cleaned:
        return True
    for href in _extract_href_links(html):
        lowered = HIDDEN_CHAR_PATTERN.sub('', html_module.unescape(href)).lower()
        if 'unsubscribe' in lowered or 'optout' in lowered or 'opt-out' in lowered:
            return True
    return False


def _extract_href_links(html: str) -> List[str]:
    if not html:
        return []
    return re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)


def _link_checks(html: str, text: str) -> Dict:
    hrefs = _extract_href_links(html)
    urls = _extract_links(text) + _extract_links(html)
    broken = [h for h in hrefs if h.strip() in ('', '#') or h.strip().lower().startswith('javascript:')]
    non_https = [u for u in urls if u.lower().startswith('http://')]
    return {
        'href_count': len(hrefs),
        'broken_links': broken,
        'non_https_links': non_https
    }






def _detect_redirect_domains(urls: List[str]) -> List[Dict]:
    results = []
    for url in urls:
        normalized = _normalize_text(url).strip()
        normalized = re.sub(r"[)>\"']+$", '', normalized)
        try:
            parsed = urlparse(normalized)
        except Exception:
            continue
        qs = parse_qs(parsed.query)
        redirect_param = None
        for key in ('redirect', 'redirect_url', 'url', 'u', 'target'):
            if key in qs and qs[key]:
                redirect_param = qs[key][0]
                break
        if not redirect_param:
            m = re.search(r'(redirect_url|redirect|url|u|target)=([^&]+)', parsed.query)
            if m:
                redirect_param = m.group(2)
        if redirect_param:
            final = unquote(redirect_param)
            try:
                final_parsed = urlparse(final)
                final_domain = final_parsed.netloc.lower() if final_parsed.netloc else ''
            except Exception:
                final_domain = ''
            results.append({
                'tracking_domain': parsed.netloc.lower(),
                'final_domain': final_domain,
                'raw_url': url
            })
    return results

def _domain_alignment_issue(from_address: str, urls: List[str]) -> bool:
    if not from_address or not urls:
        return False
    from_domain = from_address.split('@')[-1].lower()
    link_domains = {re.sub(r'https?://', '', u).split('/')[0].lower() for u in urls}
    # remove tracking subdomains by comparing root contains from domain
    for d in link_domains:
        if from_domain in d:
            return False
    return True


def _analyze_links(urls: List[str]) -> List[str]:
    issues = []
    for url in urls:
        lowered = url.lower()
        if any(k in lowered for k in ['redirect', 'redir', 'url=']):
            issues.append('Redirect-style URL detected')
            break
    if len({re.sub(r'https?://', '', u).split('/')[0] for u in urls}) > 5:
        issues.append('Multiple link domains detected')
    return issues


def _analyze_headers(parsed: Dict) -> List[str]:
    issues = []
    from_domain = parsed['from_address'].split('@')[-1] if parsed['from_address'] else ''
    reply_domain = parsed['reply_to'].split('@')[-1] if parsed['reply_to'] else ''
    return_domain = parsed['return_path'].split('@')[-1] if parsed['return_path'] else ''

    if parsed.get('from_name') and parsed.get('reply_to_name') and parsed['from_name'] != parsed['reply_to_name']:
        issues.append('From vs Reply-To display name mismatch')
    if reply_domain and from_domain and reply_domain != from_domain:
        issues.append('From vs Reply-To domain mismatch')
    if return_domain and from_domain and return_domain != from_domain:
        issues.append('Return-Path domain mismatch')
    if not (parsed.get('list_unsubscribe') or parsed.get('list_unsubscribe_post') or parsed.get('raw_list_unsubscribe') or parsed.get('raw_list_unsubscribe_post')):
        issues.append('Missing List-Unsubscribe header')

    return issues


def _personalization_issues(text: str) -> List[str]:
    if re.search(r'(\{\{.+?\}\}|%%.+?%%|\[FNAME\]|\[LNAME\])', text):
        return ['Potential personalization placeholder found']
    return []


def analyze_eml(content: bytes) -> Dict:
    parsed = _parse_eml(content)

    auth = _extract_auth_results(parsed)
    preheader = _extract_preheader(parsed.get('html_body') or '')
    subject_chars = _analyze_string_chars(html_module.unescape(parsed.get('subject') or ''))
    preheader_chars = _analyze_string_chars(html_module.unescape(preheader or ''))
    body_text = '{0}\n{1}'.format(parsed.get('text_body', ''), parsed.get('html_body', ''))
    body_chars = _analyze_string_chars(html_module.unescape(body_text))

    combined_text = '{0}\n{1}\n{2}'.format(parsed.get('subject', ''), parsed.get('text_body', ''), parsed.get('html_body', ''))
    normalized_combined_text = _normalize_text(combined_text)
    normalized_html = _normalize_text(parsed.get('html_body') or '')
    normalized_raw = _normalize_text(parsed.get('raw_content_text') or '')

    spam_hits = _detect_spam_keywords(combined_text)
    hidden_count = _detect_hidden_chars(combined_text)
    html_issues = _check_html_quality(parsed.get('html_body') or '')
    links = _extract_links(normalized_combined_text)
    href_links = _extract_href_links(normalized_html)
    raw_links = _extract_links(normalized_raw)
    links = list(dict.fromkeys(links + href_links + _extract_links(normalized_html) + raw_links))
    link_issues = _analyze_links(links)
    link_check = _link_checks(parsed.get('html_body') or '', parsed.get('text_body') or '')
    redirect_domains = _detect_redirect_domains(links)
    domain_alignment_issue = _domain_alignment_issue(parsed.get('from_address'), [r.get('final_domain') for r in redirect_domains if r.get('final_domain')] or links)
    header_issues = _analyze_headers(parsed)
    sending_ip = _extract_sending_ip(parsed)
    rdns_info = _check_rdns(sending_ip)
    sending_domain = _clean_domain(parsed.get('from_address'))
    redirect_domain_list = []
    for r in (redirect_domains or []):
        if r.get('tracking_domain'):
            redirect_domain_list.append(r.get('tracking_domain'))
        if r.get('final_domain'):
            redirect_domain_list.append(r.get('final_domain'))
    blocklist = _blocklist_checks(sending_ip, sending_domain, links, redirect_domain_list)
    personalization = _personalization_issues(combined_text)
    subject_issues = _subject_quality(parsed.get('subject') or '')
    img_ratio = _image_text_ratio(parsed.get('html_body') or '', parsed.get('text_body') or '')
    has_unsubscribe = _unsubscribe_present(parsed.get('html_body') or '', parsed.get('text_body') or '')
    if not has_unsubscribe and (parsed.get('list_unsubscribe') or parsed.get('list_unsubscribe_post') or parsed.get('raw_list_unsubscribe') or parsed.get('raw_list_unsubscribe_post')):
        has_unsubscribe = True
    if not has_unsubscribe and parsed.get('raw_content_text') and 'unsubscribe' in parsed.get('raw_content_text'):
        has_unsubscribe = True

    issues = []
    if spam_hits:
        issues.append('Spam-like language detected')
    if hidden_count > 5:
        issues.append('Hidden unicode characters detected')
    if subject_chars['hidden_chars']:
        issues.append('Hidden characters detected in subject line')
    if preheader_chars['hidden_chars']:
        issues.append('Hidden characters detected in preheader')
    if body_chars['hidden_chars']:
        issues.append('Hidden characters detected in body')
    issues.extend(subject_issues)
    if img_ratio is not None and img_ratio > 2.5:
        issues.append('High image-to-text ratio detected')
    if not has_unsubscribe:
        issues.append('Unsubscribe link not found in body')
    if link_check['broken_links']:
        issues.append('Broken or empty links detected')
    if link_check['non_https_links']:
        issues.append('Non-HTTPS links detected')
    if domain_alignment_issue:
        issues.append('From domain does not match landing URL domains')
    if redirect_domains:
        issues.append('Tracking link redirects to a different final domain')
    if blocklist.get('any_listed'):
        issues.append('Blocklist listing detected')
    issues.extend(html_issues)
    issues.extend(link_issues)
    issues.extend(header_issues)
    issues.extend(personalization)

    score = 100
    if auth.get('spf') == 'fail':
        score -= 10
    if auth.get('dkim') == 'fail':
        score -= 10
    if auth.get('dmarc') == 'fail':
        score -= 15
    if hidden_count > 5:
        score -= 15
    if spam_hits:
        score -= 10
    if subject_issues:
        score -= 5
    if img_ratio is not None and img_ratio > 2.5:
        score -= 8
    if not has_unsubscribe:
        score -= 8
    if link_check['broken_links']:
        score -= 6
    if link_check['non_https_links']:
        score -= 6
    if domain_alignment_issue:
        score -= 6
    if redirect_domains:
        score -= 6
    if link_issues:
        score -= 10
    if header_issues:
        score -= 10

    score = max(0, min(100, score))
    risk = 'Low' if score >= 80 else 'Medium' if score >= 60 else 'High'

    recommendations = []
    if auth.get('spf') != 'pass':
        recommendations.append('Fix SPF alignment for the sending domain')
    if auth.get('dkim') != 'pass':
        recommendations.append('Ensure DKIM signature passes')
    if auth.get('dmarc') != 'pass':
        recommendations.append('Configure DMARC policy and alignment')
    if hidden_count > 5:
        recommendations.append('Remove hidden or zero-width characters')
    if subject_chars['hidden_chars']:
        recommendations.append('Remove hidden characters from subject line')
    if preheader_chars['hidden_chars']:
        recommendations.append('Remove hidden characters from preheader text')
    if body_chars['hidden_chars']:
        recommendations.append('Remove hidden characters from body content')
    if spam_hits:
        recommendations.append('Reduce spam-triggering keywords in subject/body')
    if link_issues:
        recommendations.append('Avoid redirect links and limit link domains')
    if subject_issues:
        recommendations.append('Shorten subject line and avoid excessive caps/punctuation')
    if img_ratio is not None and img_ratio > 2.5:
        recommendations.append('Balance images with more text content')
    if not has_unsubscribe:
        recommendations.append('Include a clear unsubscribe link in the body')
    if link_check['broken_links']:
        recommendations.append('Fix broken or empty links')
    if link_check['non_https_links']:
        recommendations.append('Use HTTPS links only')
    if domain_alignment_issue:
        recommendations.append('Align landing page domains with From domain')
    if redirect_domains:
        recommendations.append('Use branded tracking domains that resolve to the same root domain')
    if blocklist.get('any_listed'):
        recommendations.append('Investigate blocklist listings for sending IPs/domains and request delisting')
    if header_issues:
        recommendations.append('Align From, Reply-To, and Return-Path domains')

    metrics = {
        'spam_keyword_hits': spam_hits,
        'hidden_char_count': hidden_count,
        'link_count': len(links),
        'unique_link_domains': len({re.sub(r"https?://", "", u).split('/')[0] for u in links}),
        'attachments_count': len(parsed.get('attachments') or []),
        'subject_issues': subject_issues,
        'image_text_ratio': img_ratio,
        'has_unsubscribe': has_unsubscribe,
        'broken_links': link_check['broken_links'],
        'non_https_links': link_check['non_https_links'],
        'domain_alignment_issue': domain_alignment_issue,
        'redirect_domains': redirect_domains,
        'list_unsubscribe_header': bool(parsed.get('list_unsubscribe') or parsed.get('list_unsubscribe_post') or parsed.get('raw_list_unsubscribe') or parsed.get('raw_list_unsubscribe_post') or ('list-unsubscribe:' in (parsed.get('raw_content_text') or ''))),
        'preheader': preheader,
        'subject_char_map': subject_chars['char_map'],
        'subject_hidden_chars': subject_chars['hidden_chars'],
        'preheader_char_map': preheader_chars['char_map'],
        'preheader_hidden_chars': preheader_chars['hidden_chars'],
        'body_hidden_chars': body_chars['hidden_chars'],
        'return_path': parsed.get('return_path'),
        'reply_to': parsed.get('reply_to'),
        'received_hop_count': len(parsed.get('received_headers') or []),
        'sending_ip': sending_ip,
        'rdns_hostname': rdns_info.get('rdns_hostname'),
        'rdns_valid': rdns_info.get('rdns_valid'),
        'rdns_note': rdns_info.get('rdns_note'),
        'blocklist': blocklist
    }

    return {
        'parsed': parsed,
        'authentication': auth,
        'issues': issues,
        'recommendations': recommendations,
        'score': score,
        'risk_level': risk,
        'metrics': metrics
    }
def save_analysis(filename: str, eml_path: str, analysis: Dict) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    parsed = analysis['parsed']

    cursor.execute('''
        INSERT INTO emails (filename, subject, from_address, to_address, received_at, eml_path)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        filename,
        parsed['subject'],
        parsed['from_address'],
        parsed['to_address'],
        datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        eml_path
    ))

    email_id = cursor.lastrowid

    for attachment in parsed['attachments']:
        att_filename = attachment['filename']
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', att_filename)
        att_path = os.path.join(ATTACH_DIR, f"{email_id}_{safe_name}")
        with open(att_path, 'wb') as f:
            f.write(attachment['payload'])
        cursor.execute('''
            INSERT INTO attachments (email_id, filename, content_type, file_path, size_bytes)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            email_id, att_filename, attachment['content_type'], att_path, len(attachment['payload'])
        ))

    cursor.execute('''
        INSERT INTO analysis_results (email_id, score, risk_level, authentication, issues, recommendations, metrics)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        email_id,
        analysis['score'],
        analysis['risk_level'],
        json.dumps(analysis['authentication']),
        json.dumps(analysis['issues']),
        json.dumps(analysis['recommendations']),
        json.dumps(analysis['metrics'])
    ))

    conn.commit()
    conn.close()

    return email_id


def get_report(email_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM emails WHERE id = ?', (email_id,))
    email_row = cursor.fetchone()
    if not email_row:
        conn.close()
        return None

    cursor.execute('SELECT * FROM analysis_results WHERE email_id = ? ORDER BY created_at DESC LIMIT 1', (email_id,))
    analysis_row = cursor.fetchone()

    cursor.execute('SELECT filename, content_type, size_bytes FROM attachments WHERE email_id = ?', (email_id,))
    attachments = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return {
        'email_id': email_row['id'],
        'filename': email_row['filename'],
        'subject': email_row['subject'],
        'from_address': email_row['from_address'],
        'to_address': email_row['to_address'],
        'received_at': email_row['received_at'],
        'upload_time': email_row['upload_time'],
        'authentication': json.loads(analysis_row['authentication']) if analysis_row else {},
        'issues': json.loads(analysis_row['issues']) if analysis_row else [],
        'recommendations': json.loads(analysis_row['recommendations']) if analysis_row else [],
        'metrics': json.loads(analysis_row['metrics']) if analysis_row else {},
        'score': analysis_row['score'] if analysis_row else 0,
        'risk_level': analysis_row['risk_level'] if analysis_row else 'Unknown',
        'attachments': attachments
    }


def debug_extract_links(content: bytes) -> Dict:
    parsed = _parse_eml(content)
    combined_text = '{0}\n{1}\n{2}'.format(parsed.get('subject', ''), parsed.get('text_body', ''), parsed.get('html_body', ''))
    normalized_combined_text = _normalize_text(combined_text)
    normalized_html = _normalize_text(parsed.get('html_body') or '')
    normalized_raw = _normalize_text(parsed.get('raw_content_text') or '')
    links = _extract_links(normalized_combined_text)
    href_links = _extract_href_links(normalized_html)
    raw_links = _extract_links(normalized_raw)
    all_links = list(dict.fromkeys(links + href_links + _extract_links(normalized_html) + raw_links))
    redirect_domains = _detect_redirect_domains(all_links)
    return {
        'from_address': parsed.get('from_address'),
        'links': all_links,
        'redirect_domains': redirect_domains
    }

