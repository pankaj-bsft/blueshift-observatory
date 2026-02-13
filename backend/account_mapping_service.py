"""
Account Mapping Service
Manages domain-to-account mappings with SQLite backend
"""
import sqlite3
import csv
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple


DB_PATH = '/Users/pankaj/pani/data/account_mappings.db'
CSV_PATH = '/Users/pankaj/pani/data/domain_account_mapping.csv'


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_account_mapping_database():
    """Initialize account mapping database"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS domain_account_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sending_domain TEXT UNIQUE NOT NULL,
            account_name TEXT NOT NULL,
            notes TEXT,
            is_affiliate INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create index for faster lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_sending_domain
        ON domain_account_mapping(sending_domain)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_account_name
        ON domain_account_mapping(account_name)
    ''')

    # Migration: Add is_affiliate column if it doesn't exist
    try:
        cursor.execute("SELECT is_affiliate FROM domain_account_mapping LIMIT 1")
    except sqlite3.OperationalError:
        print("Adding is_affiliate column to existing table...")
        cursor.execute("ALTER TABLE domain_account_mapping ADD COLUMN is_affiliate INTEGER DEFAULT 0")
        print("is_affiliate column added successfully")

    conn.commit()
    conn.close()
    print(f"Account mapping database initialized at {DB_PATH}")


def import_csv_to_database(csv_path: str = CSV_PATH) -> Tuple[int, int]:
    """
    Import CSV file into database
    Returns: (rows_imported, rows_skipped)
    """
    if not os.path.exists(csv_path):
        print(f"CSV file not found at {csv_path}")
        return 0, 0

    conn = get_db_connection()
    cursor = conn.cursor()

    imported = 0
    skipped = 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            sending_domain = row.get('sending_domain', '').strip().lower()
            account_name = row.get('account_name', '').strip()

            # Handle is_affiliate field (optional in CSV)
            is_affiliate_str = row.get('is_affiliate', 'No').strip()
            is_affiliate = 1 if is_affiliate_str.lower() in ['yes', 'true', '1'] else 0

            if not sending_domain or not account_name:
                skipped += 1
                continue

            try:
                cursor.execute('''
                    INSERT INTO domain_account_mapping (sending_domain, account_name, is_affiliate)
                    VALUES (?, ?, ?)
                    ON CONFLICT(sending_domain) DO UPDATE SET
                        account_name = excluded.account_name,
                        is_affiliate = excluded.is_affiliate,
                        updated_at = CURRENT_TIMESTAMP
                ''', (sending_domain, account_name, is_affiliate))
                imported += 1
            except Exception as e:
                print(f"Error importing {sending_domain}: {e}")
                skipped += 1

    conn.commit()
    conn.close()

    print(f"CSV import complete: {imported} imported, {skipped} skipped")
    return imported, skipped


def export_database_to_csv(csv_path: str = CSV_PATH) -> int:
    """
    Export database to CSV file
    Returns: number of rows exported
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT sending_domain, account_name, is_affiliate
        FROM domain_account_mapping
        ORDER BY account_name, sending_domain
    ''')

    rows = cursor.fetchall()
    conn.close()

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['sending_domain', 'account_name', 'is_affiliate'])

        for row in rows:
            is_affiliate_val = 'Yes' if row['is_affiliate'] == 1 else 'No'
            writer.writerow([row['sending_domain'], row['account_name'], is_affiliate_val])

    print(f"Exported {len(rows)} rows to {csv_path}")
    return len(rows)


def get_all_mappings(search: str = '', limit: int = 1000, offset: int = 0) -> Dict:
    """
    Get all mappings with optional search
    Returns: dict with mappings and total count
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Build search query
    if search:
        search_pattern = f"%{search}%"
        count_query = '''
            SELECT COUNT(*) as count FROM domain_account_mapping
            WHERE sending_domain LIKE ? OR account_name LIKE ?
        '''
        data_query = '''
            SELECT * FROM domain_account_mapping
            WHERE sending_domain LIKE ? OR account_name LIKE ?
            ORDER BY account_name, sending_domain
            LIMIT ? OFFSET ?
        '''
        cursor.execute(count_query, (search_pattern, search_pattern))
        total = cursor.fetchone()['count']

        cursor.execute(data_query, (search_pattern, search_pattern, limit, offset))
    else:
        cursor.execute('SELECT COUNT(*) as count FROM domain_account_mapping')
        total = cursor.fetchone()['count']

        cursor.execute('''
            SELECT * FROM domain_account_mapping
            ORDER BY account_name, sending_domain
            LIMIT ? OFFSET ?
        ''', (limit, offset))

    mappings = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        'mappings': mappings,
        'total': total,
        'limit': limit,
        'offset': offset
    }


def get_mapping_by_id(mapping_id: int) -> Optional[Dict]:
    """Get single mapping by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM domain_account_mapping WHERE id = ?', (mapping_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_account_for_domain(domain: str) -> Optional[str]:
    """Get account name for a given domain"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT account_name FROM domain_account_mapping WHERE sending_domain = ?',
        (domain.lower(),)
    )
    row = cursor.fetchone()
    conn.close()

    return row['account_name'] if row else None


def get_affiliate_accounts() -> List[str]:
    """Get list of all account names where is_affiliate = 1"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT DISTINCT account_name
        FROM domain_account_mapping
        WHERE is_affiliate = 1
        ORDER BY account_name
    ''')

    accounts = [row['account_name'] for row in cursor.fetchall()]
    conn.close()

    return accounts


