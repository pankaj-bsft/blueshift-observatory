import io
import pandas as pd
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_CENTER
from typing import Dict, List


class NumberedCanvas(canvas.Canvas):
    """Custom canvas to add page numbers"""
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        self.setFont('Helvetica', 9)
        self.drawRightString(
            7.5 * inch, 0.5 * inch,
            f'Page {self._pageNumber} of {page_count}'
        )


def export_to_excel(esp_data: Dict, df_combined: pd.DataFrame, from_date: str, to_date: str) -> bytes:
    """Export all data to Excel file"""
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Executive Summary
        overall_summary = aggregate_region_summary_for_export(df_combined)
        if overall_summary:
            summary_df = pd.DataFrame([overall_summary])
            summary_df.to_excel(writer, sheet_name='Executive Summary', index=False)

        # ESP-wise data
        for esp, data in esp_data.items():
            if data['combined_summary']:
                # Summary sheet
                esp_summary = []
                if data['us_summary']:
                    us_row = data['us_summary'].copy()
                    us_row['Region'] = 'US'
                    esp_summary.append(us_row)
                if data['eu_summary']:
                    eu_row = data['eu_summary'].copy()
                    eu_row['Region'] = 'EU'
                    esp_summary.append(eu_row)
                combined_row = data['combined_summary'].copy()
                combined_row['Region'] = 'Total'
                esp_summary.append(combined_row)

                esp_df = pd.DataFrame(esp_summary)
                esp_df.to_excel(writer, sheet_name=f'{esp} Summary', index=False)

                # Top 10 domains
                if data['top10_domains']:
                    top10_df = pd.DataFrame(data['top10_domains'])
                    top10_df.to_excel(writer, sheet_name=f'{esp} Top 10', index=False)

        # Overall Top 10
        overall_top10 = get_top10_overall_for_export(df_combined)
        if not overall_top10.empty:
            overall_top10.to_excel(writer, sheet_name='Top 10 Overall', index=False)

    output.seek(0)
    return output.getvalue()


def aggregate_region_summary_for_export(df: pd.DataFrame) -> Dict:
    """Helper function for export - same as druid_service but importable"""
    from druid_service import aggregate_region_summary
    return aggregate_region_summary(df)


def get_top10_overall_for_export(df: pd.DataFrame) -> pd.DataFrame:
    """Helper function for export - same as druid_service but importable"""
    from druid_service import get_top10_overall
    return get_top10_overall(df)


