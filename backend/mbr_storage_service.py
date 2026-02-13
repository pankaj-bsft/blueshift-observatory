"""
MBR Storage Service
Stores MBR reports (domain and account level) in SQLite for historical tracking
"""
import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple


DB_PATH = '/Users/pankaj/pani/data/mbr_reports.db'


def detect_month_year(from_date: str, to_date: str, duration_days: int) -> Tuple[Optional[int], Optional[int]]:
    """
    Detect if date range represents a full month (STRICT MODE)

    Rules:
    - Must start on 1st of month
    - Must end on 1st of next month
    - Duration must be 28-31 days

    Args:
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        duration_days: Number of days in range

    Returns:
        (month, year) or (None, None)

    Examples:
        2026-01-01 to 2026-02-01 → (1, 2026)  # January 2026
        2025-12-01 to 2026-01-01 → (12, 2025) # December 2025
        2026-01-15 to 2026-02-15 → (None, None) # Not starting on 1st
    """
    try:
        # Parse dates
        from_dt = datetime.strptime(from_date, '%Y-%m-%d')
        to_dt = datetime.strptime(to_date, '%Y-%m-%d')

        # Rule 1: Must start on 1st of month
        if from_dt.day != 1:
            return (None, None)

        # Rule 2: Must end on 1st of month
        if to_dt.day != 1:
            return (None, None)

        # Rule 3: Duration must be 28-31 days (typical month)
        if not (28 <= duration_days <= 31):
            return (None, None)

        # All rules passed - this is a full month
        month = from_dt.month
        year = from_dt.year

        return (month, year)

    except Exception as e:
        print(f'Error detecting month/year: {e}')
        return (None, None)


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_mbr_reports_database():
    """Initialize MBR reports database"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Table for storing complete MBR reports
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mbr_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            from_date TEXT NOT NULL,
            to_date TEXT NOT NULL,
            duration_days INTEGER,
            total_domains INTEGER,
            total_accounts INTEGER,
            report_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Index for faster lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_report_dates
        ON mbr_reports(from_date, to_date, report_type)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_created_at
        ON mbr_reports(created_at)
    ''')

    conn.commit()
    conn.close()
    print(f"MBR reports database initialized at {DB_PATH}")


def check_report_exists(from_date: str, to_date: str, report_type: str = 'domain') -> Optional[Dict]:
    """
    Check if a report already exists for the given date range and type
    Returns the existing report if found, None otherwise
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, created_at FROM mbr_reports
        WHERE from_date = ? AND to_date = ? AND report_type = ?
        ORDER BY created_at DESC LIMIT 1
    ''', (from_date, to_date, report_type))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'id': row['id'],
            'created_at': row['created_at']
        }
    return None


def save_mbr_report(from_date: str, to_date: str, report_type: str,
                    report_data: Dict, overwrite: bool = False) -> Dict:
    """
    Save MBR report to database

    Always creates a new timestamped snapshot. Multiple reports for the same
    date range are allowed - each represents a different point in time.

    Args:
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        report_type: 'domain' or 'account'
        report_data: The complete report data (will be JSON serialized)
        overwrite: Not used - kept for API compatibility

    Returns:
        Dict with status and report_id
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Extract summary info
    duration_days = report_data.get('date_range', {}).get('duration_days', 0)
    total_domains = report_data.get('total_domains', 0)
    total_accounts = report_data.get('total_accounts', 0)

    # Detect month and year (strict mode)
    month, year = detect_month_year(from_date, to_date, duration_days)

    # Serialize report data
    report_json = json.dumps(report_data)

    # Always insert new report (timestamped snapshot)
    cursor.execute('''
        INSERT INTO mbr_reports (
            report_type, from_date, to_date, duration_days,
            total_domains, total_accounts, report_data, month, year
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (report_type, from_date, to_date, duration_days,
          total_domains, total_accounts, report_json, month, year))

    report_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        'status': 'success',
        'message': f'Report saved successfully ({report_type})',
        'report_id': report_id,
        'overwritten': False
    }


def get_report_by_id(report_id: int) -> Optional[Dict]:
    """Get a specific report by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM mbr_reports WHERE id = ?', (report_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        'id': row['id'],
        'report_type': row['report_type'],
        'from_date': row['from_date'],
        'to_date': row['to_date'],
        'duration_days': row['duration_days'],
        'total_domains': row['total_domains'],
        'total_accounts': row['total_accounts'],
        'report_data': json.loads(row['report_data']),
        'created_at': row['created_at']
    }


def get_all_reports(report_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """
    Get all saved reports (without full report_data)

    Args:
        report_type: Filter by type ('domain' or 'account'), None for all
        limit: Maximum number of reports to return

    Returns:
        List of report summaries
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if report_type:
        cursor.execute('''
            SELECT id, report_type, from_date, to_date, duration_days,
                   total_domains, total_accounts, created_at, month, year
            FROM mbr_reports
            WHERE report_type = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (report_type, limit))
    else:
        cursor.execute('''
            SELECT id, report_type, from_date, to_date, duration_days,
                   total_domains, total_accounts, created_at, month, year
            FROM mbr_reports
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def delete_report(report_id: int) -> bool:
    """Delete a report by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM mbr_reports WHERE id = ?', (report_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def get_report_statistics() -> Dict:
    """Get statistics about stored reports"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as total FROM mbr_reports')
    total = cursor.fetchone()['total']

    cursor.execute('SELECT COUNT(*) as count FROM mbr_reports WHERE report_type = "domain"')
    domain_count = cursor.fetchone()['count']

    cursor.execute('SELECT COUNT(*) as count FROM mbr_reports WHERE report_type = "account"')
    account_count = cursor.fetchone()['count']

    cursor.execute('SELECT MIN(from_date) as earliest, MAX(to_date) as latest FROM mbr_reports')
    date_range = cursor.fetchone()

    conn.close()

    return {
        'total_reports': total,
        'domain_reports': domain_count,
        'account_reports': account_count,
        'earliest_report': date_range['earliest'],
        'latest_report': date_range['latest']
    }


# Initialize database on module import
if __name__ != '__main__':
    try:
        init_mbr_reports_database()
    except Exception as e:
        print(f"Error initializing MBR reports database: {e}")
