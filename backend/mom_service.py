"""
MoM (Month-over-Month) Service
Calculates send volume changes by comparing current period with previous period
"""
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, Optional


DB_PATH = '/Users/pankaj/pani/data/mbr_reports.db'


def get_previous_month_range(from_date: str, to_date: str) -> tuple:
    """
    Calculate the previous month's date range based on current range

    Args:
        from_date: Current period start (YYYY-MM-DD)
        to_date: Current period end (YYYY-MM-DD)

    Returns:
        (prev_from_date, prev_to_date) or (None, None) if can't calculate
    """
    try:
        current_from = datetime.strptime(from_date, '%Y-%m-%d')
        current_to = datetime.strptime(to_date, '%Y-%m-%d')

        # Calculate duration
        duration = (current_to - current_from).days + 1

        # Go back by one month
        prev_to = current_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=duration - 1)

        return (prev_from.strftime('%Y-%m-%d'), prev_to.strftime('%Y-%m-%d'))
    except Exception as e:
        print(f'Error calculating previous month range: {e}')
        return (None, None)


def get_latest_report_for_period(from_date: str, to_date: str, report_type: str = 'domain') -> Optional[Dict]:
    """
    Get the most recent report for a given date range

    Args:
        from_date: Period start (YYYY-MM-DD)
        to_date: Period end (YYYY-MM-DD)
        report_type: 'domain' or 'account'

    Returns:
        Report data dict or None if not found
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT report_data FROM mbr_reports
            WHERE from_date = ? AND to_date = ? AND report_type = ?
            ORDER BY created_at DESC LIMIT 1
        ''', (from_date, to_date, report_type))

        row = cursor.fetchone()
        conn.close()

        if row:
            return json.loads(row['report_data'])
        return None
    except Exception as e:
        print(f'Error fetching report: {e}')
        return None


def build_domain_send_map(report_data: Dict) -> Dict[str, int]:
    """
    Build a mapping of domain -> sent volume from report data

    Args:
        report_data: Full report data with esp_data structure

    Returns:
        Dict mapping domain name to sent volume
    """
    domain_map = {}

    if not report_data or 'esp_data' not in report_data:
        return domain_map

    # Iterate through all ESPs and their domain data
    for esp_name, esp_data in report_data.get('esp_data', {}).items():
        all_data = esp_data.get('all_data', [])

        for domain_row in all_data:
            domain = domain_row.get('From_domain')
            sent = domain_row.get('Sent', 0)

            if domain:
                # Sum across ESPs if domain appears in multiple
                domain_map[domain] = domain_map.get(domain, 0) + sent

    return domain_map


def build_account_send_map(report_data: Dict) -> Dict[str, int]:
    """
    Build a mapping of account -> sent volume from report data

    Args:
        report_data: Full report data with account_data structure

    Returns:
        Dict mapping account name to sent volume
    """
    account_map = {}

    if not report_data or 'esp_data' not in report_data:
        return account_map

    # For account view, we need to aggregate by account across all ESPs
    for esp_name, esp_data in report_data.get('esp_data', {}).items():
        # Check for top10_accounts data
        top_accounts = esp_data.get('top10_accounts', [])

        for account_row in top_accounts:
            account = account_row.get('Account')
            sent = account_row.get('Sent', 0)

            if account:
                account_map[account] = account_map.get(account, 0) + sent

    # Also check overall top accounts if present
    top_accounts_overall = report_data.get('top10_accounts_overall', [])
    for account_row in top_accounts_overall:
        account = account_row.get('Account')
        sent = account_row.get('Sent', 0)

        if account and account not in account_map:
            account_map[account] = sent

    return account_map


def calculate_mom_change(current_sent: int, previous_sent: int) -> Optional[float]:
    """
    Calculate MoM percentage change

    Args:
        current_sent: Current period send volume
        previous_sent: Previous period send volume

    Returns:
        Percentage change (e.g., 15.5 for 15.5% increase) or None if can't calculate
    """
    if previous_sent == 0:
        return None

    change = ((current_sent - previous_sent) / previous_sent) * 100
    return round(change, 2)


def add_mom_to_domain_data(current_data: Dict, from_date: str, to_date: str) -> Dict:
    """
    Add MoM Send change to domain-level report data

    Args:
        current_data: Current report data
        from_date: Current period start
        to_date: Current period end

    Returns:
        Updated report data with mom_send_change added to each domain
    """
    # Get previous month's date range
    prev_from, prev_to = get_previous_month_range(from_date, to_date)

    if not prev_from or not prev_to:
        print('Could not calculate previous month range')
        return current_data

    # Fetch previous month's report
    prev_report = get_latest_report_for_period(prev_from, prev_to, report_type='domain')

    if not prev_report:
        print(f'No previous report found for {prev_from} to {prev_to}')
        # Add None values to indicate no comparison data
        if 'esp_data' in current_data:
            for esp_name, esp_data in current_data['esp_data'].items():
                # Set MoM to None for ESP summaries
                if 'us_summary' in esp_data and esp_data['us_summary']:
                    esp_data['us_summary']['MoM_Send_Change'] = None
                if 'eu_summary' in esp_data and esp_data['eu_summary']:
                    esp_data['eu_summary']['MoM_Send_Change'] = None
                if 'combined_summary' in esp_data and esp_data['combined_summary']:
                    esp_data['combined_summary']['MoM_Send_Change'] = None

                # Set MoM to None for domains
                if 'all_data' in esp_data:
                    for domain_row in esp_data['all_data']:
                        domain_row['MoM_Send_Change_%'] = None

                if 'top10_domains' in esp_data:
                    for domain_row in esp_data['top10_domains']:
                        domain_row['MoM_Send_Change_%'] = None

        if 'top10_overall' in current_data:
            for domain_row in current_data['top10_overall']:
                domain_row['MoM_Send_Change_%'] = None

        return current_data

    # Build domain -> sent mapping from previous report
    prev_domain_map = build_domain_send_map(prev_report)

    print(f'Found previous report with {len(prev_domain_map)} domains')

    # Add MoM to ESP summaries and domains
    if 'esp_data' in current_data:
        for esp_name, esp_data in current_data['esp_data'].items():
            # Get previous ESP data
            prev_esp_data = prev_report.get('esp_data', {}).get(esp_name, {})

            # Add MoM to US summary
            if 'us_summary' in esp_data and esp_data['us_summary']:
                current_sent = esp_data['us_summary'].get('Total_Sent', 0)
                prev_sent = prev_esp_data.get('us_summary', {}).get('Total_Sent', 0) if prev_esp_data else 0
                esp_data['us_summary']['MoM_Send_Change'] = calculate_mom_change(current_sent, prev_sent)

            # Add MoM to EU summary
            if 'eu_summary' in esp_data and esp_data['eu_summary']:
                current_sent = esp_data['eu_summary'].get('Total_Sent', 0)
                prev_sent = prev_esp_data.get('eu_summary', {}).get('Total_Sent', 0) if prev_esp_data else 0
                esp_data['eu_summary']['MoM_Send_Change'] = calculate_mom_change(current_sent, prev_sent)

            # Add MoM to combined summary
            if 'combined_summary' in esp_data and esp_data['combined_summary']:
                current_sent = esp_data['combined_summary'].get('Total_Sent', 0)
                prev_sent = prev_esp_data.get('combined_summary', {}).get('Total_Sent', 0) if prev_esp_data else 0
                esp_data['combined_summary']['MoM_Send_Change'] = calculate_mom_change(current_sent, prev_sent)

            # Update all_data
            if 'all_data' in esp_data:
                for domain_row in esp_data['all_data']:
                    domain = domain_row.get('From_domain')
                    current_sent = domain_row.get('Sent', 0)
                    prev_sent = prev_domain_map.get(domain, 0)

                    domain_row['MoM_Send_Change_%'] = calculate_mom_change(current_sent, prev_sent)

            # Update top10_domains
            if 'top10_domains' in esp_data:
                for domain_row in esp_data['top10_domains']:
                    domain = domain_row.get('From_domain')
                    current_sent = domain_row.get('Sent', 0)
                    prev_sent = prev_domain_map.get(domain, 0)

                    domain_row['MoM_Send_Change_%'] = calculate_mom_change(current_sent, prev_sent)

    # Update top10_overall
    if 'top10_overall' in current_data:
        for domain_row in current_data['top10_overall']:
            domain = domain_row.get('From_domain')
            current_sent = domain_row.get('Sent', 0)
            prev_sent = prev_domain_map.get(domain, 0)

            domain_row['MoM_Send_Change_%'] = calculate_mom_change(current_sent, prev_sent)

    return current_data


def add_mom_to_account_data(current_data: Dict, from_date: str, to_date: str) -> Dict:
    """
    Add MoM Send change to account-level report data

    Args:
        current_data: Current report data
        from_date: Current period start
        to_date: Current period end

    Returns:
        Updated report data with mom_send_change added to each account
    """
    # Get previous month's date range
    prev_from, prev_to = get_previous_month_range(from_date, to_date)

    if not prev_from or not prev_to:
        print('Could not calculate previous month range')
        return current_data

    # Fetch previous month's report
    prev_report = get_latest_report_for_period(prev_from, prev_to, report_type='account')

    if not prev_report:
        print(f'No previous account report found for {prev_from} to {prev_to}')
        # Add None values to indicate no comparison data
        if 'esp_data' in current_data:
            for esp_name, esp_data in current_data['esp_data'].items():
                if 'top10_accounts' in esp_data:
                    for account_row in esp_data['top10_accounts']:
                        account_row['MoM_Send_Change_%'] = None

        if 'top10_accounts_overall' in current_data:
            for account_row in current_data['top10_accounts_overall']:
                account_row['MoM_Send_Change_%'] = None

        return current_data

    # Build account -> sent mapping from previous report
    prev_account_map = build_account_send_map(prev_report)

    print(f'Found previous account report with {len(prev_account_map)} accounts')

    # Add MoM to each account in current report
    if 'esp_data' in current_data:
        for esp_name, esp_data in current_data['esp_data'].items():
            if 'top10_accounts' in esp_data:
                for account_row in esp_data['top10_accounts']:
                    account = account_row.get('Account')
                    current_sent = account_row.get('Sent', 0)
                    prev_sent = prev_account_map.get(account, 0)

                    account_row['MoM_Send_Change_%'] = calculate_mom_change(current_sent, prev_sent)

    # Update top10_accounts_overall
    if 'top10_accounts_overall' in current_data:
        for account_row in current_data['top10_accounts_overall']:
            account = account_row.get('Account')
            current_sent = account_row.get('Sent', 0)
            prev_sent = prev_account_map.get(account, 0)

            account_row['MoM_Send_Change_%'] = calculate_mom_change(current_sent, prev_sent)

    return current_data
