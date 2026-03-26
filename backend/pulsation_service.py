import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import os
from druid_service import execute_druid_query, calculate_metrics
from config import DRUID_US_BROKER, DRUID_EU_BROKER
from account_mapping_service import get_all_mappings
from spamhaus_service import get_spamhaus_listing_summary, ensure_daily_refresh

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


def _attach_account_name(df: pd.DataFrame) -> pd.DataFrame:
    """Attach account_name to dataframe based on From_domain."""
    if df.empty or 'From_domain' not in df.columns:
        df['account_name'] = 'Unmapped'
        return df

    try:
        mappings = get_all_mappings(limit=100000).get('mappings', [])
        mapping_dict = {m.get('sending_domain', '').lower(): m.get('account_name', '') for m in mappings}
    except Exception:
        mapping_dict = {}

    df['account_name'] = df['From_domain'].astype(str).str.lower().map(mapping_dict).fillna('Unmapped')
    return df


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

    df = _attach_account_name(df)

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
    df = _attach_account_name(df)
    return df


def get_domain_timeseries(from_domain: str, days: int = 30, mode: str = "daily") -> pd.DataFrame:
    """Get time-series data for a specific domain"""
    cutoff_date = (datetime.utcnow().date() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT
            report_date,
            SUM(sent) as sent,
            SUM(delivered) as delivered,
            SUM(spam_report) as spam_report,
            SUM(bounces) as bounces,
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
    df = _aggregate_timeseries(df, mode)
    return df


def get_account_timeseries(account_name: str, days: int = 30, mode: str = "daily") -> pd.DataFrame:
    """Get time-series data for a specific account"""
    cutoff_date = (datetime.utcnow().date() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT
            report_date,
            from_domain,
            SUM(sent) as sent,
            SUM(delivered) as delivered,
            SUM(spam_report) as spam_report,
            SUM(bounces) as bounces
        FROM daily_metrics
        WHERE report_date >= ?
        GROUP BY report_date, from_domain
        ORDER BY report_date
    """
    df = pd.read_sql_query(query, conn, params=(cutoff_date,))
    conn.close()

    if df.empty:
        return df

    df.rename(columns={"from_domain": "From_domain"}, inplace=True)
    df = _attach_account_name(df)

    if account_name.lower() == 'unmapped':
        df = df[df['account_name'].str.lower() == 'unmapped']
    else:
        df = df[df['account_name'].str.lower() == account_name.lower()]

    if df.empty:
        return df

    grouped = df.groupby('report_date', as_index=False).agg({
        'sent': 'sum',
        'delivered': 'sum',
        'spam_report': 'sum',
        'bounces': 'sum'
    })

    grouped['delivery_rate'] = grouped.apply(lambda r: round(pct(r['delivered'], r['sent']), 2), axis=1)
    grouped['spam_rate'] = grouped.apply(lambda r: round(pct(r['spam_report'], r['delivered']), 4), axis=1)
    grouped['bounce_rate'] = grouped.apply(lambda r: round(pct(r['bounces'], r['sent']), 4), axis=1)

    grouped = _aggregate_timeseries(grouped, mode)
    return grouped





def _aggregate_timeseries(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    mode = (mode or 'daily').lower()
    if mode == 'daily':
        return df

    df = df.copy()
    df['report_date'] = pd.to_datetime(df['report_date'], errors='coerce')
    df = df.dropna(subset=['report_date'])
    if df.empty:
        return df

    if mode == 'weekly':
        df['period'] = df['report_date'].dt.strftime('%Y-W%W')
    elif mode == 'monthly':
        df['period'] = df['report_date'].dt.strftime('%Y-%m')
    else:
        return df

    grouped = df.groupby('period', as_index=False).agg({
        'sent': 'sum',
        'delivered': 'sum',
        'spam_report': 'sum',
        'bounces': 'sum'
    })

    grouped['delivery_rate'] = grouped.apply(lambda r: round(pct(r['delivered'], r['sent']), 2), axis=1)
    grouped['spam_rate'] = grouped.apply(lambda r: round(pct(r['spam_report'], r['delivered']), 4), axis=1)
    grouped['bounce_rate'] = grouped.apply(lambda r: round(pct(r['bounces'], r['sent']), 4), axis=1)
    grouped.rename(columns={'period': 'report_date'}, inplace=True)
    return grouped


def get_available_dates() -> List[str]:
    """Get list of dates with data"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT report_date FROM daily_metrics ORDER BY report_date DESC')
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return dates


def _safe_pct(n, d, decimals=2):
    return round(0.0 if not d else (n / d) * 100.0, decimals)


def _aggregate_metrics(df: pd.DataFrame, group_key: str) -> pd.DataFrame:
    grouped = df.groupby(group_key, dropna=False).agg({
        'Sent': 'sum',
        'Delivered': 'sum',
        'Bounces': 'sum',
        'Soft_bounce_count': 'sum',
        'Spam_report': 'sum',
        'Unsubscribe': 'sum'
    }).reset_index()

    grouped['delivery_rate'] = grouped.apply(lambda r: _safe_pct(r['Delivered'], r['Sent'], 2), axis=1)
    grouped['bounce_rate'] = grouped.apply(lambda r: _safe_pct(r['Bounces'], r['Sent'], 4), axis=1)
    grouped['spam_rate'] = grouped.apply(lambda r: _safe_pct(r['Spam_report'], r['Delivered'], 4), axis=1)
    grouped['unsub_rate'] = grouped.apply(lambda r: _safe_pct(r['Unsubscribe'], r['Delivered'], 4), axis=1)
    grouped['soft_bounce_pct'] = grouped.apply(lambda r: _safe_pct(r['Soft_bounce_count'], r['Sent'], 4), axis=1)
    return grouped


def _find_anomalies(df: pd.DataFrame, name_key: str) -> List[Dict]:
    anomalies = []
    for _, row in df.iterrows():
        sent = row.get('Sent', 0) or 0
        if sent < 10000:
            continue
        delivery_rate = row.get('delivery_rate', 0) or 0
        bounce_rate = row.get('bounce_rate', 0) or 0
        spam_rate = row.get('spam_rate', 0) or 0

        if delivery_rate == 0:
            anomalies.append({'reason': 'Zero delivery', name_key: row[name_key], **row.to_dict()})
        if delivery_rate > 100:
            anomalies.append({'reason': 'Delivery >100%', name_key: row[name_key], **row.to_dict()})
        if spam_rate >= 0.2:
            anomalies.append({'reason': 'High spam rate', name_key: row[name_key], **row.to_dict()})
        if bounce_rate >= 2:
            anomalies.append({'reason': 'High bounce rate', name_key: row[name_key], **row.to_dict()})

    return anomalies


def get_pulsation_summary(start_date: datetime, end_date: datetime) -> Optional[Dict]:
    df_all = query_date_range(start_date, end_date)
    if df_all.empty:
        return None

    df_all = df_all[df_all['Sent'] > 0].copy()

    overall = {
        'sent': int(df_all['Sent'].sum()),
        'delivered': int(df_all['Delivered'].sum()),
        'bounces': int(df_all['Bounces'].sum()),
        'soft_bounces': int(df_all['Soft_bounce_count'].sum()),
        'spam_reports': int(df_all['Spam_report'].sum()),
        'unsubscribes': int(df_all['Unsubscribe'].sum())
    }
    overall['delivery_rate'] = _safe_pct(overall['delivered'], overall['sent'], 2)
    overall['bounce_rate'] = _safe_pct(overall['bounces'], overall['sent'], 4)
    overall['spam_rate'] = _safe_pct(overall['spam_reports'], overall['delivered'], 4)
    overall['unsub_rate'] = _safe_pct(overall['unsubscribes'], overall['delivered'], 4)
    overall['soft_bounce_pct'] = _safe_pct(overall['soft_bounces'], overall['sent'], 4)

    esp_summary = _aggregate_metrics(df_all, 'ESP').sort_values('Sent', ascending=False)
    domain_summary = _aggregate_metrics(df_all, 'From_domain').sort_values('Sent', ascending=False)

    df_all['account_name'] = df_all.get('account_name', 'Unmapped').fillna('Unmapped')
    account_summary = _aggregate_metrics(df_all, 'account_name').sort_values('Sent', ascending=False)

    domain_anomalies = _find_anomalies(domain_summary, 'From_domain')
    account_anomalies = _find_anomalies(account_summary, 'account_name')

    domain_high_spam = domain_summary[
        (domain_summary['Sent'] >= 10000) & (domain_summary['spam_rate'] >= 0.2)
    ].sort_values('spam_rate', ascending=False)
    account_high_spam = account_summary[
        (account_summary['Sent'] >= 10000) & (account_summary['spam_rate'] >= 0.2)
    ].sort_values('spam_rate', ascending=False)

    domains = domain_summary['From_domain'].tolist()
    ensure_daily_refresh(domains)
    spamhaus_summary = get_spamhaus_listing_summary(domains)

    return {
        'overall': overall,
        'esp_summary': esp_summary.to_dict('records'),
        'domain_top10': domain_summary.head(10).to_dict('records'),
        'account_top10': account_summary.head(10).to_dict('records'),
        'domain_anomalies': domain_anomalies,
        'account_anomalies': account_anomalies,
        'domain_high_spam': domain_high_spam.to_dict('records'),
        'account_high_spam': account_high_spam.to_dict('records'),
        'spamhaus_summary': spamhaus_summary
    }



def get_daily_summary(mode: str = 'daily', days: int = 30) -> dict:
    """
    Get aggregated pulsation data grouped by day/week/month.
    mode: 'daily' | 'weekly' | 'monthly'
    Returns two datasets:
      - 'all': includes classdojo.com
      - 'excluding_classdojo': excludes classdojo.com
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT MAX(report_date) FROM daily_metrics")
    max_date_row = cursor.fetchone()
    max_date = max_date_row[0] if max_date_row else None
    if not max_date:
        conn.close()
        return {
            'mode': mode,
            'range_start': None,
            'range_end': None,
            'all': [],
            'excluding_classdojo': []
        }

    try:
        max_dt = datetime.strptime(max_date, '%Y-%m-%d')
    except ValueError:
        conn.close()
        return {
            'mode': mode,
            'range_start': None,
            'range_end': None,
            'all': [],
            'excluding_classdojo': []
        }

    days = int(days) if days else 30
    if days < 1:
        days = 1
    start_dt = max_dt - timedelta(days=days - 1)
    start_date = start_dt.strftime('%Y-%m-%d')

    if mode == 'weekly':
        period_expr = "strftime('%Y-W%W', report_date)"
    elif mode == 'monthly':
        period_expr = "strftime('%Y-%m', report_date)"
    else:
        period_expr = "report_date"
        mode = 'daily'

    def run_query(exclude_classdojo: bool = False):
        where_clauses = ["report_date >= ?", "report_date <= ?"]
        params = [start_date, max_date]
        if exclude_classdojo:
            where_clauses.append("from_domain != 'classdojo.com'")
        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                {period_expr} as period,
                SUM(sent) as total_sent,
                SUM(delivered) as total_delivered,
                SUM(bounces) as total_bounces,
                SUM(spam_report) as total_spam,
                SUM(unsubscribe) as total_unsub,
                ROUND(SUM(delivered) * 100.0 / NULLIF(SUM(sent), 0), 2) as delivery_rate,
                ROUND(SUM(bounces) * 100.0 / NULLIF(SUM(sent), 0), 2) as bounce_rate,
                ROUND(SUM(spam_report) * 100.0 / NULLIF(SUM(delivered), 0), 4) as spam_rate
            FROM daily_metrics
            WHERE {where_sql}
            GROUP BY {period_expr}
            ORDER BY {period_expr} ASC
        """
        cursor = conn.cursor()
        cursor.execute(sql, params)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    all_data = run_query(False)
    excl_data = run_query(True)
    conn.close()

    return {
        'mode': mode,
        'range_start': start_date,
        'range_end': max_date,
        'all': all_data,
        'excluding_classdojo': excl_data
    }
