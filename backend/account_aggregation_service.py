"""
Account Aggregation Service
Aggregates domain-level data to account-level using mappings
"""
import pandas as pd
from typing import Dict, List
from account_mapping_service import get_account_for_domain, get_affiliate_accounts


def add_account_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add account_name column to dataframe based on domain mapping
    """
    if df.empty:
        return df

    df = df.copy()

    # Map each domain to its account
    df['Account'] = df['From_domain'].apply(
        lambda domain: get_account_for_domain(domain) or 'Unmapped'
    )

    return df


def aggregate_by_account(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate domain-level data to account-level
    Sums volumes, recalculates percentages
    """
    if df.empty or 'Account' not in df.columns:
        return pd.DataFrame()

    # Group by Account and ESP if ESP column exists
    group_cols = ['Account']
    if 'ESP' in df.columns:
        group_cols.append('ESP')

    # Aggregate volume metrics
    agg_dict = {
        'Sent': 'sum',
        'Delivered': 'sum',
    }

    # Add other numeric columns if they exist
    optional_columns = [
        'Bounces', 'Spam_Reports', 'Spam_report',
        'Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open',
        'unique_click', 'Unsubscribes', 'Unsubscribe'
    ]

    for col in optional_columns:
        if col in df.columns:
            agg_dict[col] = 'sum'

    # Calculate Total_Unique_Opens if component columns exist
    if all(col in df.columns for col in ['Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open']):
        # Store individual components for aggregation
        pass  # Already added to agg_dict above

    # Perform aggregation
    df_agg = df.groupby(group_cols, as_index=False).agg(agg_dict)

    # Calculate Total_Unique_Opens if component columns exist
    if all(col in df_agg.columns for col in ['Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open']):
        df_agg['Total_Unique_Opens'] = (
            df_agg['Unique_user_open'] +
            df_agg['Unique_pre_fetch_open'] +
            df_agg['Unique_proxy_open']
        )

    # Recalculate percentage metrics
    if 'Delivered' in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Delivery_Rate_%'] = (
            (df_agg['Delivered'] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'Bounces' in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Bounce_Rate_%'] = (
            (df_agg['Bounces'] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Handle both Spam_Reports and Spam_report columns
    spam_col = 'Spam_Reports' if 'Spam_Reports' in df_agg.columns else 'Spam_report'
    if spam_col in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Spam_Rate_%'] = (
            (df_agg[spam_col] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Handle both Unsubscribes and Unsubscribe columns
    unsub_col = 'Unsubscribes' if 'Unsubscribes' in df_agg.columns else 'Unsubscribe'
    if unsub_col in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Unsub_Rate_%'] = (
            (df_agg[unsub_col] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Calculate Open Rate using Total_Unique_Opens
    if 'Total_Unique_Opens' in df_agg.columns and 'Delivered' in df_agg.columns:
        df_agg['Open_Rate_%'] = (
            (df_agg['Total_Unique_Opens'] / df_agg['Delivered'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Calculate Click Rate using unique_click
    if 'unique_click' in df_agg.columns and 'Delivered' in df_agg.columns:
        df_agg['Click_Rate_%'] = (
            (df_agg['unique_click'] / df_agg['Delivered'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Calculate CTOR
    if 'unique_click' in df_agg.columns and 'Total_Unique_Opens' in df_agg.columns:
        df_agg['CTOR_%'] = (
            (df_agg['unique_click'] / df_agg['Total_Unique_Opens'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Final cleanup: Replace any remaining NaN or inf values
    df_agg = df_agg.fillna(0)
    df_agg = df_agg.replace([float('inf'), float('-inf')], 0)

    return df_agg


def get_top_accounts_by_esp(df: pd.DataFrame, top_n: int = 10) -> Dict[str, List[Dict]]:
    """
    Get top N accounts by send volume for each ESP
    Returns: {esp_name: [top accounts list]}
    """
    if df.empty or 'Account' not in df.columns:
        return {}

    # Add account column if not present
    if 'Account' not in df.columns:
        df = add_account_column(df)

    # Aggregate by account and ESP
    df_agg = aggregate_by_account(df)

    if df_agg.empty:
        return {}

    result = {}

    if 'ESP' in df_agg.columns:
        for esp in df_agg['ESP'].unique():
            esp_data = df_agg[df_agg['ESP'] == esp].copy()
            esp_data = esp_data.sort_values('Sent', ascending=False).head(top_n)

            # Add rank
            esp_data['Rank'] = range(1, len(esp_data) + 1)

            result[esp] = esp_data.to_dict('records')
    else:
        # If no ESP column, treat as single group
        top_accounts = df_agg.sort_values('Sent', ascending=False).head(top_n)
        top_accounts['Rank'] = range(1, len(top_accounts) + 1)
        result['All'] = top_accounts.to_dict('records')

    return result


def get_top_accounts_overall(df: pd.DataFrame, top_n: int = 10) -> List[Dict]:
    """
    Get top N accounts overall (across all ESPs combined)
    """
    if df.empty:
        return []

    # Add account column if not present
    if 'Account' not in df.columns:
        df = add_account_column(df)

    # Aggregate by account only (not by ESP)
    group_cols = ['Account']

    agg_dict = {
        'Sent': 'sum',
        'Delivered': 'sum',
    }

    optional_columns = [
        'Bounces', 'Spam_Reports', 'Spam_report',
        'Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open',
        'unique_click', 'Unsubscribes', 'Unsubscribe'
    ]

    for col in optional_columns:
        if col in df.columns:
            agg_dict[col] = 'sum'

    df_agg = df.groupby(group_cols, as_index=False).agg(agg_dict)

    # Calculate Total_Unique_Opens if component columns exist
    if all(col in df_agg.columns for col in ['Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open']):
        df_agg['Total_Unique_Opens'] = (
            df_agg['Unique_user_open'] +
            df_agg['Unique_pre_fetch_open'] +
            df_agg['Unique_proxy_open']
        )

    # Recalculate percentages
    if 'Delivered' in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Delivery_Rate_%'] = (
            (df_agg['Delivered'] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'Bounces' in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Bounce_Rate_%'] = (
            (df_agg['Bounces'] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    spam_col = 'Spam_Reports' if 'Spam_Reports' in df_agg.columns else 'Spam_report'
    if spam_col in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Spam_Rate_%'] = (
            (df_agg[spam_col] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'Total_Unique_Opens' in df_agg.columns and 'Delivered' in df_agg.columns:
        df_agg['Open_Rate_%'] = (
            (df_agg['Total_Unique_Opens'] / df_agg['Delivered'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'unique_click' in df_agg.columns and 'Delivered' in df_agg.columns:
        df_agg['Click_Rate_%'] = (
            (df_agg['unique_click'] / df_agg['Delivered'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Sort by sent volume and get top N
    df_top = df_agg.sort_values('Sent', ascending=False).head(top_n)

    # Add rank
    df_top['Rank'] = range(1, len(df_top) + 1)

    # Final cleanup: Replace any remaining NaN or inf values
    df_top = df_top.fillna(0)
    df_top = df_top.replace([float('inf'), float('-inf')], 0)

    return df_top.to_dict('records')


def get_account_summary(df: pd.DataFrame, account_name: str) -> Dict:
    """
    Get detailed summary for a specific account
    """
    if df.empty:
        return {}

    # Add account column if not present
    if 'Account' not in df.columns:
        df = add_account_column(df)

    # Filter for specific account
    account_data = df[df['Account'] == account_name].copy()

    if account_data.empty:
        return {'error': f"No data found for account: {account_name}"}

    # Aggregate metrics
    summary = {
        'account_name': account_name,
        'total_sent': int(account_data['Sent'].sum()),
        'total_delivered': int(account_data['Delivered'].sum()),
        'delivery_rate': round(
            account_data['Delivered'].sum() / account_data['Sent'].sum() * 100, 2
        ),
        'total_domains': int(account_data['From_domain'].nunique()),
    }

    # Add ESP breakdown if available
    if 'ESP' in account_data.columns:
        summary['by_esp'] = {}
        for esp in account_data['ESP'].unique():
            esp_data = account_data[account_data['ESP'] == esp]
            summary['by_esp'][esp] = {
                'sent': int(esp_data['Sent'].sum()),
                'delivered': int(esp_data['Delivered'].sum()),
                'delivery_rate': round(
                    esp_data['Delivered'].sum() / esp_data['Sent'].sum() * 100, 2
                ) if esp_data['Sent'].sum() > 0 else 0
            }

    # Get list of domains
    summary['domains'] = sorted(account_data['From_domain'].unique().tolist())

    return summary


def get_affiliate_accounts_data(df: pd.DataFrame) -> List[Dict]:
    """
    Get aggregated data for all affiliate accounts (is_affiliate = 1)
    Returns sorted list by send volume
    """
    if df.empty:
        return []

    # Get list of affiliate account names from database
    affiliate_names = get_affiliate_accounts()

    if not affiliate_names:
        return []

    # Add account column if not present
    if 'Account' not in df.columns:
        df = add_account_column(df)

    # Filter for affiliate accounts only
    df_affiliates = df[df['Account'].isin(affiliate_names)].copy()

    if df_affiliates.empty:
        return []

    # Aggregate by account only (across all ESPs)
    group_cols = ['Account']

    agg_dict = {
        'Sent': 'sum',
        'Delivered': 'sum',
    }

    optional_columns = [
        'Bounces', 'Spam_Reports', 'Spam_report',
        'Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open',
        'unique_click', 'Unsubscribes', 'Unsubscribe'
    ]

    for col in optional_columns:
        if col in df_affiliates.columns:
            agg_dict[col] = 'sum'

    df_agg = df_affiliates.groupby(group_cols, as_index=False).agg(agg_dict)

    # Calculate Total_Unique_Opens
    if all(col in df_agg.columns for col in ['Unique_user_open', 'Unique_pre_fetch_open', 'Unique_proxy_open']):
        df_agg['Total_Unique_Opens'] = (
            df_agg['Unique_user_open'] +
            df_agg['Unique_pre_fetch_open'] +
            df_agg['Unique_proxy_open']
        )

    # Calculate percentage metrics
    if 'Delivered' in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Delivery_Rate_%'] = (
            (df_agg['Delivered'] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'Bounces' in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Bounce_Rate_%'] = (
            (df_agg['Bounces'] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    spam_col = 'Spam_Reports' if 'Spam_Reports' in df_agg.columns else 'Spam_report'
    if spam_col in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Spam_Rate_%'] = (
            (df_agg[spam_col] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    unsub_col = 'Unsubscribes' if 'Unsubscribes' in df_agg.columns else 'Unsubscribe'
    if unsub_col in df_agg.columns and 'Sent' in df_agg.columns:
        df_agg['Unsub_Rate_%'] = (
            (df_agg[unsub_col] / df_agg['Sent'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'Total_Unique_Opens' in df_agg.columns and 'Delivered' in df_agg.columns:
        df_agg['Open_Rate_%'] = (
            (df_agg['Total_Unique_Opens'] / df_agg['Delivered'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    if 'unique_click' in df_agg.columns and 'Delivered' in df_agg.columns:
        df_agg['Click_Rate_%'] = (
            (df_agg['unique_click'] / df_agg['Delivered'] * 100)
            .fillna(0)
            .replace([float('inf'), float('-inf')], 0)
            .round(2)
        )

    # Sort by send volume descending
    df_agg = df_agg.sort_values('Sent', ascending=False)

    # Add rank
    df_agg['Rank'] = range(1, len(df_agg) + 1)

    # Replace any remaining NaN or inf values before converting to dict
    df_agg = df_agg.fillna(0)
    df_agg = df_agg.replace([float('inf'), float('-inf')], 0)

    return df_agg.to_dict('records')