def get_domains_for_account(account_name: str) -> List[str]:
    """Get all domains for a given account"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT sending_domain FROM domain_account_mapping WHERE account_name = ?',
        (account_name,)
    )
    rows = cursor.fetchall()
    conn.close()

    return [row['sending_domain'] for row in rows]


def create_mapping(sending_domain: str, account_name: str, notes: str = '', is_affiliate: bool = False) -> Dict:
    """Create new domain-account mapping"""
    conn = get_db_connection()
    cursor = conn.cursor()

    sending_domain = sending_domain.strip().lower()
    account_name = account_name.strip()
    is_affiliate_int = 1 if is_affiliate else 0

    try:
        cursor.execute('''
            INSERT INTO domain_account_mapping (sending_domain, account_name, notes, is_affiliate)
            VALUES (?, ?, ?, ?)
        ''', (sending_domain, account_name, notes, is_affiliate_int))

        mapping_id = cursor.lastrowid
        conn.commit()

        # Fetch the created mapping
        cursor.execute('SELECT * FROM domain_account_mapping WHERE id = ?', (mapping_id,))
        result = dict(cursor.fetchone())
        conn.close()

        return result
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Domain '{sending_domain}' already exists")


def update_mapping(mapping_id: int, sending_domain: str = None,
                   account_name: str = None, notes: str = None, is_affiliate: bool = None) -> Dict:
    """Update existing mapping"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get existing mapping
    cursor.execute('SELECT * FROM domain_account_mapping WHERE id = ?', (mapping_id,))
    existing = cursor.fetchone()

    if not existing:
        conn.close()
        raise ValueError(f"Mapping with id {mapping_id} not found")

    # Prepare update values
    new_domain = sending_domain.strip().lower() if sending_domain else existing['sending_domain']
    new_account = account_name.strip() if account_name else existing['account_name']
    new_notes = notes if notes is not None else existing['notes']
    new_is_affiliate = (1 if is_affiliate else 0) if is_affiliate is not None else existing['is_affiliate']

    try:
        cursor.execute('''
            UPDATE domain_account_mapping
            SET sending_domain = ?, account_name = ?, notes = ?, is_affiliate = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_domain, new_account, new_notes, new_is_affiliate, mapping_id))

        conn.commit()

        # Fetch updated mapping
        cursor.execute('SELECT * FROM domain_account_mapping WHERE id = ?', (mapping_id,))
        result = dict(cursor.fetchone())
        conn.close()

        return result
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Domain '{new_domain}' already exists")


def delete_mapping(mapping_id: int) -> bool:
    """Delete mapping by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM domain_account_mapping WHERE id = ?', (mapping_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def bulk_delete_mappings(mapping_ids: List[int]) -> int:
    """Delete multiple mappings"""
    conn = get_db_connection()
    cursor = conn.cursor()

    placeholders = ','.join('?' * len(mapping_ids))
    cursor.execute(f'DELETE FROM domain_account_mapping WHERE id IN ({placeholders})', mapping_ids)
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted


def get_account_statistics() -> Dict:
    """Get statistics about mappings"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as total_mappings FROM domain_account_mapping')
    total_mappings = cursor.fetchone()['total_mappings']

    cursor.execute('SELECT COUNT(DISTINCT account_name) as total_accounts FROM domain_account_mapping')
    total_accounts = cursor.fetchone()['total_accounts']

    cursor.execute('''
        SELECT account_name, COUNT(*) as domain_count
        FROM domain_account_mapping
        GROUP BY account_name
        ORDER BY domain_count DESC
        LIMIT 10
    ''')
    top_accounts = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return {
        'total_mappings': total_mappings,
        'total_accounts': total_accounts,
        'top_accounts': top_accounts
    }


# Initialize database on module import
if __name__ != '__main__':
    try:
        init_account_mapping_database()

        # Auto-import CSV if database is empty
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM domain_account_mapping')
        count = cursor.fetchone()['count']
        conn.close()

        if count == 0 and os.path.exists(CSV_PATH):
            print("Database is empty, importing CSV...")
            import_csv_to_database()
    except Exception as e:
        print(f"Error initializing account mapping service: {e}")
