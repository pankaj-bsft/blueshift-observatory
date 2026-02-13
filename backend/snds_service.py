"""
Microsoft SNDS (Smart Network Data Services) Integration
Fetches IP reputation, spam rates, and traffic data from Microsoft
"""
import requests
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET

# SNDS API Configuration
SNDS_DATA_URL = "https://sendersupport.olc.protection.outlook.com/snds/data/?key=bc7c2e2e-23ba-4689-a338-c23c18590abd"
SNDS_IP_STATUS_URL = "https://sendersupport.olc.protection.outlook.com/snds/ipStatus/?key=bc7c2e2e-23ba-4689-a338-c23c18590abd"

# Database path
SNDS_DB_PATH = '/Users/pankaj/pani/data/snds_data.db'


def init_snds_database():
    """Initialize SNDS database with required tables"""
    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    # Main SNDS data table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS snds_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            account_name TEXT,
            data_date DATE NOT NULL,

            -- Traffic Metrics
            message_volume INTEGER DEFAULT 0,
            spam_rate REAL DEFAULT 0,
            complaint_rate REAL DEFAULT 0,
            trap_hits INTEGER DEFAULT 0,

            -- Reputation Metrics
            filter_result TEXT,
            activity_end TEXT,
            comments TEXT,

            -- Metadata
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(ip_address, data_date)
        )
    ''')

    # IP to Account mapping table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS snds_ip_mapping (
            ip_address TEXT PRIMARY KEY,
            account_name TEXT,
            esp TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create indexes for performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_snds_ip_date ON snds_data(ip_address, data_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_snds_account_date ON snds_data(account_name, data_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_snds_date ON snds_data(data_date)')

    conn.commit()
    conn.close()
    print(f'SNDS database initialized at {SNDS_DB_PATH}')


def fetch_snds_data() -> Dict:
    """
    Fetch data from SNDS Data API
    Returns raw data and parsed results
    """
    try:
        print('Fetching SNDS data...')
        response = requests.get(SNDS_DATA_URL, timeout=30)

        if response.status_code == 200:
            # SNDS returns CSV data
            content = response.text
            print(f'SNDS Data Response (first 500 chars):\n{content[:500]}')

            # Parse CSV data
            parsed_data = parse_snds_csv(content)

            return {
                'status': 'success',
                'raw_data': content,
                'parsed_data': parsed_data,
                'total_ips': len(parsed_data),
                'fetched_at': datetime.utcnow().isoformat()
            }
        else:
            print(f'SNDS API error: {response.status_code}')
            return {
                'status': 'error',
                'error': f'HTTP {response.status_code}',
                'message': response.text[:200]
            }

    except Exception as e:
        print(f'Error fetching SNDS data: {e}')
        return {
            'status': 'error',
            'error': str(e)
        }


def fetch_snds_ip_status() -> Dict:
    """
    Fetch IP status from SNDS ipStatus API
    Returns list of all registered IPs with their status
    """
    try:
        print('Fetching SNDS IP status...')
        response = requests.get(SNDS_IP_STATUS_URL, timeout=30)

        if response.status_code == 200:
            content = response.text
            print(f'SNDS IP Status Response (first 500 chars):\n{content[:500]}')

            # Parse XML data
            parsed_status = parse_snds_ip_status(content)

            return {
                'status': 'success',
                'raw_data': content,
                'parsed_data': parsed_status,
                'total_ips': len(parsed_status),
                'fetched_at': datetime.utcnow().isoformat()
            }
        else:
            print(f'SNDS IP Status API error: {response.status_code}')
            return {
                'status': 'error',
                'error': f'HTTP {response.status_code}',
                'message': response.text[:200]
            }

    except Exception as e:
        print(f'Error fetching SNDS IP status: {e}')
        return {
            'status': 'error',
            'error': str(e)
        }


def parse_snds_csv(csv_content: str) -> List[Dict]:
    """
    Parse SNDS CSV data format
    Format: IP,StartDate,EndDate,TotalMsg,FilteredMsg,DeliveredMsg,FilterResult,SpamRate,ActivityStart,ActivityEnd,TrapHits,Unknown,Comments
    Example: 149.72.205.104,2/10/2026 4:00 PM,2/11/2026 3:00 PM,285449,265903,265902,GREEN,< 0.1%,,,0,,Abuse reported for: deals@d.slickdeals.net
    """
    parsed = []
    lines = csv_content.strip().split('\n')

    if not lines:
        return parsed

    # Process all lines (no header)
    for line in lines:
        if not line.strip():
            continue

        # Split by comma
        parts = [p.strip() for p in line.split(',')]

        if len(parts) < 7:  # Minimum required fields
            continue

        try:
            # Extract IP address
            ip_address = parts[0]

            # Extract dates - use end date as the data_date
            end_date_str = parts[2] if len(parts) > 2 else None

            # Parse date (format: 2/11/2026 3:00 PM)
            data_date = None
            if end_date_str:
                try:
                    date_part = end_date_str.split()[0]  # Get "2/11/2026"
                    month, day, year = date_part.split('/')
                    data_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                except:
                    data_date = datetime.utcnow().date().isoformat()

            # Message volumes
            total_messages = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            filtered_messages = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            delivered_messages = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0

            # Filter result (GREEN/YELLOW/RED)
            filter_result = parts[6] if len(parts) > 6 else 'UNKNOWN'

            # Spam rate (< 0.1%, 0.1%, etc.)
            spam_rate_str = parts[7] if len(parts) > 7 else '0%'
            # Convert to float (< 0.1% -> 0.1, 0.4% -> 0.4)
            spam_rate = 0.0
            if spam_rate_str:
                spam_rate_str = spam_rate_str.replace('<', '').replace('%', '').strip()
                try:
                    spam_rate = float(spam_rate_str)
                except:
                    spam_rate = 0.0

            # Trap hits
            trap_hits = 0
            if len(parts) > 10 and parts[10].isdigit():
                trap_hits = int(parts[10])

            # Comments
            comments = parts[12] if len(parts) > 12 else ''

            # Calculate complaint rate (based on filtered vs delivered)
            complaint_rate = 0.0
            if delivered_messages > 0:
                complaint_rate = ((total_messages - delivered_messages) / total_messages) * 100

            record = {
                'ip_address': ip_address,
                'date': data_date,
                'message_volume': delivered_messages,
                'total_messages': total_messages,
                'filtered_messages': filtered_messages,
                'spam_rate': spam_rate,
                'complaint_rate': complaint_rate,
                'trap_hits': trap_hits,
                'filter_result': filter_result,
                'activity_end': parts[9] if len(parts) > 9 else None,
                'comments': comments
            }
            parsed.append(record)

        except Exception as e:
            print(f'Error parsing line (first 100 chars): {line[:100]} - {e}')
            continue

    print(f'Parsed {len(parsed)} SNDS data records')
    return parsed


def parse_snds_ip_status(xml_content: str) -> List[Dict]:
    """
    Parse SNDS IP Status XML format
    """
    parsed = []

    try:
        root = ET.fromstring(xml_content)

        # Parse XML structure (adjust based on actual response)
        for ip_elem in root.findall('.//ip'):
            record = {
                'ip_address': ip_elem.get('address') or ip_elem.text,
                'status': ip_elem.get('status'),
                'last_updated': ip_elem.get('last_updated')
            }
            parsed.append(record)

        print(f'Parsed {len(parsed)} IP status records')

    except ET.ParseError as e:
        print(f'Error parsing XML: {e}')
        # Try CSV format as fallback
        lines = xml_content.strip().split('\n')
        for line in lines:
            if line.strip():
                parsed.append({'raw': line})

    return parsed


def store_snds_data(data_records: List[Dict]) -> int:
    """
    Store SNDS data records in database
    Returns number of records inserted/updated
    """
    if not data_records:
        return 0

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    inserted = 0

    for record in data_records:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO snds_data
                (ip_address, data_date, message_volume, spam_rate, complaint_rate,
                 trap_hits, filter_result, activity_end, comments, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.get('ip_address'),
                record.get('date'),
                record.get('message_volume', 0),
                record.get('spam_rate', 0.0),
                record.get('complaint_rate', 0.0),
                record.get('trap_hits', 0),
                record.get('filter_result', 'unknown'),
                record.get('activity_end'),
                record.get('comments'),
                datetime.utcnow()
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f'Error inserting record: {e}')
            continue

    conn.commit()
    conn.close()

    print(f'Stored {inserted} SNDS records in database')
    return inserted


def cleanup_old_data(days_to_keep: int = 365):
    """
    Delete SNDS data older than specified days
    Default: Keep 1 year of data
    """
    cutoff_date = (datetime.utcnow() - timedelta(days=days_to_keep)).date()

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('DELETE FROM snds_data WHERE data_date < ?', (cutoff_date,))
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    print(f'Cleaned up {deleted} old SNDS records (older than {cutoff_date})')
    return deleted


def map_ips_to_accounts():
    """
    Map SNDS IP addresses to account names using ESP integration data
    """
    from esp_integration_service import get_all_account_info

    # Get all account info (includes IPs)
    account_data = get_all_account_info(force_refresh=False)

    if account_data['status'] != 'success':
        print('Failed to fetch account info for IP mapping')
        return 0

    conn = sqlite3.connect(SNDS_DB_PATH)
    cursor = conn.cursor()

    # Get all unique IPs from SNDS data
    cursor.execute('SELECT DISTINCT ip_address FROM snds_data')
    snds_ips = [row[0] for row in cursor.fetchall()]

    mapped = 0

    # Map IPs to accounts
    for snds_ip in snds_ips:
        # Search for this IP in account info data
        for item in account_data['data']:
            ip_addresses = item.get('ip_addresses', '')

            if snds_ip in ip_addresses:
                account_name = item.get('account_name', 'Unmapped')
                esp = item.get('esp', 'Unknown')

                # Store mapping
                cursor.execute('''
                    INSERT OR REPLACE INTO snds_ip_mapping
                    (ip_address, account_name, esp, last_updated)
                    VALUES (?, ?, ?, ?)
                ''', (snds_ip, account_name, esp, datetime.utcnow()))

                mapped += 1
                break

    conn.commit()

    # Update account_name in snds_data table
    cursor.execute('''
        UPDATE snds_data
        SET account_name = (
            SELECT account_name
            FROM snds_ip_mapping
            WHERE snds_ip_mapping.ip_address = snds_data.ip_address
        )
        WHERE ip_address IN (SELECT ip_address FROM snds_ip_mapping)
    ''')

    updated = cursor.rowcount
    conn.commit()

    conn.close()

    print(f'Mapped {mapped} IPs to accounts, updated {updated} SNDS records')
    return mapped


def collect_and_store_snds_data():
    """
    Main function to collect SNDS data and store in database
    Call this daily to fetch latest data
    """
    print('=== Starting SNDS Data Collection ===')

    # Initialize database if needed
    init_snds_database()

    # Fetch data from SNDS
    data_result = fetch_snds_data()

    if data_result['status'] == 'success':
        # Store in database
        stored = store_snds_data(data_result['parsed_data'])

        # Map IPs to accounts
        mapped = map_ips_to_accounts()

        # Cleanup old data
        cleaned = cleanup_old_data(days_to_keep=365)

        return {
            'status': 'success',
            'records_stored': stored,
            'ips_mapped': mapped,
            'old_records_cleaned': cleaned,
            'total_ips': data_result['total_ips']
        }
    else:
        return data_result


if __name__ == '__main__':
    # Test the service
    result = collect_and_store_snds_data()
    print(f'\nCollection Result: {result}')
