#!/usr/bin/env python3
"""
Daily SNDS Data Collection Script
Automatically collects and stores SNDS data
"""
import sys
import os
from datetime import datetime

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the SNDS service
from snds_service import collect_and_store_snds_data

def main():
    """Run daily SNDS data collection"""
    print(f"\n{'='*60}")
    print(f"SNDS Daily Collection - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    try:
        result = collect_and_store_snds_data()

        print(f"\n✓ Collection completed successfully")
        print(f"  - IPs collected: {result.get('total_ips', 0)}")
        print(f"  - Date range: {result.get('start_date', 'N/A')} to {result.get('end_date', 'N/A')}")
        print(f"\n{'='*60}\n")

        return 0
    except Exception as e:
        print(f"\n✗ Collection failed: {str(e)}")
        print(f"\n{'='*60}\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
