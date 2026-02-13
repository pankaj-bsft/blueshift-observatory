"""
Google Postmaster Tools (GPT) Analytics Service
Provides aggregations, trends, and insights from GPT data
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

GPT_DB_PATH = '/Users/pankaj/pani/data/gpt_data.db'


def get_overview_stats(days: int = 30) -> Dict:
    """
    Get overview statistics for all domains
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    # Calculate date range
    end_date = datetime.utcnow().strftime('%Y-%m-%d')
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Get latest data for each domain within period
    cursor.execute('''
        SELECT
            COUNT(DISTINCT domain) as total_domains,
            AVG(reputation_value) as avg_reputation_value,
            AVG(spam_rate) as avg_spam_rate,
            AVG(user_reported_spam_rate) as avg_user_spam_rate,
            AVG(spf_success_rate) as avg_spf_rate,
            AVG(dkim_success_rate) as avg_dkim_rate,
            AVG(dmarc_success_rate) as avg_dmarc_rate,
            AVG(tls_rate) as avg_tls_rate
        FROM (
            SELECT domain, reputation_value, spam_rate, user_reported_spam_rate,
                   spf_success_rate, dkim_success_rate, dmarc_success_rate, tls_rate
            FROM gpt_data
            WHERE data_date >= ? AND data_date <= ?
            ORDER BY data_date DESC
        )
        GROUP BY domain
    ''', (start_date, end_date))

    row = cursor.fetchone()

    # Get reputation distribution
    cursor.execute('''
        SELECT reputation, COUNT(DISTINCT domain) as count
        FROM (
            SELECT domain, reputation,
                   ROW_NUMBER() OVER (PARTITION BY domain ORDER BY data_date DESC) as rn
            FROM gpt_data
            WHERE data_date >= ? AND data_date <= ?
        )
        WHERE rn = 1
        GROUP BY reputation
    ''', (start_date, end_date))

    reputation_dist = {}
    for rep_row in cursor.fetchall():
        reputation_dist[rep_row[0]] = rep_row[1]

    conn.close()

    if not row or row[0] == 0:
        return {
            'period': f'{days}day',
            'start_date': start_date,
            'end_date': end_date,
            'total_domains': 0,
            'avg_reputation': 'UNKNOWN',
            'avg_reputation_value': 0,
            'avg_spam_rate': 0,
            'avg_user_spam_rate': 0,
            'avg_spf_rate': 0,
            'avg_dkim_rate': 0,
            'avg_dmarc_rate': 0,
            'avg_tls_rate': 0,
            'reputation_distribution': {}
        }

    # Convert reputation value back to text
    rep_value = row[1] or 0
    if rep_value >= 3.5:
        avg_reputation = 'HIGH'
    elif rep_value >= 2.5:
        avg_reputation = 'MEDIUM'
    elif rep_value >= 1.5:
        avg_reputation = 'LOW'
    else:
        avg_reputation = 'BAD'

    return {
        'period': f'{days}day',
        'start_date': start_date,
        'end_date': end_date,
        'total_domains': row[0] or 0,
        'avg_reputation': avg_reputation,
        'avg_reputation_value': round(rep_value, 2),
        'avg_spam_rate': round(row[2] or 0, 2),
        'avg_user_spam_rate': round(row[3] or 0, 2),
        'avg_spf_rate': round(row[4] or 0, 2),
        'avg_dkim_rate': round(row[5] or 0, 2),
        'avg_dmarc_rate': round(row[6] or 0, 2),
        'avg_tls_rate': round(row[7] or 0, 2),
        'reputation_distribution': reputation_dist
    }


