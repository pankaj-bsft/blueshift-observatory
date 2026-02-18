"""
ESP Integration Service
Fetches account info (domains, IPs, subaccounts, pools) from Mailgun, Sparkpost, and Sendgrid
Implements 15-minute caching to avoid rate limits
Uses concurrent requests for faster IP fetching
"""
import requests
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    MAILGUN_API_KEY, MAILGUN_US_BASE_URL, MAILGUN_EU_BASE_URL,
    SPARKPOST_API_KEY, SPARKPOST_BASE_URL,
    SENDGRID_API_KEY, SENDGRID_BASE_URL
)

# Account mappings database path
ACCOUNT_MAPPINGS_DB = '/Users/pankaj/pani/data/account_mappings.db'

# Cache storage
_cache = {
    'data': None,
    'timestamp': None,
    'expiry_minutes': 1440  # 24 hours
}


def get_account_name_mapping() -> Dict[str, str]:
    """
    Get domain -> account_name mapping from domain_account_mapping database

    Returns:
        Dict mapping sending_domain to account_name
    """
    mapping = {}

    try:
        conn = sqlite3.connect(ACCOUNT_MAPPINGS_DB)
        cursor = conn.cursor()

        cursor.execute('SELECT sending_domain, account_name FROM domain_account_mapping')
        rows = cursor.fetchall()

        for domain, account_name in rows:
            mapping[domain.lower()] = account_name

        conn.close()

        print(f'Loaded {len(mapping)} account name mappings')

    except Exception as e:
        print(f'Error loading account mappings: {e}')

    return mapping


def is_cache_valid() -> bool:
    """Check if cached data is still valid (< 24 hours old)"""
    if _cache['data'] is None or _cache['timestamp'] is None:
        return False

    elapsed = datetime.utcnow() - _cache['timestamp']
    return elapsed < timedelta(minutes=_cache['expiry_minutes'])


def get_cached_data() -> Optional[Dict]:
    """Get cached data if valid"""
    if is_cache_valid():
        return _cache['data']
    return None


def set_cache(data: Dict):
    """Store data in cache with current timestamp"""
    _cache['data'] = data
    _cache['timestamp'] = datetime.utcnow()


def fetch_mailgun_domain_ips(base_url: str, domain_name: str) -> str:
    """Helper function to fetch IPs for a single Mailgun domain"""
    try:
        ip_response = requests.get(
            f'{base_url}/domains/{domain_name}/ips',
            auth=('api', MAILGUN_API_KEY),
            timeout=5
        )
        if ip_response.status_code == 200:
            ip_data = ip_response.json()
            ips = ip_data.get('items', [])
            if ips:
                return ', '.join(ips)
        return 'Shared Pool'
    except Exception as e:
        print(f'Error fetching IPs for {domain_name}: {e}')
        return 'Shared Pool'


