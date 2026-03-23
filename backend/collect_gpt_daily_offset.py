#!/usr/bin/env python3
"""
Daily GPT collection for a single day offset (default: 3 days ago).
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gpt_service import (
    initialize_database,
    list_domains,
    get_traffic_stats,
    store_domain_data,
    cleanup_old_data
)

OFFSET_DAYS = 3


def main():
    print(f"\n{'='*60}")
    print(f"GPT Offset Daily Collection - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    initialize_database()
    domains = list_domains()

    if not domains:
        print("\n✗ No domains found. Please authorize first or check domain verification.")
        return 1

    target_date = (datetime.utcnow() - timedelta(days=OFFSET_DAYS)).date()
    start_date_str = target_date.strftime('%Y-%m-%d')
    end_date_str = (target_date + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"Collecting data for date: {start_date_str} (UTC, offset {OFFSET_DAYS} days)")

    total_records = 0
    domains_collected = 0

    for domain in domains:
        print(f"Fetching data for {domain}...")
        stats = get_traffic_stats(domain, start_date_str, end_date_str)

        if stats:
            stored = store_domain_data(domain, stats)
            total_records += stored
            domains_collected += 1
            print(f"  Stored {stored} records for {domain}")
        else:
            print(f"  No data available for {domain}")

    deleted = cleanup_old_data(365)
    print(f"Cleaned up {deleted} old records (older than 365 days)")

    print(f"\n✓ Collection completed")
    print(f"  Domains processed: {domains_collected}/{len(domains)}")
    print(f"  Total records stored: {total_records}")
    print(f"{'='*60}\n")

    return 0


if __name__ == '__main__':
    sys.exit(main())
