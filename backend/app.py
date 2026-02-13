from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime
import io
from typing import Dict, Optional, List

from druid_service import (
    fetch_region_data,
    aggregate_data_by_esp,
    aggregate_region_summary,
    get_top10_overall
)
from export_service import export_to_excel, export_to_pdf
from config import DRUID_US_BROKER, DRUID_EU_BROKER
from pulsation_service import (
    init_pulsation_database,
    fetch_pulsation_data,
    process_pulsation_dataframe,
    data_exists_for_date,
    insert_daily_data,
    cleanup_old_data,
    query_date_range,
    get_domain_timeseries,
    get_available_dates
)
from datetime import timedelta
import pandas as pd
from account_mapping_service import (
    get_all_mappings,
    get_mapping_by_id,
    create_mapping,
    update_mapping,
    delete_mapping,
    bulk_delete_mappings,
    import_csv_to_database,
    export_database_to_csv,
    get_account_statistics
)
from account_aggregation_service import (
    add_account_column,
    get_top_accounts_by_esp,
    get_top_accounts_overall,
    get_account_summary,
    get_affiliate_accounts_data
)
from snds_service import (
    init_snds_database,
    fetch_snds_data,
    collect_and_store_snds_data,
    cleanup_old_data as cleanup_snds_old_data
)
from snds_analytics_service import (
    get_snds_overview,
    get_snds_data_by_period,
    get_reputation_trends,
    get_traffic_trends,
    get_top_performers,
    get_problem_ips,
    get_accounts_list as get_snds_accounts,
    get_ips_list as get_snds_ips
)
from gpt_service import (
    initialize_database as init_gpt_database,
    get_authorization_url,
    exchange_code_for_tokens,
    get_tokens as get_gpt_tokens,
    collect_and_store_gpt_data,
    list_domains as list_gpt_domains
)
from gpt_analytics_service import (
    get_overview_stats as get_gpt_overview,
    get_domain_data as get_gpt_domain_data,
    get_reputation_trends as get_gpt_reputation_trends,
    get_spam_trends as get_gpt_spam_trends,
    get_auth_trends as get_gpt_auth_trends,
    get_reputation_changes,
    get_domains_list as get_gpt_domains_list,
    get_yesterday_overview,
    get_all_domains_latest,
    get_enhanced_reputation_changes,
    get_domain_detailed_metrics
)
from mbr_storage_service import (
    check_report_exists,
    save_mbr_report,
    get_report_by_id,
    get_all_reports,
    delete_report,
    get_report_statistics
)
from mom_service import (
    add_mom_to_domain_data,
    add_mom_to_account_data
)
from email_service import (
    get_all_recipients,
    get_recipient_by_id,
    create_recipient,
    update_recipient,
    delete_recipient,
    send_report_email,
    get_recipient_statistics
)
from esp_integration_service import (
    get_all_account_info,
    clear_cache
)

app = FastAPI(title='MBR Deliverability Dashboard')

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


class DateRange(BaseModel):
    from_date: str
    to_date: str


@app.get('/')
async def root():
    return {'message': 'MBR Deliverability Dashboard API', 'status': 'running'}


@app.get('/health')
async def health_check():
    return {'status': 'healthy'}


@app.post('/api/fetch-data')
async def fetch_data(date_range: DateRange):
    """Fetch deliverability data from Druid for the given date range"""
    try:
        # Validate dates
        from_date = datetime.strptime(date_range.from_date, '%Y-%m-%d')
        to_date = datetime.strptime(date_range.to_date, '%Y-%m-%d')

        if to_date <= from_date:
            raise HTTPException(status_code=400, detail='To Date must be after From Date')

        # Fetch data from both regions
        df_us = fetch_region_data('US', DRUID_US_BROKER, date_range.from_date, date_range.to_date)
        df_eu = fetch_region_data('EU', DRUID_EU_BROKER, date_range.from_date, date_range.to_date)

        if df_us.empty and df_eu.empty:
            raise HTTPException(status_code=404, detail='No data found - Make sure you are connected to Prod VPN')

        # Aggregate data by ESP
        esp_data, df_combined = aggregate_data_by_esp(df_us, df_eu)

        # Get overall summary
        overall_summary = aggregate_region_summary(df_combined[df_combined['Delivered'] > 0])

        # Get overall top 10
        top10_overall = get_top10_overall(df_combined)

        # Build response data
        response_data = {
            'status': 'success',
            'date_range': {
                'from_date': date_range.from_date,
                'to_date': date_range.to_date,
                'duration_days': (to_date - from_date).days
            },
            'overall_summary': overall_summary,
            'esp_data': esp_data,
            'top10_overall': top10_overall.to_dict('records') if not top10_overall.empty else [],
            'total_domains': len(df_combined['From_domain'].unique())
        }

        # Add MoM (Month-over-Month) Send change calculations
        response_data = add_mom_to_domain_data(response_data, date_range.from_date, date_range.to_date)

        return response_data

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f'Invalid date format: {str(e)}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching data: {str(e)}')


