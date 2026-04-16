"""
Monthly Account MBR report collector
Runs on the 2nd of each month to save the previous full month's account report.
Use --months N to backfill previous N months.
"""
from datetime import datetime, date, timedelta
import sys
import argparse
import pandas as pd

from druid_service import fetch_region_data, DRUID_US_BROKER, DRUID_EU_BROKER
from account_aggregation_service import (
    add_account_column,
    get_top_accounts_by_esp,
    get_top_accounts_overall,
    get_affiliate_accounts_data
)
from mom_service import add_mom_to_account_data
from mbr_storage_service import save_mbr_report, check_report_exists


def _month_range_for_offset(offset_months: int) -> tuple[str, str]:
    """Return [from_date, to_date) for the month offset from current UTC month.
    offset_months=1 => previous full month.
    """
    today = datetime.utcnow().date()
    first_current = date(today.year, today.month, 1)
    # Move back offset_months months
    year = first_current.year
    month = first_current.month - offset_months
    while month <= 0:
        month += 12
        year -= 1
    first_target = date(year, month, 1)
    # to_date is first day of next month
    next_year = year
    next_month = month + 1
    if next_month == 13:
        next_month = 1
        next_year += 1
    first_next = date(next_year, next_month, 1)
    return first_target.strftime('%Y-%m-%d'), first_next.strftime('%Y-%m-%d')


def build_account_report(from_date: str, to_date: str) -> dict:
    df_us = fetch_region_data('US', DRUID_US_BROKER, from_date, to_date)
    df_eu = fetch_region_data('EU', DRUID_EU_BROKER, from_date, to_date)

    if df_us.empty and df_eu.empty:
        return {}

    df_combined = add_account_column(pd.concat([df_us, df_eu], ignore_index=True))

    top_accounts_by_esp = get_top_accounts_by_esp(df_combined, top_n=10)
    top_accounts_overall = get_top_accounts_overall(df_combined, top_n=10)
    affiliate_accounts = get_affiliate_accounts_data(df_combined)

    response_data = {
        'status': 'success',
        'date_range': {
            'from_date': from_date,
            'to_date': to_date,
            'duration_days': (datetime.strptime(to_date, '%Y-%m-%d') - datetime.strptime(from_date, '%Y-%m-%d')).days
        },
        'esp_data': {esp: {'top10_accounts': accounts} for esp, accounts in top_accounts_by_esp.items()},
        'top10_accounts_overall': top_accounts_overall,
        'affiliate_accounts': affiliate_accounts,
        'total_accounts': len(df_combined['Account'].unique()),
        'unmapped_domains': int((df_combined['Account'] == 'Unmapped').sum())
    }

    response_data = add_mom_to_account_data(response_data, from_date, to_date)
    response_data['top_accounts_by_esp'] = response_data['esp_data']
    return response_data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--months', type=int, default=1, help='Number of previous months to backfill (default 1)')
    args = parser.parse_args()

    months = max(args.months, 1)
    exit_code = 0

    for offset in range(1, months + 1):
        from_date, to_date = _month_range_for_offset(offset)
        existing = check_report_exists(from_date, to_date, report_type='account')
        if existing:
            print(f"[MBR Account Monthly] Report already exists for {from_date} to {to_date}. Skipping.")
            continue

        print(f"[MBR Account Monthly] Collecting account report for {from_date} to {to_date}...")
        report = build_account_report(from_date, to_date)
        if not report:
            print('[MBR Account Monthly] No data found. Make sure VPN/Druid connectivity is available.')
            exit_code = 1
            continue

        result = save_mbr_report(from_date, to_date, 'account', report, overwrite=False)
        print(f"[MBR Account Monthly] Saved report: {result}")

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
