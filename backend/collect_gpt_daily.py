#!/usr/bin/env python3
"""
Daily Google Postmaster Tools (GPT) Data Collection Script
Automatically collects and stores GPT data
"""
import sys
import os
from datetime import datetime

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the GPT service
from gpt_service import collect_and_store_gpt_data

def main():
    """Run daily GPT data collection"""
    print(f"\n{'='*60}")
    print(f"GPT Daily Collection - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    try:
        # Collect last 120 days (Google's API limit)
        result = collect_and_store_gpt_data(days_back=120)

        if result['status'] == 'success':
            print(f"\n✓ Collection completed successfully")
            print(f"  - Total domains: {result.get('total_domains', 0)}")
            print(f"  - Domains collected: {result.get('domains_collected', 0)}")
            print(f"  - Total records: {result.get('total_records', 0)}")
            print(f"  - Date range: {result.get('start_date', 'N/A')} to {result.get('end_date', 'N/A')}")
            print(f"  - Deleted old records: {result.get('deleted_old_records', 0)}")
            print(f"\n{'='*60}\n")
            return 0
        else:
            print(f"\n✗ Collection failed: {result.get('message', 'Unknown error')}")
            print(f"\n{'='*60}\n")
            return 1

    except Exception as e:
        print(f"\n✗ Collection failed: {str(e)}")
        print(f"\n{'='*60}\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