def create_summary_table(summary_data: Dict, title: str):
    """Create a formatted table for summary metrics"""
    if not summary_data:
        return []

    data = [
        ['Metric', 'Value'],
        ['Total Sent', f"{summary_data['Total_Sent']:,}"],
        ['Total Delivered', f"{summary_data['Total_Delivered']:,}"],
        ['Delivery Rate', f"{summary_data['Delivery_Rate_%']}%"],
        ['Total Bounces', f"{summary_data['Total_Bounces']:,}"],
        ['Bounce Rate', f"{summary_data['Bounce_Rate_%']}%"],
        ['Total Spam Reports', f"{summary_data['Total_Spam_Reports']:,}"],
        ['Spam Rate', f"{summary_data['Spam_Rate_%']}%"],
        ['Total Unsubscribes', f"{summary_data['Total_Unsubscribes']:,}"],
        ['Unsubscribe Rate', f"{summary_data['Unsub_Rate_%']}%"],
        ['Total Unique Opens', f"{summary_data['Total_Unique_Opens']:,}"],
        ['Open Rate', f"{summary_data['Open_Rate_%']}%"],
        ['Total Unique Clicks', f"{summary_data['Total_Unique_Clicks']:,}"],
        ['Click Rate', f"{summary_data['Click_Rate_%']}%"],
        ['Click-to-Open Rate', f"{summary_data['CTOR_%']}%"],
    ]

    table = Table(data, colWidths=[3*inch, 2*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0C3C78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=colors.HexColor('#0C3C78'),
        spaceAfter=6
    )

    return [Paragraph(f'<b>{title}</b>', title_style), table, Spacer(1, 0.3*inch)]


def create_esp_summary_table(esp_info: Dict, esp_name: str):
    """Create a formatted table for ESP summary with regional breakdown"""
    if not esp_info or not esp_info.get('combined_summary'):
        return []

    # Header row
    data = [['Region', 'Sent', 'Delivered', 'Delivery %', 'Bounce %', 'Open %', 'Click %', 'CTOR %', 'Health Score']]

    # US row
    if esp_info.get('us_summary'):
        us = esp_info['us_summary']
        health_score = us.get('Health_Score', 0)
        health_rating = us.get('Health_Rating', 'N/A')
        data.append([
            'US',
            f"{us['Total_Sent']:,}",
            f"{us['Total_Delivered']:,}",
            f"{us['Delivery_Rate_%']:.2f}%",
            f"{us['Bounce_Rate_%']:.2f}%",
            f"{us['Open_Rate_%']:.2f}%",
            f"{us['Click_Rate_%']:.2f}%",
            f"{us['CTOR_%']:.2f}%",
            f"{health_score} ({health_rating})",
        ])

    # EU row
    if esp_info.get('eu_summary'):
        eu = esp_info['eu_summary']
        health_score = eu.get('Health_Score', 0)
        health_rating = eu.get('Health_Rating', 'N/A')
        data.append([
            'EU',
            f"{eu['Total_Sent']:,}",
            f"{eu['Total_Delivered']:,}",
            f"{eu['Delivery_Rate_%']:.2f}%",
            f"{eu['Bounce_Rate_%']:.2f}%",
            f"{eu['Open_Rate_%']:.2f}%",
            f"{eu['Click_Rate_%']:.2f}%",
            f"{eu['CTOR_%']:.2f}%",
            f"{health_score} ({health_rating})",
        ])

    # Combined row (bold)
    combined = esp_info['combined_summary']
    health_score = combined.get('Health_Score', 0)
    health_rating = combined.get('Health_Rating', 'N/A')
    data.append([
        'Total',
        f"{combined['Total_Sent']:,}",
        f"{combined['Total_Delivered']:,}",
        f"{combined['Delivery_Rate_%']:.2f}%",
        f"{combined['Bounce_Rate_%']:.2f}%",
        f"{combined['Open_Rate_%']:.2f}%",
        f"{combined['Click_Rate_%']:.2f}%",
        f"{combined['CTOR_%']:.2f}%",
        f"{health_score} ({health_rating})",
    ])

    table = Table(data, colWidths=[0.7*inch, 0.9*inch, 0.9*inch, 0.75*inch, 0.7*inch, 0.65*inch, 0.65*inch, 0.65*inch, 1.0*inch])

    style_commands = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0C3C78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]

    # Make Total row bold
    if len(data) > 1:
        total_row_idx = len(data) - 1
        style_commands.append(('FONTNAME', (0, total_row_idx), (-1, total_row_idx), 'Helvetica-Bold'))
        style_commands.append(('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), colors.HexColor('#E8F4F8')))

    # Color code health score column based on rating
    health_col_idx = len(data[0]) - 1  # Last column
    for row_idx in range(1, len(data)):
        health_text = data[row_idx][health_col_idx]
        if 'Excellent' in health_text:
            style_commands.append(('TEXTCOLOR', (health_col_idx, row_idx), (health_col_idx, row_idx), colors.HexColor('#0F9D58')))
            style_commands.append(('FONTNAME', (health_col_idx, row_idx), (health_col_idx, row_idx), 'Helvetica-Bold'))
        elif 'Good' in health_text:
            style_commands.append(('TEXTCOLOR', (health_col_idx, row_idx), (health_col_idx, row_idx), colors.HexColor('#34A853')))
        elif 'Fair' in health_text:
            style_commands.append(('TEXTCOLOR', (health_col_idx, row_idx), (health_col_idx, row_idx), colors.HexColor('#FBBC04')))
        elif 'Poor' in health_text:
            style_commands.append(('TEXTCOLOR', (health_col_idx, row_idx), (health_col_idx, row_idx), colors.HexColor('#EA4335')))
        elif 'Critical' in health_text:
            style_commands.append(('TEXTCOLOR', (health_col_idx, row_idx), (health_col_idx, row_idx), colors.HexColor('#D32F2F')))
            style_commands.append(('FONTNAME', (health_col_idx, row_idx), (health_col_idx, row_idx), 'Helvetica-Bold'))

    table.setStyle(TableStyle(style_commands))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ESPSummaryTitle',
        parent=styles['Heading3'],
        fontSize=13,
        textColor=colors.HexColor('#0C3C78'),
        spaceAfter=8,
        spaceBefore=10
    )

    return [Paragraph(f'<b>{esp_name} - Summary Metrics</b>', title_style), table, Spacer(1, 0.15*inch)]


def create_domain_table(domains: List[Dict], title: str):
    """Create a formatted table for domain data"""
    if not domains:
        return []

    # Header row
    data = [['Domain', 'Sent', 'Delivered', 'Delivery %', 'Bounce %', 'Open %', 'Click %']]

    # Data rows
    for domain in domains[:10]:  # Top 10
        data.append([
            domain.get('From_domain', '')[:30],  # Truncate long domains
            f"{domain.get('Sent', 0):,}",
            f"{domain.get('Delivered', 0):,}",
            f"{domain.get('Delivery_Rate_%', 0):.2f}%",
            f"{domain.get('Bounce_Rate_%', 0):.2f}%",
            f"{domain.get('Open_Rate_%', 0):.2f}%",
            f"{domain.get('Click_Rate_%', 0):.2f}%",
        ])

    table = Table(data, colWidths=[2*inch, 0.9*inch, 0.9*inch, 0.8*inch, 0.8*inch, 0.7*inch, 0.7*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0C3C78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=colors.HexColor('#0C3C78'),
        spaceAfter=6
    )

    return [Paragraph(f'<b>{title}</b>', title_style), table, Spacer(1, 0.3*inch)]


def create_account_table(accounts: List[Dict], title: str):
    """Create a formatted table for account data"""
    if not accounts:
        return []

    # Header row
    data = [['Rank', 'Account Name', 'Sent', 'Delivered', 'Delivery %', 'Open %', 'Click %']]

    # Data rows
    for account in accounts[:10]:  # Top 10
        data.append([
            str(account.get('Rank', '')),
            account.get('Account', '')[:25],  # Truncate long names
            f"{account.get('Sent', 0):,}",
            f"{account.get('Delivered', 0):,}",
            f"{account.get('Delivery_Rate_%', 0):.2f}%",
            f"{account.get('Open_Rate_%', 0):.2f}%",
            f"{account.get('Click_Rate_%', 0):.2f}%",
        ])

    table = Table(data, colWidths=[0.5*inch, 2*inch, 1*inch, 1*inch, 0.9*inch, 0.8*inch, 0.8*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0C3C78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=colors.HexColor('#0C3C78'),
        spaceAfter=6
    )

    return [Paragraph(f'<b>{title}</b>', title_style), table, Spacer(1, 0.3*inch)]


def export_to_pdf(esp_data: Dict, df_combined: pd.DataFrame, from_date: str, to_date: str,
                  account_data: Dict = None) -> bytes:
    """Export data to PDF file with all domain and account tables"""
    output = io.BytesIO()

    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch
    )

    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontSize=24,
        textColor=colors.HexColor('#0C3C78'),
        alignment=TA_CENTER,
        spaceAfter=12
    )

    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=12,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=20
    )

    section_style = ParagraphStyle(
        'Section',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=colors.HexColor('#0C3C78'),
        spaceAfter=12,
        spaceBefore=12
    )

    # Title page
    story.append(Spacer(1, 1.5*inch))
    story.append(Paragraph('Monthly Business Review (MBR)', title_style))
    story.append(Paragraph('Deliverability Report', title_style))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(f'Report Period: {from_date} to {to_date}', subtitle_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}", subtitle_style))
    story.append(PageBreak())

    # Executive Summary
    from druid_service import aggregate_region_summary, get_top10_overall
    overall_summary = aggregate_region_summary(df_combined[df_combined['Delivered'] > 0])
    if overall_summary:
        story.extend(create_summary_table(overall_summary, 'Executive Summary - All ESPs'))

    story.append(PageBreak())

    # ===== ESP-WISE METRICS =====
    story.append(Paragraph('<b>ESP-WISE METRICS</b>', section_style))
    story.append(Spacer(1, 0.2*inch))

    # ESP summary tables (US/EU/Combined breakdown)
    for esp_name, esp_info in esp_data.items():
        if esp_info.get('combined_summary'):
            story.extend(create_esp_summary_table(esp_info, esp_name))

    story.append(PageBreak())

    # ===== DOMAIN-LEVEL DATA =====
    story.append(Paragraph('<b>DOMAIN-LEVEL ANALYSIS</b>', section_style))
    story.append(Spacer(1, 0.2*inch))

    # ESP-specific domain tables
    for esp_name, esp_info in esp_data.items():
        if esp_info.get('top10_domains'):
            story.extend(create_domain_table(
                esp_info['top10_domains'],
                f'{esp_name} - Top 10 Domains by Send Volume'
            ))

    # Overall Top 10 Domains
    top10_overall = get_top10_overall(df_combined)
    if not top10_overall.empty:
        story.extend(create_domain_table(
            top10_overall.to_dict('records'),
            'Top 10 Domains Across All ESPs'
        ))

    # ===== ACCOUNT-LEVEL DATA =====
    if account_data:
        story.append(PageBreak())
        story.append(Paragraph('<b>ACCOUNT-LEVEL ANALYSIS</b>', section_style))
        story.append(Spacer(1, 0.2*inch))

        # Overall Top 10 Accounts
        if account_data.get('top10_accounts_overall'):
            story.extend(create_account_table(
                account_data['top10_accounts_overall'],
                'Top 10 Accounts (Across All ESPs)'
            ))

        # ESP-specific account tables
        if account_data.get('esp_data'):
            for esp_name, esp_info in account_data['esp_data'].items():
                if esp_info.get('top10_accounts'):
                    story.extend(create_account_table(
                        esp_info['top10_accounts'],
                        f'{esp_name} - Top 10 Accounts by Send Volume'
                    ))

        # ===== AFFILIATE ACCOUNTS SECTION =====
        if account_data.get('affiliate_accounts'):
            story.append(PageBreak())
            story.append(Paragraph('<b>AFFILIATE ACCOUNTS ANALYSIS</b>', section_style))
            story.append(Spacer(1, 0.2*inch))
            story.extend(create_account_table(
                account_data['affiliate_accounts'],
                'Affiliate Accounts (IsAffiliate = Yes)'
            ))

    doc.build(story, canvasmaker=NumberedCanvas)

    output.seek(0)
    return output.getvalue()
