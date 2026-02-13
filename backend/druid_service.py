import json
import requests
import pandas as pd
from typing import Dict, List, Optional, Tuple
from config import DRUID_US_BROKER, DRUID_EU_BROKER, DRUID_QUERY_TEMPLATE, ESPS
from health_score_service import add_health_score_to_summary


def execute_druid_query(broker_url: str, query: str, timeout: int = 120) -> List[Dict]:
    """Execute a Druid SQL query and return results as JSON"""
    headers = {'Content-Type': 'application/json'}
    payload = {'query': query}

    try:
        response = requests.post(
            broker_url,
            headers=headers,
            data=json.dumps(payload),
            timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f'Druid query failed: {e}')
        return []


def fetch_region_data(region_name: str, broker_url: str, from_date: str, to_date: str) -> pd.DataFrame:
    """Fetch deliverability data from a specific Druid region"""
    print(f'Querying {region_name} Druid broker...')

    query = DRUID_QUERY_TEMPLATE.format(start_date=from_date, end_date=to_date)
    results = execute_druid_query(broker_url, query)

    if not results:
        print(f'No data returned from {region_name} broker')
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df['Region'] = region_name
    print(f'Retrieved {len(df)} rows from {region_name}')
    return df


def calculate_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate derived metrics (rates, percentages) for deliverability data"""
    numeric_cols = [
        'Sent', 'Delivered', 'Unique_user_open', 'Unique_pre_fetch_open',
        'Unique_proxy_open', 'Clicks', 'unique_click', 'Bounces',
        'Unique_soft_bounce', 'Spam_report', 'Unsubscribe'
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    df['Delivery_Rate_%'] = df.apply(
        lambda row: round((row['Delivered'] / row['Sent'] * 100), 2)
        if row['Sent'] > 0 else 0.0, axis=1
    )

    df['Bounce_Rate_%'] = df.apply(
        lambda row: round((row['Bounces'] / row['Sent'] * 100), 4)
        if row['Sent'] > 0 else 0.0, axis=1
    )

    df['Spam_Rate_%'] = df.apply(
        lambda row: round((row['Spam_report'] / row['Delivered'] * 100), 4)
        if row['Delivered'] > 0 else 0.0, axis=1
    )

    df['Unsub_Rate_%'] = df.apply(
        lambda row: round((row['Unsubscribe'] / row['Delivered'] * 100), 4)
        if row['Delivered'] > 0 else 0.0, axis=1
    )

    df['Total_Unique_Opens'] = (
        df['Unique_user_open'] +
        df['Unique_pre_fetch_open'] +
        df['Unique_proxy_open']
    )

    df['Open_Rate_%'] = df.apply(
        lambda row: round((row['Total_Unique_Opens'] / row['Delivered'] * 100), 2)
        if row['Delivered'] > 0 else 0.0, axis=1
    )

    df['Click_Rate_%'] = df.apply(
        lambda row: round((row['unique_click'] / row['Delivered'] * 100), 2)
        if row['Delivered'] > 0 else 0.0, axis=1
    )

    df['CTOR_%'] = df.apply(
        lambda row: round((row['unique_click'] / row['Total_Unique_Opens'] * 100), 2)
        if row['Total_Unique_Opens'] > 0 else 0.0, axis=1
    )

    return df


def aggregate_region_summary(df: pd.DataFrame) -> Optional[Dict]:
    """Aggregate metrics for a region or combined dataset"""
    if df.empty:
        return None

    total_sent = df['Sent'].sum()
    total_delivered = df['Delivered'].sum()
    total_bounces = df['Bounces'].sum()
    total_spam = df['Spam_report'].sum()
    total_unsub = df['Unsubscribe'].sum()
    total_user_opens = df['Unique_user_open'].sum()
    total_prefetch_opens = df['Unique_pre_fetch_open'].sum()
    total_proxy_opens = df['Unique_proxy_open'].sum()
    total_unique_opens = total_user_opens + total_prefetch_opens + total_proxy_opens
    total_unique_clicks = df['unique_click'].sum()
    total_soft_bounces = df['Unique_soft_bounce'].sum()

    delivery_rate = round((total_delivered / total_sent * 100), 2) if total_sent > 0 else 0.0
    bounce_rate = round((total_bounces / total_sent * 100), 4) if total_sent > 0 else 0.0
    spam_rate = round((total_spam / total_delivered * 100), 4) if total_delivered > 0 else 0.0
    unsub_rate = round((total_unsub / total_delivered * 100), 4) if total_delivered > 0 else 0.0
    open_rate = round((total_unique_opens / total_delivered * 100), 2) if total_delivered > 0 else 0.0
    click_rate = round((total_unique_clicks / total_delivered * 100), 2) if total_delivered > 0 else 0.0
    ctor = round((total_unique_clicks / total_unique_opens * 100), 2) if total_unique_opens > 0 else 0.0

    summary = {
        'Total_Sent': int(total_sent),
        'Total_Delivered': int(total_delivered),
        'Total_Bounces': int(total_bounces),
        'Total_Soft_Bounces': int(total_soft_bounces),
        'Total_Spam_Reports': int(total_spam),
        'Total_Unsubscribes': int(total_unsub),
        'Total_Unique_User_Opens': int(total_user_opens),
        'Total_Unique_PreFetch_Opens': int(total_prefetch_opens),
        'Total_Unique_Proxy_Opens': int(total_proxy_opens),
        'Total_Unique_Opens': int(total_unique_opens),
        'Total_Unique_Clicks': int(total_unique_clicks),
        'Delivery_Rate_%': delivery_rate,
        'Bounce_Rate_%': bounce_rate,
        'Spam_Rate_%': spam_rate,
        'Unsub_Rate_%': unsub_rate,
        'Open_Rate_%': open_rate,
        'Click_Rate_%': click_rate,
        'CTOR_%': ctor,
        'Domain_Count': len(df)
    }

    # Add health score
    summary = add_health_score_to_summary(summary)

    return summary


def get_top10_domains(df: pd.DataFrame) -> pd.DataFrame:
    """Get top 10 sending domains by volume with all metrics"""
    if df.empty:
        return pd.DataFrame()

    grouped = df.groupby('From_domain', as_index=False).agg({
        'Sent': 'sum',
        'Delivered': 'sum',
        'Bounces': 'sum',
        'Unique_soft_bounce': 'sum',
        'Spam_report': 'sum',
        'Unsubscribe': 'sum',
        'Unique_user_open': 'sum',
        'Unique_pre_fetch_open': 'sum',
        'Unique_proxy_open': 'sum',
        'unique_click': 'sum'
    })

    grouped = calculate_metrics(grouped)
    top10 = grouped.sort_values('Sent', ascending=False).head(10)
    return top10


def aggregate_data_by_esp(df_us: pd.DataFrame, df_eu: pd.DataFrame) -> Tuple[Dict, pd.DataFrame]:
    """Aggregate data by ESP with regional breakdowns"""
    df_combined = pd.concat([df_us, df_eu], ignore_index=True)
    df_combined = df_combined[df_combined['Delivered'] > 0].copy()
    df_combined = calculate_metrics(df_combined)

    esp_data = {}

    for esp in ESPS:
        esp_df = df_combined[df_combined['ESP'] == esp].copy()

        if esp_df.empty:
            print(f'No data found for {esp}')
            continue

        us_df = esp_df[esp_df['Region'] == 'US'].copy()
        us_summary = aggregate_region_summary(us_df) if not us_df.empty else None

        eu_df = esp_df[esp_df['Region'] == 'EU'].copy()
        eu_summary = aggregate_region_summary(eu_df) if not eu_df.empty else None

        combined_summary = aggregate_region_summary(esp_df)
        top10_domains = get_top10_domains(esp_df)

        esp_data[esp] = {
            'us_summary': us_summary,
            'eu_summary': eu_summary,
            'combined_summary': combined_summary,
            'top10_domains': top10_domains.to_dict('records') if not top10_domains.empty else [],
            'all_data': esp_df.to_dict('records')
        }

        print(f'Processed {esp}: {len(esp_df)} domains')

    return esp_data, df_combined


def get_top10_overall(df_combined: pd.DataFrame) -> pd.DataFrame:
    """Get top 10 sending domains across all ESPs"""
    if df_combined.empty:
        return pd.DataFrame()

    grouped = df_combined.groupby('From_domain', as_index=False).agg({
        'Sent': 'sum',
        'Delivered': 'sum',
        'Bounces': 'sum',
        'Unique_soft_bounce': 'sum',
        'Spam_report': 'sum',
        'Unsubscribe': 'sum',
        'Unique_user_open': 'sum',
        'Unique_pre_fetch_open': 'sum',
        'Unique_proxy_open': 'sum',
        'unique_click': 'sum',
        'ESP': lambda x: ', '.join(sorted(set(x)))
    })

    grouped = calculate_metrics(grouped)
    top10 = grouped.sort_values('Sent', ascending=False).head(10)
    return top10
