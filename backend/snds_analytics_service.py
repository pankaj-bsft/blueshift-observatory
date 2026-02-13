"""
SNDS Analytics Service
Provides aggregations, trends, and analytics for SNDS data
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

SNDS_DB_PATH = '/Users/pankaj/pani/data/snds_data.db'


def get_time_period_dates(period: str) -> tuple:
    """
    Get start and end dates for a time period
    Returns (start_date, end_date)
    """
    end_date = datetime.utcnow().date()

    period_map = {
        'yesterday': timedelta(days=1),
        '7day': timedelta(days=7),
        '30day': timedelta(days=30),
        '60day': timedelta(days=60),
        '90day': timedelta(days=90),
        '120day': timedelta(days=120),
        '1year': timedelta(days=365)
    }

    delta = period_map.get(period, timedelta(days=30))
    start_date = end_date - delta

    return (start_date, end_date)


def calculate_reputation_score(spam_rate: float, trap_hits: int, filter_result: str) -> float:
    """
    Calculate reputation score (0-100)

    Algorithm:
    - Start at 100
    - Subtract based on spam rate (0-40 points)
    - Subtract based on trap hits (0-30 points)
    - Subtract based on filter result (0-30 points)
    """
    score = 100.0

    # Spam rate penalty (0-40 points)
    if spam_rate > 0:
        spam_penalty = min(spam_rate * 100, 40)  # Max 40 points
        score -= spam_penalty

    # Trap hits penalty (0-30 points)
    if trap_hits > 0:
        trap_penalty = min(trap_hits * 3, 30)  # Each trap hit = 3 points, max 30
        score -= trap_penalty

    # Filter result penalty
    if filter_result == 'RED':
        score -= 30
    elif filter_result == 'YELLOW':
        score -= 15

    return max(0, min(100, score))


def get_reputation_rating(score: float) -> str:
    """Get reputation rating based on score"""
    if score >= 85:
        return 'Excellent'
    elif score >= 70:
        return 'Good'
    elif score >= 50:
        return 'Fair'
    elif score >= 30:
        return 'Poor'
    else:
        return 'Critical'


def get_snds_overview(period: str = '30day') -> Dict:
    """
    Get overview statistics for SNDS data
    """
    start_date, end_date = get_time_period_dates(period)

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    # Total IPs monitored
    cursor.execute('''
        SELECT COUNT(DISTINCT ip_address)
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
    ''', (start_date, end_date))
    total_ips = cursor.fetchone()[0]

    # Total messages
    cursor.execute('''
        SELECT SUM(message_volume)
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
    ''', (start_date, end_date))
    total_messages = cursor.fetchone()[0] or 0

    # Average spam rate
    cursor.execute('''
        SELECT AVG(spam_rate)
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
    ''', (start_date, end_date))
    avg_spam_rate = cursor.fetchone()[0] or 0

    # Total trap hits
    cursor.execute('''
        SELECT SUM(trap_hits)
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
    ''', (start_date, end_date))
    total_trap_hits = cursor.fetchone()[0] or 0

    # Filter result distribution
    cursor.execute('''
        SELECT filter_result, COUNT(*)
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
        GROUP BY filter_result
    ''', (start_date, end_date))
    filter_distribution = {row[0]: row[1] for row in cursor.fetchall()}

    # Calculate average reputation
    cursor.execute('''
        SELECT ip_address, AVG(spam_rate), SUM(trap_hits), filter_result
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
        GROUP BY ip_address
    ''', (start_date, end_date))

    reputation_scores = []
    for row in cursor.fetchall():
        score = calculate_reputation_score(row[1], row[2], row[3])
        reputation_scores.append(score)

    avg_reputation = sum(reputation_scores) / len(reputation_scores) if reputation_scores else 0

    # Accounts with data
    cursor.execute('''
        SELECT COUNT(DISTINCT account_name)
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
        AND account_name IS NOT NULL
        AND account_name != ''
    ''', (start_date, end_date))
    total_accounts = cursor.fetchone()[0]

    conn.close()

    return {
        'period': period,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'total_ips': total_ips,
        'total_accounts': total_accounts,
        'total_messages': total_messages,
        'avg_spam_rate': round(avg_spam_rate, 2),
        'total_trap_hits': total_trap_hits,
        'avg_reputation_score': round(avg_reputation, 1),
        'avg_reputation_rating': get_reputation_rating(avg_reputation),
        'filter_distribution': filter_distribution
    }


def get_snds_data_by_period(period: str = '30day', view_by: str = 'ip', account_name: Optional[str] = None, ip_address: Optional[str] = None) -> List[Dict]:
    """
    Get SNDS data aggregated by time period

    Args:
        period: Time period (yesterday, 7day, 30day, etc.)
        view_by: 'ip' or 'account'
        account_name: Filter by specific account
        ip_address: Filter by specific IP
    """
    start_date, end_date = get_time_period_dates(period)

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    # Build query based on view_by
    if view_by == 'account':
        query = '''
            SELECT
                account_name,
                data_date,
                SUM(message_volume) as total_messages,
                AVG(spam_rate) as avg_spam_rate,
                SUM(trap_hits) as total_trap_hits,
                filter_result
            FROM snds_data
            WHERE data_date BETWEEN ? AND ?
        '''
        params = [start_date, end_date]

        if account_name:
            query += ' AND account_name = ?'
            params.append(account_name)

        query += '''
            GROUP BY account_name, data_date
            ORDER BY data_date DESC, total_messages DESC
        '''
    else:  # by IP
        query = '''
            SELECT
                ip_address,
                account_name,
                data_date,
                message_volume,
                spam_rate,
                trap_hits,
                filter_result,
                comments
            FROM snds_data
            WHERE data_date BETWEEN ? AND ?
        '''
        params = [start_date, end_date]

        if ip_address:
            query += ' AND ip_address = ?'
            params.append(ip_address)

        if account_name:
            query += ' AND account_name = ?'
            params.append(account_name)

        query += ' ORDER BY data_date DESC, message_volume DESC'

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        if view_by == 'account':
            reputation = calculate_reputation_score(row[3], row[4], row[5])
            results.append({
                'account_name': row[0] or 'Unmapped',
                'date': row[1],
                'message_volume': row[2],
                'spam_rate': round(row[3], 2),
                'trap_hits': row[4],
                'filter_result': row[5],
                'reputation_score': round(reputation, 1),
                'reputation_rating': get_reputation_rating(reputation)
            })
        else:
            reputation = calculate_reputation_score(row[4], row[5], row[6])
            results.append({
                'ip_address': row[0],
                'account_name': row[1] or 'Unmapped',
                'date': row[2],
                'message_volume': row[3],
                'spam_rate': round(row[4], 2),
                'trap_hits': row[5],
                'filter_result': row[6],
                'comments': row[7],
                'reputation_score': round(reputation, 1),
                'reputation_rating': get_reputation_rating(reputation)
            })

    conn.close()
    return results


def get_reputation_trends(period: str = '30day', group_by: str = 'ip') -> Dict:
    """
    Get reputation trends over time
    Returns data suitable for line charts
    """
    start_date, end_date = get_time_period_dates(period)

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    if group_by == 'account':
        cursor.execute('''
            SELECT
                account_name,
                data_date,
                AVG(spam_rate) as avg_spam_rate,
                SUM(trap_hits) as trap_hits,
                filter_result
            FROM snds_data
            WHERE data_date BETWEEN ? AND ?
            AND account_name IS NOT NULL
            AND account_name != ''
            GROUP BY account_name, data_date
            ORDER BY account_name, data_date
        ''', (start_date, end_date))
    else:
        cursor.execute('''
            SELECT
                ip_address,
                data_date,
                spam_rate,
                trap_hits,
                filter_result
            FROM snds_data
            WHERE data_date BETWEEN ? AND ?
            ORDER BY ip_address, data_date
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    # Group data by IP/Account
    trends = defaultdict(lambda: {'dates': [], 'scores': [], 'spam_rates': [], 'trap_hits': [], 'filter_results': []})

    for row in rows:
        entity = row[0]  # IP or account
        date = row[1]
        spam_rate = row[2]
        trap_hits = row[3]
        filter_result = row[4]

        score = calculate_reputation_score(spam_rate, trap_hits, filter_result)

        trends[entity]['dates'].append(date)
        trends[entity]['scores'].append(round(score, 1))
        trends[entity]['spam_rates'].append(round(spam_rate, 2))
        trends[entity]['trap_hits'].append(trap_hits)
        trends[entity]['filter_results'].append(filter_result)

    return {
        'period': period,
        'group_by': group_by,
        'trends': dict(trends)
    }


def get_traffic_trends(period: str = '30day', group_by: str = 'ip') -> Dict:
    """
    Get traffic volume trends over time
    """
    start_date, end_date = get_time_period_dates(period)

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    if group_by == 'account':
        cursor.execute('''
            SELECT
                account_name,
                data_date,
                SUM(message_volume) as volume
            FROM snds_data
            WHERE data_date BETWEEN ? AND ?
            AND account_name IS NOT NULL
            AND account_name != ''
            GROUP BY account_name, data_date
            ORDER BY account_name, data_date
        ''', (start_date, end_date))
    else:
        cursor.execute('''
            SELECT
                ip_address,
                data_date,
                message_volume
            FROM snds_data
            WHERE data_date BETWEEN ? AND ?
            ORDER BY ip_address, data_date
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    # Group data
    trends = defaultdict(lambda: {'dates': [], 'volumes': []})

    for row in rows:
        entity = row[0]
        date = row[1]
        volume = row[2]

        trends[entity]['dates'].append(date)
        trends[entity]['volumes'].append(volume)

    return {
        'period': period,
        'group_by': group_by,
        'trends': dict(trends)
    }


def get_top_performers(period: str = '30day', metric: str = 'reputation', limit: int = 10) -> List[Dict]:
    """
    Get top performing IPs/Accounts by metric

    Metrics: reputation, volume, spam_rate
    """
    start_date, end_date = get_time_period_dates(period)

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            ip_address,
            account_name,
            AVG(spam_rate) as avg_spam_rate,
            SUM(trap_hits) as total_trap_hits,
            filter_result,
            SUM(message_volume) as total_volume
        FROM snds_data
        WHERE data_date BETWEEN ? AND ?
        GROUP BY ip_address
    ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    # Calculate scores
    performers = []
    for row in rows:
        reputation = calculate_reputation_score(row[2], row[3], row[4])
        performers.append({
            'ip_address': row[0],
            'account_name': row[1] or 'Unmapped',
            'reputation_score': round(reputation, 1),
            'reputation_rating': get_reputation_rating(reputation),
            'spam_rate': round(row[2], 2),
            'trap_hits': row[3],
            'filter_result': row[4],
            'total_volume': row[5]
        })

    # Sort by metric
    if metric == 'reputation':
        performers.sort(key=lambda x: x['reputation_score'], reverse=True)
    elif metric == 'volume':
        performers.sort(key=lambda x: x['total_volume'], reverse=True)
    elif metric == 'spam_rate':
        performers.sort(key=lambda x: x['spam_rate'])

    return performers[:limit]


def get_problem_ips(period: str = '30day', threshold: float = 50.0) -> List[Dict]:
    """
    Get IPs with reputation below threshold
    """
    performers = get_top_performers(period, 'reputation', limit=1000)
    problems = [p for p in performers if p['reputation_score'] < threshold]

    return sorted(problems, key=lambda x: x['reputation_score'])


def get_accounts_list() -> List[str]:
    """Get list of all accounts with SNDS data"""
    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT DISTINCT account_name
        FROM snds_data
        WHERE account_name IS NOT NULL
        AND account_name != ''
        ORDER BY account_name
    ''')

    accounts = [row[0] for row in cursor.fetchall()]
    conn.close()

    return accounts


def get_ips_list() -> List[str]:
    """Get list of all IPs with SNDS data"""
    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT DISTINCT ip_address
        FROM snds_data
        ORDER BY ip_address
    ''')

    ips = [row[0] for row in cursor.fetchall()]
    conn.close()

    return ips


if __name__ == '__main__':
    # Test analytics
    print('=== SNDS Analytics Test ===\n')

    overview = get_snds_overview('30day')
    print('Overview (30 days):')
    print(f"  Total IPs: {overview['total_ips']}")
    print(f"  Total Accounts: {overview['total_accounts']}")
    print(f"  Total Messages: {overview['total_messages']:,}")
    print(f"  Avg Reputation: {overview['avg_reputation_score']} ({overview['avg_reputation_rating']})")
    print(f"  Avg Spam Rate: {overview['avg_spam_rate']}%")
    print(f"  Total Trap Hits: {overview['total_trap_hits']}")
    print(f"  Filter Distribution: {overview['filter_distribution']}")

    print('\nTop Performers:')
    top = get_top_performers('30day', 'reputation', 5)
    for i, p in enumerate(top, 1):
        print(f"  {i}. {p['ip_address']} ({p['account_name']}) - Score: {p['reputation_score']}")
