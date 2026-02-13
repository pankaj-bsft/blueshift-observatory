import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import os
from druid_service import execute_druid_query, calculate_metrics
from config import DRUID_US_BROKER, DRUID_EU_BROKER

# Database path
DB_PATH = '/Users/pankaj/pani/data/deliverability_history.db'
RETENTION_DAYS = 365

# Druid query template for Pulsation
PULSATION_QUERY_TEMPLATE = """
SELECT
  MV_OFFSET(STRING_TO_MV(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address'), '@'), 1) AS "From_domain",
  sum(case action when 'sent' then "count" else 0 end) as Sent,
  sum(case action when 'delivered' then "count" else 0 end) as Delivered,
  sum(case action when 'bounce' then "count" else 0 end) as Bounces,
  SUM(CASE WHEN action = 'soft_bounce' THEN "count" ELSE 0 END) AS Soft_bounce_count,
  APPROX_COUNT_DISTINCT_DS_HLL(case action when 'soft_bounce' then "message_distinct" else null end) as Unique_soft_bounce,
  sum(case action when 'spam_report' then "count" else 0 end) as Spam_report,
  sum(case action when 'unsubscribe' then "count" else 0 end) as Unsubscribe,
  LOOKUP(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name') AS "ESP"
FROM ucts_1
WHERE "__time" >= TIMESTAMP '{start_date}'
  AND "__time" < TIMESTAMP '{end_date}'
  AND LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address') IS NOT NULL
  AND LOOKUP(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name') IN ('Sparkpost','Mailgun','Sendgrid')
GROUP BY
  LOOKUP(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name'),
  MV_OFFSET(STRING_TO_MV(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address'), '@'), 1)
"""