def fetch_mailgun_data() -> List[Dict]:
    """
    Fetch domain and IP info from Mailgun (both US and EU regions)
    Uses /domains/{domain}/ips endpoint with parallel requests for performance

    Returns list of dicts with structure:
    {
        'esp': 'Mailgun',
        'region': 'US' or 'EU',
        'domain': 'example.com',
        'subaccount': 'N/A or actual value',
        'ip_pool': 'pool name or N/A',
        'status': 'active/disabled',
        'verified': True/False,
        'created_at': 'ISO date'
    }
    """
    results = []

    # Fetch from both US and EU regions
    for region, base_url in [('US', MAILGUN_US_BASE_URL), ('EU', MAILGUN_EU_BASE_URL)]:
        try:
            # Fetch domains
            response = requests.get(
                f'{base_url}/domains',
                auth=('api', MAILGUN_API_KEY),
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                domains = data.get('items', [])

                # Fetch IPs for all domains in parallel
                domain_ips = {}
                with ThreadPoolExecutor(max_workers=10) as executor:
                    future_to_domain = {
                        executor.submit(fetch_mailgun_domain_ips, base_url, domain.get('name', 'N/A')): domain.get('name', 'N/A')
                        for domain in domains
                    }

                    for future in as_completed(future_to_domain):
                        domain_name = future_to_domain[future]
                        try:
                            domain_ips[domain_name] = future.result()
                        except Exception as e:
                            print(f'Error fetching IPs for {domain_name}: {e}')
                            domain_ips[domain_name] = 'Shared Pool'

                # Build results with fetched IPs
                for domain in domains:
                    domain_name = domain.get('name', 'N/A')
                    results.append({
                        'esp': 'Mailgun',
                        'region': region,
                        'domain': domain_name,
                        'ip_addresses': domain_ips.get(domain_name, 'Shared Pool'),
                        'subaccount': 'N/A',  # Mailgun doesn't have subaccounts in same way
                        'ip_pool': 'N/A',
                        'status': domain.get('state', 'unknown'),
                        'verified': domain.get('state') == 'active',
                        'created_at': domain.get('created_at', 'N/A')
                    })

                print(f'Fetched {len(domains)} Mailgun {region} domains with IPs')
            else:
                print(f'Mailgun {region} API error: {response.status_code}')

        except Exception as e:
            print(f'Error fetching Mailgun {region} data: {e}')

    return results


def fetch_sparkpost_domain_pool(domain_name: str) -> tuple:
    """
    Helper function to fetch IP pool assignment for a single Sparkpost domain
    Returns (ip_pool_name, ip_list_as_string)
    """
    try:
        detail_response = requests.get(
            f'{SPARKPOST_BASE_URL}/sending-domains/{domain_name}',
            headers={'Authorization': SPARKPOST_API_KEY},
            timeout=5
        )
        if detail_response.status_code == 200:
            detail_data = detail_response.json()
            domain_detail = detail_data.get('results', {})

            # Check for IP pool assignment
            # Sparkpost can assign domains to IP pools
            ip_pool = domain_detail.get('ip_pool', '')

            # Also check for direct IP assignment
            assigned_ips = domain_detail.get('sending_ips', [])
            if assigned_ips and isinstance(assigned_ips, list):
                ip_list = []
                for ip_info in assigned_ips:
                    if isinstance(ip_info, dict):
                        ext_ip = ip_info.get('external_ip', ip_info.get('ip', ''))
                        if ext_ip:
                            ip_list.append(ext_ip)
                    elif isinstance(ip_info, str):
                        ip_list.append(ip_info)

                if ip_list:
                    return (ip_pool if ip_pool else 'Direct Assignment', ', '.join(ip_list))

            # Return pool name if found (IPs will be looked up from pool map)
            if ip_pool:
                return (ip_pool, None)

        return (None, None)
    except Exception as e:
        print(f'Error fetching domain detail for {domain_name}: {e}')
        return (None, None)


def fetch_sparkpost_data() -> List[Dict]:
    """
    Fetch sending domains and IP info from Sparkpost
    Maps: Domain → IP Pool → IPs using IP pools endpoint

    Since Sparkpost doesn't expose domain→pool mappings in the API, we:
    1. Fetch all IP pools with their IPs from /ip-pools
    2. Match domains to pools by name (pool names often contain domain names)
    3. For matched domains, show pool IPs; otherwise show "Shared Pool"

    Returns list of dicts with same structure as Mailgun
    """
    results = []

    try:
        # Step 1: Fetch all IP pools with their IPs
        ip_pool_map = {}  # pool_id -> list of IPs
        pool_names = {}  # pool_id -> pool_name
        pool_by_domain = {}  # domain -> pool_id (for name-based matching)

        try:
            pools_response = requests.get(
                f'{SPARKPOST_BASE_URL}/ip-pools',
                headers={'Authorization': SPARKPOST_API_KEY},
                timeout=10
            )
            if pools_response.status_code == 200:
                pools_data = pools_response.json()
                pools = pools_data.get('results', [])

                for pool in pools:
                    pool_id = pool.get('id', '')
                    pool_name = pool.get('name', '')
                    ips_data = pool.get('ips', [])

                    if pool_id and ips_data:
                        # Extract IP addresses from IP objects
                        ips = [ip_info.get('external_ip') for ip_info in ips_data if ip_info.get('external_ip')]
                        if ips:
                            ip_pool_map[pool_id] = ips
                            pool_names[pool_id] = pool_name

                            # Try to extract domain from pool name for matching
                            # Pool names like "Blue Modo Media - mail.grantgiven.com" or "animalplanet"
                            if ' - ' in pool_name:
                                # Format: "Company - domain.com"
                                potential_domain = pool_name.split(' - ')[-1].strip()
                                if '.' in potential_domain:
                                    pool_by_domain[potential_domain.lower()] = pool_id
                            elif '.' in pool_id and pool_id.count('.') >= 1:
                                # Pool ID itself might be a domain (e.g., pool_id = "example.com")
                                pool_by_domain[pool_id.lower()] = pool_id
                            elif '.' in pool_name and pool_name.count('.') >= 1:
                                # Pool name is a domain
                                pool_by_domain[pool_name.lower()] = pool_id

                print(f'Loaded {len(pools)} Sparkpost IP pools, {len(ip_pool_map)} with IPs')
                print(f'Direct matches: {len(pool_by_domain)} pools to domain names')
        except Exception as e:
            print(f'Error fetching Sparkpost IP pools: {e}')

        # Step 2: Fetch sending domains
        response = requests.get(
            f'{SPARKPOST_BASE_URL}/sending-domains',
            headers={'Authorization': SPARKPOST_API_KEY},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            domains = data.get('results', [])

            matched_domains = 0

            # Step 3: Build results by matching domains to pools
            for domain in domains:
                domain_name = domain.get('domain', 'N/A')
                status_data = domain.get('status', {})

                # Try to find matching pool for this domain
                ip_addresses = 'Shared Pool'
                ip_pool_display = 'N/A'
                matched_pool_id = None

                # Strategy 1: Exact domain match
                if domain_name.lower() in pool_by_domain:
                    matched_pool_id = pool_by_domain[domain_name.lower()]

                # Strategy 2: Base domain match (e.g., pr.zumper.com → zumper.com)
                if not matched_pool_id and '.' in domain_name:
                    parts = domain_name.lower().split('.')
                    if len(parts) >= 3:
                        # Try matching base domain (e.g., zumper.com from pr.zumper.com)
                        base_domain = '.'.join(parts[-2:])
                        if base_domain in pool_by_domain:
                            matched_pool_id = pool_by_domain[base_domain]

                # Strategy 3: Strip common email prefixes and try base domain
                if not matched_pool_id:
                    # Common email subdomain prefixes to strip
                    email_prefixes = ['bounces', 'em', 'email', 'notify', 'orders', 'trans',
                                     'services', 'info', 'news', 'mail', 'newsletters', 'alerts']

                    domain_parts = domain_name.lower().split('.')
                    if len(domain_parts) >= 3 and domain_parts[0] in email_prefixes:
                        # Strip the email prefix and try matching the rest
                        stripped_domain = '.'.join(domain_parts[1:])
                        if stripped_domain in pool_by_domain:
                            matched_pool_id = pool_by_domain[stripped_domain]

                # Strategy 4: Fuzzy match - look for domain keywords in ALL pool names/IDs
                if not matched_pool_id:
                    # Extract key parts of domain name (e.g., "zumper" from "pr.zumper.com")
                    domain_keywords = set()
                    for part in domain_name.lower().replace('-', '_').replace('.', '_').split('_'):
                        if len(part) > 3 and part not in ['com', 'net', 'org', 'www', 'mail', 'email',
                                                           'bounces', 'trans', 'services', 'orders', 'emails']:
                            domain_keywords.add(part)

                    # Look for pools (by ID or name) that contain these keywords
                    if domain_keywords:
                        # Search ALL pools, not just pool_by_domain
                        for pool_id in ip_pool_map.keys():
                            pool_name = pool_names.get(pool_id, '')

                            # Check if any domain keyword appears in pool ID or pool name
                            pool_text = f"{pool_id} {pool_name}".lower()
                            for keyword in domain_keywords:
                                if keyword in pool_text:
                                    matched_pool_id = pool_id
                                    break

                            if matched_pool_id:
                                break

                # Apply matched pool IPs
                if matched_pool_id and matched_pool_id in ip_pool_map:
                    ips = ip_pool_map[matched_pool_id]
                    ip_addresses = ', '.join(ips)
                    ip_pool_display = pool_names.get(matched_pool_id, matched_pool_id)
                    matched_domains += 1

                results.append({
                    'esp': 'Sparkpost',
                    'region': 'N/A',
                    'domain': domain_name,
                    'ip_addresses': ip_addresses,
                    'subaccount': str(domain.get('subaccount_id', 'N/A')),
                    'ip_pool': ip_pool_display,
                    'status': status_data.get('ownership_verified', False) and 'active' or 'pending',
                    'verified': status_data.get('ownership_verified', False),
                    'created_at': 'N/A'  # Sparkpost doesn't return creation date in list
                })

            print(f'Fetched {len(domains)} Sparkpost domains, matched {matched_domains} to IP pools')
        else:
            print(f'Sparkpost API error: {response.status_code}')

    except Exception as e:
        print(f'Error fetching Sparkpost data: {e}')

    return results


def fetch_sendgrid_data() -> List[Dict]:
    """
    Fetch subusers and authenticated domains from Sendgrid
    Uses /v3/ips endpoint to get all IPs and maps them to domains

    Returns list of dicts with same structure as others
    """
    results = []

    # First, fetch all IPs to create a mapping
    all_ips_map = {}  # Maps IP -> list of subusers/domains
    try:
        ips_response = requests.get(
            f'{SENDGRID_BASE_URL}/ips',
            headers={
                'Authorization': f'Bearer {SENDGRID_API_KEY}',
                'Content-Type': 'application/json'
            },
            timeout=10
        )
        if ips_response.status_code == 200:
            all_ips = ips_response.json()
            for ip_info in all_ips:
                ip_addr = ip_info.get('ip', '')
                subusers = ip_info.get('subusers', [])
                if ip_addr:
                    all_ips_map[ip_addr] = subusers
            print(f'Loaded {len(all_ips)} Sendgrid IPs')
    except Exception as e:
        print(f'Error fetching Sendgrid IPs: {e}')

    try:
        # Fetch authenticated domains
        response = requests.get(
            f'{SENDGRID_BASE_URL}/whitelabel/domains',
            headers={
                'Authorization': f'Bearer {SENDGRID_API_KEY}',
                'Content-Type': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            domains = response.json()

            for domain in domains:
                # Check if domain has IPs in the response
                ips = domain.get('ips', [])

                # If no IPs in domain response, try to find IPs assigned to this domain's subuser
                if not ips:
                    username = domain.get('username')
                    if username and username != 'N/A':
                        # Find IPs assigned to this subuser
                        for ip_addr, subusers in all_ips_map.items():
                            if username in subusers:
                                ips.append(ip_addr)

                results.append({
                    'esp': 'Sendgrid',
                    'region': 'N/A',
                    'domain': domain.get('domain', 'N/A'),
                    'ip_addresses': ', '.join(ips) if ips else 'Shared Pool',
                    'subaccount': domain.get('username', 'N/A'),
                    'ip_pool': ', '.join(ips) if ips else 'N/A',
                    'status': 'active' if domain.get('valid', False) else 'invalid',
                    'verified': domain.get('valid', False),
                    'created_at': 'N/A'  # Not in API response
                })
        else:
            print(f'Sendgrid API error: {response.status_code} - {response.text}')

    except Exception as e:
        print(f'Error fetching Sendgrid data: {e}')

    # Also try to fetch subusers
    try:
        response = requests.get(
            f'{SENDGRID_BASE_URL}/subusers',
            headers={
                'Authorization': f'Bearer {SENDGRID_API_KEY}',
                'Content-Type': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            subusers = response.json()

            # For each subuser, fetch their authenticated domains
            for subuser in subusers[:10]:  # Limit to first 10 to avoid rate limits
                username = subuser.get('username')

                # Fetch domains for this subuser using on-behalf-of
                sub_response = requests.get(
                    f'{SENDGRID_BASE_URL}/whitelabel/domains',
                    headers={
                        'Authorization': f'Bearer {SENDGRID_API_KEY}',
                        'Content-Type': 'application/json',
                        'on-behalf-of': username
                    },
                    timeout=10
                )

                if sub_response.status_code == 200:
                    sub_domains = sub_response.json()

                    for domain in sub_domains:
                        # Check if domain has IPs in the response
                        ips = domain.get('ips', [])

                        # If no IPs in domain response, try to find IPs assigned to this subuser
                        if not ips and username:
                            # Find IPs assigned to this subuser
                            for ip_addr, subusers_list in all_ips_map.items():
                                if username in subusers_list:
                                    ips.append(ip_addr)

                        results.append({
                            'esp': 'Sendgrid',
                            'region': 'N/A',
                            'domain': domain.get('domain', 'N/A'),
                            'ip_addresses': ', '.join(ips) if ips else 'Shared Pool',
                            'subaccount': username,
                            'ip_pool': ', '.join(ips) if ips else 'N/A',
                            'status': 'active' if domain.get('valid', False) else 'invalid',
                            'verified': domain.get('valid', False),
                            'created_at': 'N/A'
                        })

    except Exception as e:
        print(f'Error fetching Sendgrid subusers: {e}')

    return results


def get_all_account_info(force_refresh: bool = False) -> Dict:
    """
    Fetch account info from all ESPs with 24-hour caching

    Args:
        force_refresh: If True, bypass cache and fetch fresh data

    Returns:
        Dict with structure:
        {
            'status': 'success',
            'cached': True/False,
            'last_updated': ISO timestamp,
            'cache_expires_in': seconds,
            'total_records': int,
            'data': [list of account info dicts],
            'errors': {
                'mailgun': error message or None,
                'sparkpost': error message or None,
                'sendgrid': error message or None
            }
        }
    """
    # Check cache first
    if not force_refresh:
        cached = get_cached_data()
        if cached:
            # Calculate time until expiry
            elapsed = datetime.utcnow() - _cache['timestamp']
            remaining = timedelta(minutes=_cache['expiry_minutes']) - elapsed

            # IMPORTANT: Always refresh account mappings even on cached ESP data
            # because mappings can be updated independently
            account_mapping = get_account_name_mapping()
            for item in cached['data']:
                domain = item['domain'].lower()
                item['account_name'] = account_mapping.get(domain, 'Unmapped')

            return {
                **cached,
                'cached': True,
                'cache_expires_in': int(remaining.total_seconds())
            }

    # Fetch fresh data from all ESPs
    errors = {
        'mailgun': None,
        'sparkpost': None,
        'sendgrid': None
    }

    all_data = []

    # Fetch Mailgun
    try:
        mailgun_data = fetch_mailgun_data()
        all_data.extend(mailgun_data)
    except Exception as e:
        errors['mailgun'] = str(e)
        print(f'Mailgun fetch failed: {e}')

    # Fetch Sparkpost
    try:
        sparkpost_data = fetch_sparkpost_data()
        all_data.extend(sparkpost_data)
    except Exception as e:
        errors['sparkpost'] = str(e)
        print(f'Sparkpost fetch failed: {e}')

    # Fetch Sendgrid
    try:
        sendgrid_data = fetch_sendgrid_data()
        all_data.extend(sendgrid_data)
    except Exception as e:
        errors['sendgrid'] = str(e)
        print(f'Sendgrid fetch failed: {e}')

    # Add account names from account mappings
    account_mapping = get_account_name_mapping()

    for item in all_data:
        domain = item['domain'].lower()
        item['account_name'] = account_mapping.get(domain, 'Unmapped')

    # Build response
    response = {
        'status': 'success',
        'cached': False,
        'last_updated': datetime.utcnow().isoformat(),
        'cache_expires_in': _cache['expiry_minutes'] * 60,
        'total_records': len(all_data),
        'data': all_data,
        'errors': errors
    }

    # Store in cache
    set_cache(response)

    return response


def clear_cache():
    """Manually clear the cache"""
    _cache['data'] = None
    _cache['timestamp'] = None