def get_domain_data(days: int = 30, domain: str = None) -> List[Dict]:
    """
    Get detailed domain data
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    end_date = datetime.utcnow().strftime('%Y-%m-%d')
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    if domain:
        cursor.execute('''
            SELECT domain, data_date, reputation, reputation_value,
                   spam_rate, user_reported_spam_rate,
                   spf_success_rate, dkim_success_rate, dmarc_success_rate,
                   tls_rate, verified
            FROM gpt_data
            WHERE domain = ? AND data_date >= ? AND data_date <= ?
            ORDER BY data_date DESC
        ''', (domain, start_date, end_date))
    else:
        # Get latest record for each domain
        cursor.execute('''
            SELECT domain, data_date, reputation, reputation_value,
                   spam_rate, user_reported_spam_rate,
                   spf_success_rate, dkim_success_rate, dmarc_success_rate,
                   tls_rate, verified
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY domain ORDER BY data_date DESC) as rn
                FROM gpt_data
                WHERE data_date >= ? AND data_date <= ?
            )
            WHERE rn = 1
            ORDER BY reputation_value DESC, domain
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    data = []
    for row in rows:
        data.append({
            'domain': row[0],
            'date': row[1],
            'reputation': row[2],
            'reputation_value': row[3],
            'spam_rate': round(row[4] or 0, 2),
            'user_spam_rate': round(row[5] or 0, 2),
            'spf_rate': round(row[6] or 0, 2),
            'dkim_rate': round(row[7] or 0, 2),
            'dmarc_rate': round(row[8] or 0, 2),
            'tls_rate': round(row[9] or 0, 2),
            'verified': bool(row[10])
        })

    return data


def get_reputation_trends(days: int = 30, domain: str = None) -> Dict:
    """
    Get reputation trends over time
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    end_date = datetime.utcnow().strftime('%Y-%m-%d')
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    if domain:
        cursor.execute('''
            SELECT data_date, AVG(reputation_value) as avg_rep
            FROM gpt_data
            WHERE domain = ? AND data_date >= ? AND data_date <= ?
            GROUP BY data_date
            ORDER BY data_date
        ''', (domain, start_date, end_date))
    else:
        cursor.execute('''
            SELECT data_date, AVG(reputation_value) as avg_rep
            FROM gpt_data
            WHERE data_date >= ? AND data_date <= ?
            GROUP BY data_date
            ORDER BY data_date
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    dates = []
    values = []

    for row in rows:
        dates.append(row[0])
        values.append(round(row[1] or 0, 2))

    return {
        'dates': dates,
        'values': values,
        'period': f'{days}day'
    }


def get_spam_trends(days: int = 30, domain: str = None) -> Dict:
    """
    Get spam rate trends over time
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    end_date = datetime.utcnow().strftime('%Y-%m-%d')
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    if domain:
        cursor.execute('''
            SELECT data_date,
                   AVG(spam_rate) as avg_spam,
                   AVG(user_reported_spam_rate) as avg_user_spam
            FROM gpt_data
            WHERE domain = ? AND data_date >= ? AND data_date <= ?
            GROUP BY data_date
            ORDER BY data_date
        ''', (domain, start_date, end_date))
    else:
        cursor.execute('''
            SELECT data_date,
                   AVG(spam_rate) as avg_spam,
                   AVG(user_reported_spam_rate) as avg_user_spam
            FROM gpt_data
            WHERE data_date >= ? AND data_date <= ?
            GROUP BY data_date
            ORDER BY data_date
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    dates = []
    spam_rates = []
    user_spam_rates = []

    for row in rows:
        dates.append(row[0])
        spam_rates.append(round(row[1] or 0, 2))
        user_spam_rates.append(round(row[2] or 0, 2))

    return {
        'dates': dates,
        'spam_rates': spam_rates,
        'user_spam_rates': user_spam_rates,
        'period': f'{days}day'
    }


def get_auth_trends(days: int = 30, domain: str = None) -> Dict:
    """
    Get authentication success rate trends
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    end_date = datetime.utcnow().strftime('%Y-%m-%d')
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    if domain:
        cursor.execute('''
            SELECT data_date,
                   AVG(spf_success_rate) as avg_spf,
                   AVG(dkim_success_rate) as avg_dkim,
                   AVG(dmarc_success_rate) as avg_dmarc
            FROM gpt_data
            WHERE domain = ? AND data_date >= ? AND data_date <= ?
            GROUP BY data_date
            ORDER BY data_date
        ''', (domain, start_date, end_date))
    else:
        cursor.execute('''
            SELECT data_date,
                   AVG(spf_success_rate) as avg_spf,
                   AVG(dkim_success_rate) as avg_dkim,
                   AVG(dmarc_success_rate) as avg_dmarc
            FROM gpt_data
            WHERE data_date >= ? AND data_date <= ?
            GROUP BY data_date
            ORDER BY data_date
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    dates = []
    spf_rates = []
    dkim_rates = []
    dmarc_rates = []

    for row in rows:
        dates.append(row[0])
        spf_rates.append(round(row[1] or 0, 2))
        dkim_rates.append(round(row[2] or 0, 2))
        dmarc_rates.append(round(row[3] or 0, 2))

    return {
        'dates': dates,
        'spf_rates': spf_rates,
        'dkim_rates': dkim_rates,
        'dmarc_rates': dmarc_rates,
        'period': f'{days}day'
    }


def get_reputation_changes() -> List[Dict]:
    """
    Get domains with reputation changes from yesterday
    Returns list of domains with change notification
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    today = datetime.utcnow().strftime('%Y-%m-%d')
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

    # Get reputation for today and yesterday for each domain
    cursor.execute('''
        SELECT
            t.domain,
            t.reputation as today_rep,
            t.reputation_value as today_val,
            t.spam_rate as today_spam,
            y.reputation as yesterday_rep,
            y.reputation_value as yesterday_val,
            y.spam_rate as yesterday_spam
        FROM
            (SELECT domain, reputation, reputation_value, spam_rate
             FROM gpt_data WHERE data_date = ?) t
        LEFT JOIN
            (SELECT domain, reputation, reputation_value, spam_rate
             FROM gpt_data WHERE data_date = ?) y
        ON t.domain = y.domain
    ''', (today, yesterday))

    rows = cursor.fetchall()
    conn.close()

    changes = []

    for row in rows:
        domain = row[0]
        today_rep = row[1]
        today_val = row[2]
        today_spam = row[3] or 0
        yesterday_rep = row[4]
        yesterday_val = row[5] or 0
        yesterday_spam = row[6] or 0

        has_change = False
        change_type = 'stable'
        message = ''

        # Check reputation change
        if yesterday_rep and today_rep != yesterday_rep:
            has_change = True
            if today_val < yesterday_val:
                change_type = 'degraded'
                message = f'Reputation dropped from {yesterday_rep} to {today_rep}'
            else:
                change_type = 'improved'
                message = f'Reputation improved from {yesterday_rep} to {today_rep}'

        # Check spam rate increase (even if reputation is same)
        elif yesterday_spam > 0 and today_spam > yesterday_spam * 1.5:  # 50% increase
            has_change = True
            change_type = 'spam_increase'
            message = f'Spam rate increased from {yesterday_spam:.1f}% to {today_spam:.1f}%'

        if has_change:
            changes.append({
                'domain': domain,
                'change_type': change_type,
                'message': message,
                'today_reputation': today_rep,
                'yesterday_reputation': yesterday_rep,
                'today_spam_rate': round(today_spam, 2),
                'yesterday_spam_rate': round(yesterday_spam, 2)
            })

    return changes


def get_domains_list() -> List[str]:
    """Get list of all domains being tracked"""
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT DISTINCT domain FROM gpt_data ORDER BY domain')
    rows = cursor.fetchall()

    conn.close()

    return [row[0] for row in rows]


def get_yesterday_overview() -> List[Dict]:
    """
    Get most recent data for all domains (for overview table)
    Shows latest available data for each domain, not just yesterday
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    # Attach account mappings database
    cursor.execute("ATTACH DATABASE '/Users/pankaj/pani/data/account_mappings.db' AS mappings")

    # Get latest data for each domain with account name (subquery finds max date per domain)
    cursor.execute('''
        SELECT
            g.domain,
            g.reputation as domain_reputation,
            g.reputation_value as domain_reputation_value,
            g.ip_reputation,
            g.spam_rate,
            g.user_reported_spam_rate,
            g.spf_success_rate,
            g.dkim_success_rate,
            g.dmarc_success_rate,
            g.tls_rate,
            g.data_date,
            g.verified,
            m.account_name
        FROM gpt_data g
        INNER JOIN (
            SELECT domain, MAX(data_date) as max_date
            FROM gpt_data
            GROUP BY domain
        ) latest ON g.domain = latest.domain AND g.data_date = latest.max_date
        LEFT JOIN mappings.domain_account_mapping m ON g.domain = m.sending_domain
        ORDER BY g.domain
    ''')

    rows = cursor.fetchall()
    conn.close()

    data = []
    for row in rows:
        # Parse IP reputation JSON
        ip_rep_json = row[3]
        ip_rep_breakdown = {}
        ip_rep_samples = {}
        if ip_rep_json:
            try:
                ip_data = json.loads(ip_rep_json)
                ip_rep_breakdown = ip_data.get('breakdown', {})
                ip_rep_samples = ip_data.get('samples', {})
            except (json.JSONDecodeError, TypeError):
                # Old format (single string) - convert to breakdown
                if ip_rep_json and ip_rep_json != 'N/A':
                    ip_rep_breakdown = {ip_rep_json: 1}

        data.append({
            'domain': row[0],
            'domain_reputation': row[1],
            'domain_reputation_value': row[2],
            'ip_reputation': ip_rep_breakdown,  # Now an object with counts
            'ip_reputation_samples': ip_rep_samples,  # Sample IPs
            'spam_rate': round(row[4] or 0, 2),
            'user_spam_rate': round(row[5] or 0, 2),
            'spf_rate': round(row[6] or 0, 2),
            'dkim_rate': round(row[7] or 0, 2),
            'dmarc_rate': round(row[8] or 0, 2),
            'tls_rate': round(row[9] or 0, 2),
            'date': row[10],
            'verified': bool(row[11]),
            'account_name': row[12] or 'N/A'
        })

    return data


def get_all_domains_latest() -> List[Dict]:
    """
    Get latest data for all domains (fallback if yesterday has no data)
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    # Attach account mappings database
    cursor.execute("ATTACH DATABASE '/Users/pankaj/pani/data/account_mappings.db' AS mappings")

    cursor.execute('''
        SELECT
            g.domain,
            g.reputation as domain_reputation,
            g.reputation_value as domain_reputation_value,
            g.ip_reputation,
            g.spam_rate,
            g.user_reported_spam_rate,
            g.spf_success_rate,
            g.dkim_success_rate,
            g.dmarc_success_rate,
            g.tls_rate,
            g.data_date,
            g.verified,
            m.account_name
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY domain ORDER BY data_date DESC) as rn
            FROM gpt_data
        ) g
        LEFT JOIN mappings.domain_account_mapping m ON g.domain = m.sending_domain
        WHERE g.rn = 1
        ORDER BY g.domain
    ''')

    rows = cursor.fetchall()
    conn.close()

    data = []
    for row in rows:
        # Parse IP reputation JSON
        ip_rep_json = row[3]
        ip_rep_breakdown = {}
        ip_rep_samples = {}
        if ip_rep_json:
            try:
                ip_data = json.loads(ip_rep_json)
                ip_rep_breakdown = ip_data.get('breakdown', {})
                ip_rep_samples = ip_data.get('samples', {})
            except (json.JSONDecodeError, TypeError):
                # Old format (single string) - convert to breakdown
                if ip_rep_json and ip_rep_json != 'N/A':
                    ip_rep_breakdown = {ip_rep_json: 1}

        data.append({
            'domain': row[0],
            'domain_reputation': row[1],
            'domain_reputation_value': row[2],
            'ip_reputation': ip_rep_breakdown,  # Now an object with counts
            'ip_reputation_samples': ip_rep_samples,  # Sample IPs
            'spam_rate': round(row[4] or 0, 2),
            'user_spam_rate': round(row[5] or 0, 2),
            'spf_rate': round(row[6] or 0, 2),
            'dkim_rate': round(row[7] or 0, 2),
            'dmarc_rate': round(row[8] or 0, 2),
            'tls_rate': round(row[9] or 0, 2),
            'date': row[10],
            'verified': bool(row[11]),
            'account_name': row[12] or 'N/A'
        })

    return data


def get_enhanced_reputation_changes(days_back: int = 2) -> List[Dict]:
    """
    Get domains with reputation changes (both IP and Domain)
    Compares most recent data vs data from days_back days ago
    Default: compares last 2 days of available data for each domain
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    # Get the two most recent dates with data for each domain
    cutoff_date = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    # Get the two most recent records for each domain
    cursor.execute('''
        WITH ranked_data AS (
            SELECT
                domain,
                reputation,
                reputation_value,
                ip_reputation,
                spam_rate,
                data_date,
                ROW_NUMBER() OVER (PARTITION BY domain ORDER BY data_date DESC) as rn
            FROM gpt_data
            WHERE data_date >= ?
        ),
        recent AS (
            SELECT * FROM ranked_data WHERE rn = 1
        ),
        previous AS (
            SELECT * FROM ranked_data WHERE rn = 2
        )
        SELECT
            r.domain,
            r.reputation as recent_domain_rep,
            r.reputation_value as recent_domain_val,
            r.ip_reputation as recent_ip_rep,
            r.spam_rate as recent_spam,
            r.data_date as recent_date,
            p.reputation as prev_domain_rep,
            p.reputation_value as prev_domain_val,
            p.ip_reputation as prev_ip_rep,
            p.spam_rate as prev_spam,
            p.data_date as prev_date
        FROM recent r
        LEFT JOIN previous p ON r.domain = p.domain
        WHERE p.domain IS NOT NULL
    ''', (cutoff_date,))

    rows = cursor.fetchall()
    conn.close()

    changes = []

    for row in rows:
        domain = row[0]
        recent_domain_rep = row[1]
        recent_domain_val = row[2] or 0
        recent_ip_rep = row[3]
        recent_spam = row[4] or 0
        recent_date = row[5]
        prev_domain_rep = row[6]
        prev_domain_val = row[7] or 0
        prev_ip_rep = row[8]
        prev_spam = row[9] or 0
        prev_date = row[10]

        has_change = False
        change_type = 'stable'
        messages = []

        # Check domain reputation change
        if prev_domain_rep and recent_domain_rep != prev_domain_rep:
            has_change = True
            if recent_domain_val < prev_domain_val:
                change_type = 'degraded'
                messages.append(f'Domain reputation dropped from {prev_domain_rep} to {recent_domain_rep}')
            else:
                if change_type != 'degraded':
                    change_type = 'improved'
                messages.append(f'Domain reputation improved from {prev_domain_rep} to {recent_domain_rep}')

        # Check IP reputation change (compare JSON strings)
        if prev_ip_rep and recent_ip_rep and prev_ip_rep != recent_ip_rep:
            has_change = True
            messages.append(f'IP reputation changed (check details)')
            if change_type == 'stable':
                change_type = 'ip_changed'

        # Check spam rate increase
        if prev_spam > 0 and recent_spam > prev_spam * 1.5:
            has_change = True
            messages.append(f'Spam rate increased from {prev_spam:.1f}% to {recent_spam:.1f}%')
            if change_type == 'stable':
                change_type = 'spam_increase'

        if has_change:
            # Combine all messages into one
            message = ' | '.join(messages)

            changes.append({
                'domain': domain,
                'change_type': change_type,
                'message': message,
                'messages': messages,
                'recent_date': recent_date,
                'prev_date': prev_date,
                'recent_domain_reputation': recent_domain_rep,
                'prev_domain_reputation': prev_domain_rep,
                'recent_ip_reputation': recent_ip_rep,
                'prev_ip_reputation': prev_ip_rep,
                'recent_spam_rate': round(recent_spam, 2),
                'prev_spam_rate': round(prev_spam, 2)
            })

    return changes


def get_domain_detailed_metrics(domain: str, days: int = 30) -> Dict:
    """
    Get detailed metrics for a specific domain over time period
    Used for detailed domain view

    Note: Gets all available data for the domain (up to 365 days stored)
    The 'days' parameter is provided for future use but currently gets all data
    """
    conn = sqlite3.connect(GPT_DB_PATH)
    cursor = conn.cursor()

    # Get all available data for this domain (max 365 days stored anyway)
    cursor.execute('''
        SELECT
            data_date,
            reputation as domain_reputation,
            reputation_value as domain_reputation_value,
            ip_reputation,
            spam_rate,
            user_reported_spam_rate,
            spf_success_rate,
            dkim_success_rate,
            dmarc_success_rate,
            tls_rate,
            delivery_errors
        FROM gpt_data
        WHERE domain = ?
        ORDER BY data_date DESC
        LIMIT 365
    ''', (domain,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return None

    # Latest data
    latest = rows[0]

    # Parse latest IP reputation JSON
    latest_ip_rep_json = latest[3]
    latest_ip_rep_breakdown = {}
    latest_ip_rep_samples = {}
    if latest_ip_rep_json:
        try:
            ip_data = json.loads(latest_ip_rep_json)
            latest_ip_rep_breakdown = ip_data.get('breakdown', {})
            latest_ip_rep_samples = ip_data.get('samples', {})
        except (json.JSONDecodeError, TypeError):
            # Old format (single string)
            if latest_ip_rep_json and latest_ip_rep_json != 'N/A':
                latest_ip_rep_breakdown = {latest_ip_rep_json: 1}

    # Calculate trends
    domain_rep_trend = []
    ip_rep_trend = []
    ip_rep_breakdown_trend = {'HIGH': [], 'MEDIUM': [], 'LOW': [], 'BAD': []}
    spam_trend = []
    auth_trends = {'spf': [], 'dkim': [], 'dmarc': []}
    dates = []

    for row in reversed(rows):
        dates.append(row[0])
        domain_rep_trend.append(row[2] or 0)

        # Parse IP reputation JSON for trend
        ip_rep_json = row[3]
        ip_rep_value = 0

        # Initialize counts for this date
        high_count = 0
        medium_count = 0
        low_count = 0
        bad_count = 0

        if ip_rep_json:
            try:
                ip_data = json.loads(ip_rep_json)
                breakdown = ip_data.get('breakdown', {})

                # Extract counts for each reputation type
                high_count = breakdown.get('HIGH', 0)
                medium_count = breakdown.get('MEDIUM', 0)
                low_count = breakdown.get('LOW', 0)
                bad_count = breakdown.get('BAD', 0)

                # For trending, use highest reputation present (backward compatibility)
                if 'HIGH' in breakdown:
                    ip_rep_value = 4
                elif 'MEDIUM' in breakdown:
                    ip_rep_value = 3
                elif 'LOW' in breakdown:
                    ip_rep_value = 2
                elif 'BAD' in breakdown:
                    ip_rep_value = 1
            except (json.JSONDecodeError, TypeError):
                # Old format fallback
                ip_rep = ip_rep_json if ip_rep_json else 'UNKNOWN'
                ip_rep_map = {'HIGH': 4, 'MEDIUM': 3, 'LOW': 2, 'BAD': 1, 'UNKNOWN': 0, 'N/A': 0}
                ip_rep_value = ip_rep_map.get(ip_rep, 0)

                # Set count to 1 for old format
                if ip_rep == 'HIGH':
                    high_count = 1
                elif ip_rep == 'MEDIUM':
                    medium_count = 1
                elif ip_rep == 'LOW':
                    low_count = 1
                elif ip_rep == 'BAD':
                    bad_count = 1

        # Append breakdown counts
        ip_rep_breakdown_trend['HIGH'].append(high_count)
        ip_rep_breakdown_trend['MEDIUM'].append(medium_count)
        ip_rep_breakdown_trend['LOW'].append(low_count)
        ip_rep_breakdown_trend['BAD'].append(bad_count)

        ip_rep_trend.append(ip_rep_value)
        spam_trend.append(row[4] or 0)
        auth_trends['spf'].append(row[6] or 0)
        auth_trends['dkim'].append(row[7] or 0)
        auth_trends['dmarc'].append(row[8] or 0)

    return {
        'domain': domain,
        'latest': {
            'date': latest[0],
            'domain_reputation': latest[1],
            'domain_reputation_value': latest[2],
            'ip_reputation': latest_ip_rep_breakdown,
            'ip_reputation_samples': latest_ip_rep_samples,
            'spam_rate': round(latest[4] or 0, 2),
            'user_spam_rate': round(latest[5] or 0, 2),
            'spf_rate': round(latest[6] or 0, 2),
            'dkim_rate': round(latest[7] or 0, 2),
            'dmarc_rate': round(latest[8] or 0, 2),
            'tls_rate': round(latest[9] or 0, 2),
            'delivery_errors': json.loads(latest[10]) if latest[10] else []
        },
        'trends': {
            'dates': dates,
            'domain_reputation': domain_rep_trend,
            'ip_reputation': ip_rep_trend,
            'ip_reputation_breakdown': ip_rep_breakdown_trend,
            'spam_rate': spam_trend,
            'spf_rate': auth_trends['spf'],
            'dkim_rate': auth_trends['dkim'],
            'dmarc_rate': auth_trends['dmarc']
        }
    }
