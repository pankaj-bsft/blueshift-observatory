"""
Email Service
Manages email recipients and sends MBR reports via SendGrid
"""
import sqlite3
import base64
import ssl
import certifi
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition

# Configure SSL to use certifi certificates
import os
from dotenv import load_dotenv

load_dotenv()
os.environ['SSL_CERT_FILE'] = certifi.where()


# SendGrid Configuration (loaded from environment)
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
FROM_EMAIL = 'Pankaj@deliverability.getblueshift.com'
REPLY_TO_EMAIL = 'pankaj.kumar@getblueshift.com'

DB_PATH = '/Users/pankaj/pani/data/account_mappings.db'


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_email_recipients_database():
    """Initialize email recipients database table"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            notes TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create index for faster lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_email
        ON email_recipients(email)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_is_active
        ON email_recipients(is_active)
    ''')

    conn.commit()
    conn.close()
    print(f"Email recipients database initialized at {DB_PATH}")


def get_all_recipients(active_only: bool = True) -> List[Dict]:
    """
    Get all email recipients

    Args:
        active_only: If True, only return active recipients

    Returns:
        List of recipient dictionaries
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if active_only:
        cursor.execute('''
            SELECT * FROM email_recipients
            WHERE is_active = 1
            ORDER BY name
        ''')
    else:
        cursor.execute('''
            SELECT * FROM email_recipients
            ORDER BY name
        ''')

    recipients = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return recipients


def get_recipient_by_id(recipient_id: int) -> Optional[Dict]:
    """Get single recipient by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM email_recipients WHERE id = ?', (recipient_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def create_recipient(name: str, email: str, notes: str = '') -> Dict:
    """Create new email recipient"""
    conn = get_db_connection()
    cursor = conn.cursor()

    name = name.strip()
    email = email.strip().lower()

    try:
        cursor.execute('''
            INSERT INTO email_recipients (name, email, notes)
            VALUES (?, ?, ?)
        ''', (name, email, notes))

        recipient_id = cursor.lastrowid
        conn.commit()

        # Fetch the created recipient
        cursor.execute('SELECT * FROM email_recipients WHERE id = ?', (recipient_id,))
        result = dict(cursor.fetchone())
        conn.close()

        return result
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Email '{email}' already exists")


def update_recipient(recipient_id: int, name: str = None, email: str = None,
                     notes: str = None, is_active: bool = None) -> Dict:
    """Update existing recipient"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get existing recipient
    cursor.execute('SELECT * FROM email_recipients WHERE id = ?', (recipient_id,))
    existing = cursor.fetchone()

    if not existing:
        conn.close()
        raise ValueError(f"Recipient with id {recipient_id} not found")

    # Prepare update values
    new_name = name.strip() if name else existing['name']
    new_email = email.strip().lower() if email else existing['email']
    new_notes = notes if notes is not None else existing['notes']
    new_is_active = (1 if is_active else 0) if is_active is not None else existing['is_active']

    try:
        cursor.execute('''
            UPDATE email_recipients
            SET name = ?, email = ?, notes = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_name, new_email, new_notes, new_is_active, recipient_id))

        conn.commit()

        # Fetch updated recipient
        cursor.execute('SELECT * FROM email_recipients WHERE id = ?', (recipient_id,))
        result = dict(cursor.fetchone())
        conn.close()

        return result
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Email '{new_email}' already exists")


def delete_recipient(recipient_id: int) -> bool:
    """Delete recipient by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM email_recipients WHERE id = ?', (recipient_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def send_report_email(recipient_emails: List[str], subject: str, body: str,
                     pdf_data: bytes, pdf_filename: str) -> Dict:
    """
    Send MBR report email via SendGrid

    Args:
        recipient_emails: List of recipient email addresses
        subject: Email subject line
        body: Email body (plain text or HTML)
        pdf_data: PDF file data as bytes
        pdf_filename: Name of the PDF file

    Returns:
        Dict with status and message
    """
    try:
        # Create SendGrid message
        message = Mail(
            from_email=Email(FROM_EMAIL),
            to_emails=[To(email) for email in recipient_emails],
            subject=subject,
            html_content=Content("text/html", body)
        )

        # Set reply-to
        message.reply_to = Email(REPLY_TO_EMAIL)

        # Attach PDF
        encoded_pdf = base64.b64encode(pdf_data).decode()
        attachment = Attachment(
            FileContent(encoded_pdf),
            FileName(pdf_filename),
            FileType('application/pdf'),
            Disposition('attachment')
        )
        message.attachment = attachment

        # Send email
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)

        return {
            'status': 'success',
            'message': f'Email sent successfully to {len(recipient_emails)} recipient(s)',
            'status_code': response.status_code,
            'recipients': recipient_emails
        }

    except Exception as e:
        return {
            'status': 'error',
            'message': f'Failed to send email: {str(e)}',
            'error': str(e)
        }


def get_recipient_statistics() -> Dict:
    """Get statistics about email recipients"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as total FROM email_recipients')
    total = cursor.fetchone()['total']

    cursor.execute('SELECT COUNT(*) as count FROM email_recipients WHERE is_active = 1')
    active_count = cursor.fetchone()['count']

    cursor.execute('SELECT COUNT(*) as count FROM email_recipients WHERE is_active = 0')
    inactive_count = cursor.fetchone()['count']

    conn.close()

    return {
        'total_recipients': total,
        'active_recipients': active_count,
        'inactive_recipients': inactive_count
    }


# Initialize database on module import
if __name__ != '__main__':
    try:
        init_email_recipients_database()
    except Exception as e:
        print(f"Error initializing email recipients database: {e}")