def init_pulsation_database():
    """Create database and table if not exists"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            from_domain TEXT NOT NULL,
            region TEXT NOT NULL,
            esp TEXT NOT NULL,
            sent INTEGER,
            delivered INTEGER,
            bounces INTEGER,
            soft_bounce_count INTEGER,
            unique_soft_bounce INTEGER,
            spam_report INTEGER,
            unsubscribe INTEGER,
            delivery_rate REAL,
            spam_rate REAL,
            unsub_rate REAL,
            bounce_rate REAL,
            soft_bounce_pct REAL,
            risk_score REAL,
            classification TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(report_date, from_domain, region, esp)
        )
    """)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_report_date ON daily_metrics(report_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_from_domain ON daily_metrics(from_domain)')
    conn.commit()
    conn.close()
    print(f'Pulsation database initialized at {DB_PATH}')


def pct(n, d):
    """Calculate percentage"""
    return 0.0 if (d == 0 or d is None) else (n / d) * 100.0


def classify_row(delivery_rate, spam_rate):
    """Classify domain health based on metrics"""
    if spam_rate >= 0.3 or delivery_rate < 70:
        return 'Red (High Spam Complaints)'
    if delivery_rate < 80:
        return 'Orange (Low Delivery)'
    if 80 <= delivery_rate < 95:
        return 'Yellow (Monitor)'
    if delivery_rate >= 95:
        return 'Green (Healthy)'
    return 'Unclassified'


def process_pulsation_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Process and calculate all metrics for Pulsation data"""
    df['From_domain'] = df['From_domain'].fillna('').astype(str).str.strip().str.lower()

    num_cols = ['Sent', 'Delivered', 'Bounces', 'Soft_bounce_count', 'Unique_soft_bounce', 'Spam_report', 'Unsubscribe']
    for c in num_cols:
        df[c] = pd.to_numeric(df.get(c, 0), errors='coerce').fillna(0).astype(int)

    df['delivery_rate'] = df.apply(lambda r: round(pct(r['Delivered'], r['Sent']), 2), axis=1)
    df['spam_rate'] = df.apply(lambda r: round(pct(r['Spam_report'], r['Delivered']), 4), axis=1)
    df['unsub_rate'] = df.apply(lambda r: round(pct(r['Unsubscribe'], r['Delivered']), 4), axis=1)
    df['bounce_rate'] = df.apply(lambda r: round(pct(r['Bounces'], r['Sent']), 4), axis=1)
    df['soft_bounce_pct'] = df.apply(lambda r: round(pct(r['Soft_bounce_count'], r['Sent']), 4), axis=1)

    df['classification'] = df.apply(lambda r: classify_row(r['delivery_rate'], r['spam_rate']), axis=1)

    df['risk_score'] = (
        (100 - df['delivery_rate']) * 0.4 +
        (df['spam_rate'] * 100) * 0.4 +
        (df['bounce_rate'] * 100) * 0.2
    )

    return df


def fetch_pulsation_data(region_name: str, broker_url: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch Pulsation data from Druid"""
    print(f'Querying {region_name} Druid broker for Pulsation data...')

    query = PULSATION_QUERY_TEMPLATE.format(start_date=start_date, end_date=end_date)

    try:
        results = execute_druid_query(broker_url, query)
    except Exception as e:
        print(f'ERROR querying {region_name}: {e}')
        return pd.DataFrame()

    df = pd.DataFrame(results if isinstance(results, list) else [])

    expected = ['From_domain', 'Sent', 'Delivered', 'Bounces', 'Soft_bounce_count',
                'Unique_soft_bounce', 'Spam_report', 'Unsubscribe', 'ESP']
    for c in expected:
        if c not in df.columns:
            df[c] = 0

    df['Region'] = region_name
    return df


def data_exists_for_date(report_date: str) -> bool:
    """Check if data already exists for this date"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM daily_metrics WHERE report_date = ?', (report_date,))
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def insert_daily_data(df: pd.DataFrame, report_date: str):
    """Insert daily data into database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for _, row in df.iterrows():
        cursor.execute("""
            INSERT OR REPLACE INTO daily_metrics
            (report_date, from_domain, region, esp, sent, delivered, bounces,
             soft_bounce_count, unique_soft_bounce, spam_report, unsubscribe,
             delivery_rate, spam_rate, unsub_rate, bounce_rate, soft_bounce_pct,
             risk_score, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report_date,
            row.get('From_domain', ''),
            row.get('Region', ''),
            row.get('ESP', ''),
            int(row.get('Sent', 0)),
            int(row.get('Delivered', 0)),
            int(row.get('Bounces', 0)),
            int(row.get('Soft_bounce_count', 0)),
            int(row.get('Unique_soft_bounce', 0)),
            int(row.get('Spam_report', 0)),
            int(row.get('Unsubscribe', 0)),
            float(row.get('delivery_rate', 0.0)),
            float(row.get('spam_rate', 0.0)),
            float(row.get('unsub_rate', 0.0)),
            float(row.get('bounce_rate', 0.0)),
            float(row.get('soft_bounce_pct', 0.0)),
            float(row.get('risk_score', 0.0)),
            row.get('classification', 'Unclassified')
        ))

    conn.commit()
    conn.close()
    print(f'Inserted {len(df)} rows for {report_date}')


def cleanup_old_data(days: int = RETENTION_DAYS):
    """Delete records older than specified days"""
    cutoff_date = (datetime.utcnow().date() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM daily_metrics WHERE report_date < ?', (cutoff_date,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted_count > 0:
        print(f'Cleaned up {deleted_count} rows older than {cutoff_date}')


def query_date_range(start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Query and aggregate data for date range"""
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT
            from_domain as From_domain,
            region as Region,
            esp as ESP,
            SUM(sent) as Sent,
            SUM(delivered) as Delivered,
            SUM(bounces) as Bounces,
            SUM(soft_bounce_count) as Soft_bounce_count,
            SUM(unique_soft_bounce) as Unique_soft_bounce,
            SUM(spam_report) as Spam_report,
            SUM(unsubscribe) as Unsubscribe,
            CASE WHEN SUM(sent) > 0 THEN ROUND(100.0 * SUM(delivered) / SUM(sent), 2) ELSE 0 END as delivery_rate,
            CASE WHEN SUM(delivered) > 0 THEN ROUND(100.0 * SUM(spam_report) / SUM(delivered), 4) ELSE 0 END as spam_rate,
            CASE WHEN SUM(delivered) > 0 THEN ROUND(100.0 * SUM(unsubscribe) / SUM(delivered), 4) ELSE 0 END as unsub_rate,
            CASE WHEN SUM(sent) > 0 THEN ROUND(100.0 * SUM(bounces) / SUM(sent), 4) ELSE 0 END as bounce_rate,
            CASE WHEN SUM(sent) > 0 THEN ROUND(100.0 * SUM(soft_bounce_count) / SUM(sent), 4) ELSE 0 END as soft_bounce_pct,
            AVG(risk_score) as risk_score,
            MAX(classification) as classification
        FROM daily_metrics
        WHERE report_date >= ? AND report_date < ?
        GROUP BY from_domain, region, esp
    """
    df = pd.read_sql_query(query, conn, params=(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    conn.close()
    return df


def get_domain_timeseries(from_domain: str, days: int = 30) -> pd.DataFrame:
    """Get time-series data for a specific domain"""
    cutoff_date = (datetime.utcnow().date() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT
            report_date,
            SUM(sent) as sent,
            SUM(delivered) as delivered,
            CASE WHEN SUM(sent) > 0 THEN ROUND(100.0 * SUM(delivered) / SUM(sent), 2) ELSE 0 END as delivery_rate,
            CASE WHEN SUM(delivered) > 0 THEN ROUND(100.0 * SUM(spam_report) / SUM(delivered), 4) ELSE 0 END as spam_rate,
            CASE WHEN SUM(sent) > 0 THEN ROUND(100.0 * SUM(bounces) / SUM(sent), 4) ELSE 0 END as bounce_rate
        FROM daily_metrics
        WHERE from_domain = ? AND report_date >= ?
        GROUP BY report_date
        ORDER BY report_date
    """
    df = pd.read_sql_query(query, conn, params=(from_domain, cutoff_date))
    conn.close()
    return df


def get_available_dates() -> List[str]:
    """Get list of dates with data"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT report_date FROM daily_metrics ORDER BY report_date DESC')
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return dates