@app.post('/api/export/excel')
async def export_excel(date_range: DateRange):
    """Export data to Excel"""
    try:
        # Fetch data (reuse logic from fetch_data)
        df_us = fetch_region_data('US', DRUID_US_BROKER, date_range.from_date, date_range.to_date)
        df_eu = fetch_region_data('EU', DRUID_EU_BROKER, date_range.from_date, date_range.to_date)

        if df_us.empty and df_eu.empty:
            raise HTTPException(status_code=404, detail='No data found - Make sure you are connected to Prod VPN')

        esp_data, df_combined = aggregate_data_by_esp(df_us, df_eu)

        # Generate Excel file
        excel_data = export_to_excel(esp_data, df_combined, date_range.from_date, date_range.to_date)

        # Return as downloadable file
        filename = f"mbr_deliverability_report_{date_range.from_date}_to_{date_range.to_date}.xlsx"

        return StreamingResponse(
            io.BytesIO(excel_data),
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error generating Excel: {str(e)}')


@app.post('/api/export/pdf')
async def export_pdf(date_range: DateRange):
    """Export data to PDF with both domain and account level data"""
    try:
        # Validate dates
        from_date = datetime.strptime(date_range.from_date, '%Y-%m-%d')
        to_date = datetime.strptime(date_range.to_date, '%Y-%m-%d')

        # Fetch data
        df_us = fetch_region_data('US', DRUID_US_BROKER, date_range.from_date, date_range.to_date)
        df_eu = fetch_region_data('EU', DRUID_EU_BROKER, date_range.from_date, date_range.to_date)

        if df_us.empty and df_eu.empty:
            raise HTTPException(status_code=404, detail='No data found - Make sure you are connected to Prod VPN')

        # Get domain-level data
        esp_data, df_combined = aggregate_data_by_esp(df_us, df_eu)

        # Get account-level data
        df_combined_with_accounts = pd.concat([df_us, df_eu], ignore_index=True)
        df_combined_with_accounts = add_account_column(df_combined_with_accounts)

        # Get top accounts
        top_accounts_by_esp = get_top_accounts_by_esp(df_combined_with_accounts, top_n=10)
        top_accounts_overall = get_top_accounts_overall(df_combined_with_accounts, top_n=10)

        # Get affiliate accounts data
        affiliate_accounts = get_affiliate_accounts_data(df_combined_with_accounts)

        # Build account data structure
        account_data = {
            'esp_data': {esp: {'top10_accounts': accounts} for esp, accounts in top_accounts_by_esp.items()},
            'top10_accounts_overall': top_accounts_overall,
            'affiliate_accounts': affiliate_accounts,
        }

        # Generate PDF file with both domain and account data
        pdf_data = export_to_pdf(esp_data, df_combined, date_range.from_date, date_range.to_date, account_data)

        # Return as downloadable file
        filename = f"mbr_deliverability_report_{date_range.from_date}_to_{date_range.to_date}.pdf"

        return StreamingResponse(
            io.BytesIO(pdf_data),
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error generating PDF: {str(e)}')


# -------------------------
# Pulsation Endpoints
# -------------------------

class PulsationViewType(BaseModel):
    view_type: str  # 'yesterday', '7day', or '30day'


@app.get('/api/pulsation/init')
async def initialize_pulsation():
    """Initialize Pulsation database"""
    try:
        init_pulsation_database()
        return {'status': 'success', 'message': 'Database initialized'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error initializing database: {str(e)}')


@app.post('/api/pulsation/collect-yesterday')
async def collect_yesterday_data():
    """Collect yesterday's data from Druid and store in database"""
    try:
        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')

        # Check if data already exists
        if data_exists_for_date(yesterday_str):
            return {
                'status': 'skipped',
                'message': f'Data for {yesterday_str} already exists',
                'date': yesterday_str
            }

        # Fetch from both regions
        df_us = fetch_pulsation_data('US', DRUID_US_BROKER, yesterday_str, today_str)
        df_eu = fetch_pulsation_data('EU', DRUID_EU_BROKER, yesterday_str, today_str)

        df_yesterday = pd.concat([df_us, df_eu], ignore_index=True)
        df_yesterday = process_pulsation_dataframe(df_yesterday)

        # Insert into database
        insert_daily_data(df_yesterday, yesterday_str)

        # Cleanup old data
        cleanup_old_data()

        return {
            'status': 'success',
            'message': f'Collected and stored data for {yesterday_str}',
            'date': yesterday_str,
            'rows_inserted': len(df_yesterday)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error collecting data: {str(e)}')


@app.post('/api/pulsation/query')
async def query_pulsation_data(view: PulsationViewType):
    """Query Pulsation data by view type"""
    try:
        today = datetime.utcnow().date()

        if view.view_type == 'yesterday':
            start_date = today - timedelta(days=1)
            end_date = today
        elif view.view_type == '7day':
            start_date = today - timedelta(days=7)
            end_date = today
        elif view.view_type == '30day':
            start_date = today - timedelta(days=30)
            end_date = today
        else:
            raise HTTPException(status_code=400, detail='Invalid view_type. Must be yesterday, 7day, or 30day')

        # Query data
        df_all = query_date_range(start_date, end_date)

        if df_all.empty:
            return {
                'status': 'no_data',
                'message': 'No data available for the selected period',
                'view_type': view.view_type
            }

        # Filter to sent > 0
        df_nonzero = df_all[df_all['Sent'] > 0].copy()

        # Get Top 20 lists
        top20_low_delivery = df_nonzero.sort_values('delivery_rate', ascending=True).head(20).to_dict('records')
        top20_spam = df_nonzero.sort_values('spam_rate', ascending=False).head(20).to_dict('records')
        top20_bounce = df_nonzero.sort_values('bounce_rate', ascending=False).head(20).to_dict('records')
        top20_risk = df_nonzero.sort_values('risk_score', ascending=False).head(20).to_dict('records')

        # Classification counts
        classification_counts = df_nonzero['classification'].value_counts().to_dict()

        # Get all unique domains for chart selector
        all_domains = sorted([d for d in df_all['From_domain'].unique() if d])

        return {
            'status': 'success',
            'view_type': view.view_type,
            'date_range': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            },
            'overall_data': df_nonzero.to_dict('records'),
            'top20_low_delivery': top20_low_delivery,
            'top20_spam': top20_spam,
            'top20_bounce': top20_bounce,
            'top20_risk': top20_risk,
            'classification_counts': classification_counts,
            'all_domains': all_domains
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error querying data: {str(e)}')


@app.get('/api/pulsation/domain-timeseries/{domain}')
async def get_domain_chart_data(domain: str, days: int = 30):
    """Get time-series data for a specific domain"""
    try:
        df = get_domain_timeseries(domain, days)

        if df.empty:
            return {
                'status': 'no_data',
                'domain': domain,
                'data': []
            }

        return {
            'status': 'success',
            'domain': domain,
            'data': {
                'dates': df['report_date'].tolist(),
                'sent': df['sent'].tolist(),
                'delivered': df['delivered'].tolist(),
                'delivery_rate': df['delivery_rate'].tolist(),
                'spam_rate': df['spam_rate'].tolist(),
                'bounce_rate': df['bounce_rate'].tolist()
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching timeseries: {str(e)}')


@app.get('/api/pulsation/available-dates')
async def get_pulsation_dates():
    """Get list of dates with available data"""
    try:
        dates = get_available_dates()
        return {
            'status': 'success',
            'dates': dates,
            'count': len(dates)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching dates: {str(e)}')


# -------------------------
# Account Mapping Endpoints
# -------------------------

class AccountMapping(BaseModel):
    sending_domain: str
    account_name: str
    notes: Optional[str] = ''
    is_affiliate: Optional[bool] = False


class AccountMappingUpdate(BaseModel):
    sending_domain: Optional[str] = None
    account_name: Optional[str] = None
    notes: Optional[str] = None
    is_affiliate: Optional[bool] = None


class BulkDelete(BaseModel):
    ids: List[int]


@app.get('/api/account-mappings/statistics')
async def get_mapping_statistics():
    """Get statistics about mappings"""
    try:
        stats = get_account_statistics()
        return {
            'status': 'success',
            **stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching statistics: {str(e)}')


@app.get('/api/account-mappings')
async def get_mappings(search: str = '', limit: int = 1000, offset: int = 0):
    """Get all account mappings with optional search"""
    try:
        result = get_all_mappings(search, limit, offset)
        return {
            'status': 'success',
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching mappings: {str(e)}')


@app.get('/api/account-mappings/{mapping_id}')
async def get_mapping(mapping_id: int):
    """Get single mapping by ID"""
    try:
        mapping = get_mapping_by_id(mapping_id)
        if not mapping:
            raise HTTPException(status_code=404, detail='Mapping not found')
        return {
            'status': 'success',
            'mapping': mapping
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching mapping: {str(e)}')


@app.post('/api/account-mappings')
async def create_new_mapping(mapping: AccountMapping):
    """Create new domain-account mapping"""
    try:
        result = create_mapping(
            mapping.sending_domain,
            mapping.account_name,
            mapping.notes or '',
            mapping.is_affiliate
        )
        return {
            'status': 'success',
            'message': 'Mapping created successfully',
            'mapping': result
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error creating mapping: {str(e)}')


@app.put('/api/account-mappings/{mapping_id}')
async def update_existing_mapping(mapping_id: int, mapping: AccountMappingUpdate):
    """Update existing mapping"""
    try:
        result = update_mapping(
            mapping_id,
            mapping.sending_domain,
            mapping.account_name,
            mapping.notes,
            mapping.is_affiliate
        )
        return {
            'status': 'success',
            'message': 'Mapping updated successfully',
            'mapping': result
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error updating mapping: {str(e)}')


@app.delete('/api/account-mappings/{mapping_id}')
async def delete_existing_mapping(mapping_id: int):
    """Delete mapping by ID"""
    try:
        success = delete_mapping(mapping_id)
        if not success:
            raise HTTPException(status_code=404, detail='Mapping not found')
        return {
            'status': 'success',
            'message': 'Mapping deleted successfully'
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error deleting mapping: {str(e)}')


@app.post('/api/account-mappings/bulk-delete')
async def bulk_delete_mappings_endpoint(bulk_delete: BulkDelete):
    """Delete multiple mappings"""
    try:
        deleted_count = bulk_delete_mappings(bulk_delete.ids)
        return {
            'status': 'success',
            'message': f'Deleted {deleted_count} mapping(s)',
            'deleted_count': deleted_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error deleting mappings: {str(e)}')


@app.post('/api/account-mappings/import-csv')
async def import_csv():
    """Import CSV file into database"""
    try:
        imported, skipped = import_csv_to_database()
        return {
            'status': 'success',
            'message': f'Import complete: {imported} imported, {skipped} skipped',
            'imported': imported,
            'skipped': skipped
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error importing CSV: {str(e)}')


@app.get('/api/account-mappings/export-csv')
async def export_csv():
    """Export database to CSV file"""
    try:
        count = export_database_to_csv()
        return {
            'status': 'success',
            'message': f'Exported {count} mappings to CSV',
            'count': count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error exporting CSV: {str(e)}')


# -------------------------
# Account-Level Reporting Endpoints
# -------------------------

@app.post('/api/fetch-data-by-account')
async def fetch_data_by_account(date_range: DateRange):
    """Fetch deliverability data aggregated by account"""
    try:
        # Validate dates
        from_date = datetime.strptime(date_range.from_date, '%Y-%m-%d')
        to_date = datetime.strptime(date_range.to_date, '%Y-%m-%d')

        if to_date <= from_date:
            raise HTTPException(status_code=400, detail='To Date must be after From Date')

        # Fetch data from both regions
        df_us = fetch_region_data('US', DRUID_US_BROKER, date_range.from_date, date_range.to_date)
        df_eu = fetch_region_data('EU', DRUID_EU_BROKER, date_range.from_date, date_range.to_date)

        if df_us.empty and df_eu.empty:
            raise HTTPException(status_code=404, detail='No data found - Make sure you are connected to Prod VPN')

        # Combine and add account column
        df_combined = pd.concat([df_us, df_eu], ignore_index=True)
        df_combined = add_account_column(df_combined)

        # Get top accounts by ESP
        top_accounts_by_esp = get_top_accounts_by_esp(df_combined, top_n=10)

        # Get overall top 10 accounts
        top_accounts_overall = get_top_accounts_overall(df_combined, top_n=10)

        # Get affiliate accounts data
        affiliate_accounts = get_affiliate_accounts_data(df_combined)

        # Build response data
        response_data = {
            'status': 'success',
            'date_range': {
                'from_date': date_range.from_date,
                'to_date': date_range.to_date,
                'duration_days': (to_date - from_date).days
            },
            'esp_data': {esp: {'top10_accounts': accounts} for esp, accounts in top_accounts_by_esp.items()},
            'top10_accounts_overall': top_accounts_overall,
            'affiliate_accounts': affiliate_accounts,
            'total_accounts': len(df_combined['Account'].unique()),
            'unmapped_domains': int((df_combined['Account'] == 'Unmapped').sum())
        }

        # Add MoM (Month-over-Month) Send change calculations
        response_data = add_mom_to_account_data(response_data, date_range.from_date, date_range.to_date)

        # Extract top_accounts_by_esp from response for backward compatibility
        response_data['top_accounts_by_esp'] = response_data['esp_data']

        return response_data

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f'Invalid date format: {str(e)}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching data by account: {str(e)}')


@app.get('/api/account-summary/{account_name}')
async def get_account_details(account_name: str, from_date: str, to_date: str):
    """Get detailed summary for a specific account"""
    try:
        # Validate dates
        datetime.strptime(from_date, '%Y-%m-%d')
        datetime.strptime(to_date, '%Y-%m-%d')

        # Fetch data
        df_us = fetch_region_data('US', DRUID_US_BROKER, from_date, to_date)
        df_eu = fetch_region_data('EU', DRUID_EU_BROKER, from_date, to_date)

        df_combined = pd.concat([df_us, df_eu], ignore_index=True)
        df_combined = add_account_column(df_combined)

        summary = get_account_summary(df_combined, account_name)

        return {
            'status': 'success',
            **summary
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching account summary: {str(e)}')


# -------------------------
# MBR Report Storage Endpoints
# -------------------------

class SaveReportRequest(BaseModel):
    from_date: str
    to_date: str
    report_type: str  # 'domain' or 'account'
    report_data: Dict
    overwrite: Optional[bool] = False


@app.post('/api/mbr/check-report-exists')
async def check_existing_report(date_range: DateRange, report_type: str = 'domain'):
    """Check if a report already exists for the given date range"""
    try:
        existing = check_report_exists(date_range.from_date, date_range.to_date, report_type)
        return {
            'status': 'success',
            'exists': existing is not None,
            'report': existing
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error checking report: {str(e)}')


@app.post('/api/mbr/save-report')
async def save_report(request: SaveReportRequest):
    """Save MBR report to database"""
    try:
        result = save_mbr_report(
            request.from_date,
            request.to_date,
            request.report_type,
            request.report_data,
            request.overwrite
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error saving report: {str(e)}')


@app.get('/api/mbr/reports')
async def list_reports(report_type: Optional[str] = None, limit: int = 50):
    """Get list of saved reports"""
    try:
        reports = get_all_reports(report_type, limit)
        return {
            'status': 'success',
            'reports': reports,
            'count': len(reports)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching reports: {str(e)}')


@app.get('/api/mbr/reports/{report_id}')
async def get_saved_report(report_id: int):
    """Get a specific saved report with full data"""
    try:
        report = get_report_by_id(report_id)
        if not report:
            raise HTTPException(status_code=404, detail='Report not found')
        return {
            'status': 'success',
            'report': report
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching report: {str(e)}')


@app.delete('/api/mbr/reports/{report_id}')
async def delete_saved_report(report_id: int):
    """Delete a saved report"""
    try:
        success = delete_report(report_id)
        if not success:
            raise HTTPException(status_code=404, detail='Report not found')
        return {
            'status': 'success',
            'message': 'Report deleted successfully'
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error deleting report: {str(e)}')


@app.get('/api/mbr/reports-statistics')
async def get_reports_stats():
    """Get statistics about saved reports"""
    try:
        stats = get_report_statistics()
        return {
            'status': 'success',
            **stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching statistics: {str(e)}')


# -------------------------
# Email Recipients Endpoints
# -------------------------

class EmailRecipient(BaseModel):
    name: str
    email: str
    notes: Optional[str] = ''


class EmailRecipientUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class SendEmailRequest(BaseModel):
    recipient_emails: List[str]
    subject: str
    body: str
    from_date: str
    to_date: str


@app.get('/api/email-recipients')
async def get_recipients(active_only: bool = True):
    """Get all email recipients"""
    try:
        recipients = get_all_recipients(active_only)
        return {
            'status': 'success',
            'recipients': recipients,
            'count': len(recipients)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching recipients: {str(e)}')


@app.get('/api/email-recipients/statistics')
async def get_recipient_stats():
    """Get statistics about recipients"""
    try:
        stats = get_recipient_statistics()
        return {
            'status': 'success',
            **stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching statistics: {str(e)}')


@app.get('/api/email-recipients/{recipient_id}')
async def get_recipient(recipient_id: int):
    """Get single recipient by ID"""
    try:
        recipient = get_recipient_by_id(recipient_id)
        if not recipient:
            raise HTTPException(status_code=404, detail='Recipient not found')
        return {
            'status': 'success',
            'recipient': recipient
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching recipient: {str(e)}')


@app.post('/api/email-recipients')
async def create_new_recipient(recipient: EmailRecipient):
    """Create new email recipient"""
    try:
        result = create_recipient(
            recipient.name,
            recipient.email,
            recipient.notes or ''
        )
        return {
            'status': 'success',
            'message': 'Recipient created successfully',
            'recipient': result
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error creating recipient: {str(e)}')


@app.put('/api/email-recipients/{recipient_id}')
async def update_existing_recipient(recipient_id: int, recipient: EmailRecipientUpdate):
    """Update existing recipient"""
    try:
        result = update_recipient(
            recipient_id,
            recipient.name,
            recipient.email,
            recipient.notes,
            recipient.is_active
        )
        return {
            'status': 'success',
            'message': 'Recipient updated successfully',
            'recipient': result
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error updating recipient: {str(e)}')


@app.delete('/api/email-recipients/{recipient_id}')
async def delete_existing_recipient(recipient_id: int):
    """Delete recipient by ID"""
    try:
        success = delete_recipient(recipient_id)
        if not success:
            raise HTTPException(status_code=404, detail='Recipient not found')
        return {
            'status': 'success',
            'message': 'Recipient deleted successfully'
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error deleting recipient: {str(e)}')


@app.post('/api/send-report-email')
async def send_report_via_email(request: SendEmailRequest):
    """Send MBR report PDF via email"""
    try:
        # Validate dates
        from_date = datetime.strptime(request.from_date, '%Y-%m-%d')
        to_date = datetime.strptime(request.to_date, '%Y-%m-%d')

        # Fetch data
        df_us = fetch_region_data('US', DRUID_US_BROKER, request.from_date, request.to_date)
        df_eu = fetch_region_data('EU', DRUID_EU_BROKER, request.from_date, request.to_date)

        if df_us.empty and df_eu.empty:
            raise HTTPException(status_code=404, detail='No data found for the selected date range')

        # Get domain-level data
        esp_data, df_combined = aggregate_data_by_esp(df_us, df_eu)

        # Get account-level data
        df_combined_with_accounts = pd.concat([df_us, df_eu], ignore_index=True)
        df_combined_with_accounts = add_account_column(df_combined_with_accounts)

        top_accounts_by_esp = get_top_accounts_by_esp(df_combined_with_accounts, top_n=10)
        top_accounts_overall = get_top_accounts_overall(df_combined_with_accounts, top_n=10)

        # Get affiliate accounts data
        affiliate_accounts = get_affiliate_accounts_data(df_combined_with_accounts)

        account_data = {
            'esp_data': {esp: {'top10_accounts': accounts} for esp, accounts in top_accounts_by_esp.items()},
            'top10_accounts_overall': top_accounts_overall,
            'affiliate_accounts': affiliate_accounts,
        }

        # Generate PDF
        pdf_data = export_to_pdf(esp_data, df_combined, request.from_date, request.to_date, account_data)
        pdf_filename = f"mbr_deliverability_report_{request.from_date}_to_{request.to_date}.pdf"

        # Send email
        result = send_report_email(
            request.recipient_emails,
            request.subject,
            request.body,
            pdf_data,
            pdf_filename
        )

        if result['status'] == 'success':
            return result
        else:
            raise HTTPException(status_code=500, detail=result['message'])

    except ValueError as e:
        print(f"ValueError in send_report_via_email: {str(e)}")
        raise HTTPException(status_code=400, detail=f'Invalid date format: {str(e)}')
    except HTTPException:
        raise
    except Exception as e:
        print(f"Exception in send_report_via_email: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f'Error sending email: {str(e)}')


# -------------------------
# Account Info Endpoints (ESP Integration)
# -------------------------

@app.get('/api/account-info')
async def get_account_info(force_refresh: bool = False):
    """
    Get account info from all ESPs (Mailgun, Sparkpost, Sendgrid)
    Includes: domains, IPs, subaccounts, pools, status, verification

    Query params:
        force_refresh: Set to true to bypass 24-hour cache
    """
    try:
        result = get_all_account_info(force_refresh=force_refresh)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching account info: {str(e)}')


@app.post('/api/account-info/clear-cache')
async def clear_account_info_cache():
    """Clear the account info cache (force next request to fetch fresh data)"""
    try:
        clear_cache()
        return {
            'status': 'success',
            'message': 'Cache cleared successfully'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error clearing cache: {str(e)}')


# -------------------------
# SNDS (Microsoft) Endpoints
# -------------------------

@app.get('/api/snds/overview')
async def get_snds_overview_endpoint(period: str = '30day'):
    """
    Get SNDS overview statistics

    Query params:
        period: Time period (yesterday, 7day, 30day, 60day, 90day, 120day, 1year)
    """
    try:
        result = get_snds_overview(period)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching SNDS overview: {str(e)}')


@app.get('/api/snds/data')
async def get_snds_data_endpoint(
    period: str = '30day',
    view_by: str = 'ip',
    account_name: Optional[str] = None,
    ip_address: Optional[str] = None
):
    """
    Get SNDS data for specified period

    Query params:
        period: Time period (yesterday, 7day, 30day, etc.)
        view_by: Group by 'ip' or 'account'
        account_name: Filter by specific account (optional)
        ip_address: Filter by specific IP (optional)
    """
    try:
        result = get_snds_data_by_period(period, view_by, account_name, ip_address)
        return {
            'status': 'success',
            'period': period,
            'view_by': view_by,
            'total_records': len(result),
            'data': result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching SNDS data: {str(e)}')


@app.get('/api/snds/reputation-trends')
async def get_snds_reputation_trends_endpoint(period: str = '30day', group_by: str = 'ip'):
    """
    Get reputation trends over time

    Query params:
        period: Time period
        group_by: Group by 'ip' or 'account'
    """
    try:
        result = get_reputation_trends(period, group_by)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching reputation trends: {str(e)}')


@app.get('/api/snds/traffic-trends')
async def get_snds_traffic_trends_endpoint(period: str = '30day', group_by: str = 'ip'):
    """
    Get traffic volume trends over time

    Query params:
        period: Time period
        group_by: Group by 'ip' or 'account'
    """
    try:
        result = get_traffic_trends(period, group_by)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching traffic trends: {str(e)}')


@app.get('/api/snds/top-performers')
async def get_snds_top_performers_endpoint(period: str = '30day', metric: str = 'reputation', limit: int = 10):
    """
    Get top performing IPs/Accounts

    Query params:
        period: Time period
        metric: Sort by 'reputation', 'volume', or 'spam_rate'
        limit: Number of results (default: 10)
    """
    try:
        result = get_top_performers(period, metric, limit)
        return {
            'status': 'success',
            'period': period,
            'metric': metric,
            'total': len(result),
            'performers': result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching top performers: {str(e)}')


@app.get('/api/snds/problem-ips')
async def get_snds_problem_ips_endpoint(period: str = '30day', threshold: float = 50.0):
    """
    Get IPs with reputation below threshold

    Query params:
        period: Time period
        threshold: Reputation score threshold (default: 50.0)
    """
    try:
        result = get_problem_ips(period, threshold)
        return {
            'status': 'success',
            'period': period,
            'threshold': threshold,
            'total': len(result),
            'problem_ips': result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching problem IPs: {str(e)}')


@app.get('/api/snds/accounts')
async def get_snds_accounts_endpoint():
    """Get list of all accounts with SNDS data"""
    try:
        accounts = get_snds_accounts()
        return {
            'status': 'success',
            'total': len(accounts),
            'accounts': accounts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching accounts: {str(e)}')


@app.get('/api/snds/ips')
async def get_snds_ips_endpoint():
    """Get list of all IPs with SNDS data"""
    try:
        ips = get_snds_ips()
        return {
            'status': 'success',
            'total': len(ips),
            'ips': ips
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching IPs: {str(e)}')


@app.post('/api/snds/collect')
async def collect_snds_data_endpoint():
    """
    Manually trigger SNDS data collection
    Fetches latest data from Microsoft SNDS API
    """
    try:
        result = collect_and_store_snds_data()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error collecting SNDS data: {str(e)}')


# ============================================================================
# GOOGLE POSTMASTER TOOLS (GPT) ENDPOINTS
# ============================================================================

@app.get('/api/gpt/authorize')
async def get_gpt_authorization_url():
    """
    Get OAuth 2.0 authorization URL for Google Postmaster Tools
    User needs to visit this URL to grant access (one-time)
    """
    try:
        auth_url = get_authorization_url()
        return {
            'authorization_url': auth_url,
            'instructions': 'Visit this URL in your browser, authorize access, and copy the authorization code from the OAuth Playground.'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error generating auth URL: {str(e)}')


@app.post('/api/gpt/oauth-callback')
async def handle_gpt_oauth_callback(code: str):
    """
    Handle OAuth callback and exchange code for tokens
    Call this with the authorization code from OAuth Playground
    """
    try:
        tokens = exchange_code_for_tokens(code)
        return {
            'status': 'success',
            'message': 'Authorization successful! You can now collect GPT data.',
            'token_type': tokens.get('token_type'),
            'expires_in': tokens.get('expires_in')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'OAuth callback failed: {str(e)}')


@app.get('/api/gpt/auth-status')
async def get_gpt_auth_status():
    """
    Check if GPT is authorized (tokens exist)
    """
    try:
        tokens = get_gpt_tokens()
        return {
            'authorized': tokens is not None,
            'has_refresh_token': tokens.get('refresh_token') is not None if tokens else False
        }
    except Exception as e:
        return {'authorized': False, 'error': str(e)}


@app.get('/api/gpt/overview')
async def get_gpt_overview_endpoint(period: str = '30day'):
    """
    Get GPT overview statistics
    Query params:
        period: yesterday, 7day, 30day, 60day, 90day, 120day, 180day, 365day (default: 30day)
    """
    try:
        period_map = {
            'yesterday': 1,
            '7day': 7,
            '30day': 30,
            '60day': 60,
            '90day': 90,
            '120day': 120,
            '180day': 180,
            '365day': 365
        }
        days = period_map.get(period, 30)

        overview = get_gpt_overview(days)
        return overview
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching GPT overview: {str(e)}')


@app.get('/api/gpt/data')
async def get_gpt_data_endpoint(
    period: str = '30day',
    domain: Optional[str] = None
):
    """
    Get detailed GPT domain data
    Query params:
        period: 7day, 30day, 90day (default: 30day)
        domain: Filter by specific domain (optional)
    """
    try:
        period_map = {'yesterday': 1, '7day': 7, '30day': 30, '60day': 60, '90day': 90, '120day': 120, '180day': 180, '365day': 365}
        days = period_map.get(period, 30)

        data = get_gpt_domain_data(days, domain)
        return {'data': data, 'period': period}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching GPT data: {str(e)}')


@app.get('/api/gpt/domains')
async def get_gpt_domains_endpoint():
    """
    Get list of all domains being tracked in GPT
    """
    try:
        domains = get_gpt_domains_list()
        return {'domains': domains, 'total': len(domains)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching GPT domains: {str(e)}')


@app.get('/api/gpt/reputation-trends')
async def get_gpt_reputation_trends_endpoint(
    period: str = '30day',
    domain: Optional[str] = None
):
    """
    Get reputation trends over time
    Query params:
        period: 7day, 30day, 90day (default: 30day)
        domain: Filter by specific domain (optional)
    """
    try:
        period_map = {'yesterday': 1, '7day': 7, '30day': 30, '60day': 60, '90day': 90, '120day': 120, '180day': 180, '365day': 365}
        days = period_map.get(period, 30)

        trends = get_gpt_reputation_trends(days, domain)
        return trends
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching reputation trends: {str(e)}')


@app.get('/api/gpt/spam-trends')
async def get_gpt_spam_trends_endpoint(
    period: str = '30day',
    domain: Optional[str] = None
):
    """
    Get spam rate trends over time
    Query params:
        period: 7day, 30day, 90day (default: 30day)
        domain: Filter by specific domain (optional)
    """
    try:
        period_map = {'yesterday': 1, '7day': 7, '30day': 30, '60day': 60, '90day': 90, '120day': 120, '180day': 180, '365day': 365}
        days = period_map.get(period, 30)

        trends = get_gpt_spam_trends(days, domain)
        return trends
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching spam trends: {str(e)}')


@app.get('/api/gpt/auth-trends')
async def get_gpt_auth_trends_endpoint(
    period: str = '30day',
    domain: Optional[str] = None
):
    """
    Get authentication success rate trends (SPF, DKIM, DMARC)
    Query params:
        period: 7day, 30day, 90day (default: 30day)
        domain: Filter by specific domain (optional)
    """
    try:
        period_map = {'yesterday': 1, '7day': 7, '30day': 30, '60day': 60, '90day': 90, '120day': 120, '180day': 180, '365day': 365}
        days = period_map.get(period, 30)

        trends = get_gpt_auth_trends(days, domain)
        return trends
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching auth trends: {str(e)}')


@app.get('/api/gpt/reputation-changes')
async def get_gpt_reputation_changes_endpoint():
    """
    Get domains with reputation changes from yesterday
    Used for notifications
    """
    try:
        changes = get_reputation_changes()
        return {'changes': changes, 'total': len(changes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching reputation changes: {str(e)}')


@app.get('/api/gpt/overview-table')
async def get_gpt_overview_table_endpoint():
    """
    Get yesterday's overview data for all domains (for home page table)
    Shows all domains with their latest reputation metrics
    """
    try:
        data = get_yesterday_overview()
        if not data or len(data) == 0:
            # Fallback to latest data if yesterday has no data
            data = get_all_domains_latest()
        return {'domains': data, 'total': len(data), 'date': 'yesterday'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching overview table: {str(e)}')


@app.get('/api/gpt/enhanced-changes')
async def get_gpt_enhanced_changes_endpoint():
    """
    Get enhanced reputation changes (both IP and Domain)
    Compares yesterday vs day before
    """
    try:
        changes = get_enhanced_reputation_changes()
        return {'changes': changes, 'total': len(changes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching enhanced changes: {str(e)}')


@app.get('/api/gpt/domain-details')
async def get_gpt_domain_details_endpoint(domain: str, period: str = '30day'):
    """
    Get detailed metrics for a specific domain
    Used for detailed domain view

    Query params:
        domain: Domain name (required)
        period: 7day, 30day, 60day, 90day, 120day, 180day, 365day (default: 30day)
    """
    try:
        period_map = {
            'yesterday': 1,
            '7day': 7,
            '30day': 30,
            '60day': 60,
            '90day': 90,
            '120day': 120,
            '180day': 180,
            '365day': 365
        }
        days = period_map.get(period, 30)

        details = get_domain_detailed_metrics(domain, days)

        if not details:
            raise HTTPException(status_code=404, detail=f'No data found for domain: {domain}')

        return details
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error fetching domain details: {str(e)}')


@app.post('/api/gpt/collect')
async def collect_gpt_data_endpoint(days_back: int = 120, background_tasks: BackgroundTasks = None):
    """
    Manually trigger GPT data collection (runs in background)
    Collects last 120 days of data from Google (their API limit)

    Query params:
        days_back: Number of days to fetch (max 120, default: 120)

    Returns immediately with status, collection continues in background
    """
    try:
        if days_back > 120:
            days_back = 120

        # Run collection in background thread to avoid blocking the server
        if background_tasks:
            background_tasks.add_task(collect_and_store_gpt_data, days_back)
            return {
                'status': 'started',
                'message': f'Collection started in background for last {days_back} days',
                'note': 'Check /api/gpt/domains to see updated domain list'
            }
        else:
            # Fallback to synchronous if no background_tasks available
            result = collect_and_store_gpt_data(days_back)
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error collecting GPT data: {str(e)}')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8001)
