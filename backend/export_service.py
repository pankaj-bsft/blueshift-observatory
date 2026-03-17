import io
import pandas as pd
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_CENTER
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.widgets.markers import makeMarker
from pdf_html_service import build_mbr_html_report, export_to_pdf_html
from typing import Dict, List
from mbr_storage_service import get_monthly_sent_by_esp


BRAND = {
    "navy": colors.HexColor("#0C3C78"),
    "slate": colors.HexColor("#334155"),
    "slate_light": colors.HexColor("#E2E8F0"),
    "table_header": colors.HexColor("#0F172A"),
    "row_alt": colors.HexColor("#F8FAFC"),
    "row_alt_2": colors.white,
    "accent": colors.HexColor("#2563EB"),
}


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


def create_section_header(title: str):
    """Create a styled section header bar."""
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'SectionHeaderText',
        parent=styles['Heading2'],
        fontSize=13,
        textColor=colors.whitesmoke,
        spaceAfter=0,
        spaceBefore=0
    )
    table = Table([[Paragraph(f'<b>{title}</b>', title_style)]], colWidths=[7.0 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND["table_header"]),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BOX', (0, 0), (-1, -1), 0.5, BRAND["slate_light"]),
    ]))
    return [table, Spacer(1, 0.15 * inch)]


def _format_label_value(value: float, suffix: str = "") -> str:
    try:
        return f"{float(value):,.1f}{suffix}" if suffix else f"{float(value):,.1f}"
    except Exception:
        return "0.0" + suffix


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
        ('BACKGROUND', (0, 0), (-1, 0), BRAND["table_header"]),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.4, BRAND["slate_light"]),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND["row_alt_2"], BRAND["row_alt"]]),
    ]))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=BRAND["navy"],
        spaceAfter=6
    )

    return [KeepTogether([Paragraph(f'<b>{title}</b>', title_style), table, Spacer(1, 0.25*inch)])]


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
        ('BACKGROUND', (0, 0), (-1, 0), BRAND["table_header"]),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.4, BRAND["slate_light"]),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND["row_alt_2"], BRAND["row_alt"]]),
    ]

    # Make Total row bold
    if len(data) > 1:
        total_row_idx = len(data) - 1
        style_commands.append(('FONTNAME', (0, total_row_idx), (-1, total_row_idx), 'Helvetica-Bold'))
        style_commands.append(('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), colors.HexColor('#E7F0FE')))

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
        textColor=BRAND["navy"],
        spaceAfter=8,
        spaceBefore=10
    )

    return [KeepTogether([Paragraph(f'<b>{esp_name} - Summary Metrics</b>', title_style), table, Spacer(1, 0.15*inch)])]


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
        ('BACKGROUND', (0, 0), (-1, 0), BRAND["table_header"]),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.4, BRAND["slate_light"]),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND["row_alt_2"], BRAND["row_alt"]]),
    ]))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=BRAND["navy"],
        spaceAfter=6
    )

    return [KeepTogether([Paragraph(f'<b>{title}</b>', title_style), table, Spacer(1, 0.3*inch)])]


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
        ('BACKGROUND', (0, 0), (-1, 0), BRAND["table_header"]),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.4, BRAND["slate_light"]),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND["row_alt_2"], BRAND["row_alt"]]),
    ]))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=BRAND["navy"],
        spaceAfter=6
    )

    return [KeepTogether([Paragraph(f'<b>{title}</b>', title_style), table, Spacer(1, 0.3*inch)])]


def _chart_title(title: str) -> Paragraph:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ChartTitle',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=colors.HexColor('#0C3C78'),
        spaceAfter=6,
        spaceBefore=8
    )
    return Paragraph(f'<b>{title}</b>', title_style)


def _build_legend(color_labels: List[tuple], x: int, y: int) -> Legend:
    legend = Legend()
    legend.x = x
    legend.y = y
    legend.boxAnchor = 'w'
    legend.columnMaximum = len(color_labels)
    legend.fontName = 'Helvetica'
    legend.fontSize = 8
    legend.strokeWidth = 0
    legend.alignment = 'right'
    legend.colorNamePairs = [(c, l) for c, l in color_labels]
    return legend


def create_esp_comparison_chart(esp_data: Dict):
    """Grouped bar chart: Delivery/Bounce/Open/Click/CTOR by ESP"""
    if not esp_data:
        return []

    metrics = [
        ('Delivery_Rate_%', 'Delivery %'),
        ('Bounce_Rate_%', 'Bounce %'),
        ('Open_Rate_%', 'Open %'),
        ('Click_Rate_%', 'Click %'),
        ('CTOR_%', 'CTOR %'),
    ]

    esp_names = []
    series = [[] for _ in metrics]

    for esp_name, esp_info in esp_data.items():
        combined = esp_info.get('combined_summary')
        if not combined:
            continue
        esp_names.append(esp_name)
        for idx, (key, _) in enumerate(metrics):
            series[idx].append(float(combined.get(key, 0)))

    if not esp_names:
        return []

    width = 6.8 * inch
    height = 2.8 * inch
    drawing = Drawing(width, height)

    chart = VerticalBarChart()
    chart.x = 45
    chart.y = 35
    chart.height = height - 70
    chart.width = width - 90
    chart.data = series
    chart.categoryAxis.categoryNames = esp_names
    chart.categoryAxis.labels.boxAnchor = 'ne'
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 8
    chart.valueAxis.labelTextFormat = '%d%%'
    chart.valueAxis.strokeColor = colors.HexColor('#CBD5E1')
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E2E8F0')
    chart.barWidth = 6
    chart.groupSpacing = 8
    chart.barSpacing = 2

    chart_colors = [
        colors.HexColor('#0C3C78'),
        colors.HexColor('#EA4335'),
        colors.HexColor('#34A853'),
        colors.HexColor('#1A73E8'),
        colors.HexColor('#FBBC04'),
    ]
    for i, color in enumerate(chart_colors):
        chart.bars[i].fillColor = color
        chart.bars[i].strokeColor = colors.white
        chart.bars[i].strokeWidth = 0.3

    chart.barLabels.nudge = 8
    chart.barLabels.fontName = 'Helvetica-Bold'
    chart.barLabels.fontSize = 8
    chart.barLabels.fillColor = colors.HexColor('#0F172A')
    chart.barLabelFormat = lambda v: _format_label_value(v, '%')

    drawing.add(chart)

    legend = _build_legend(
        list(zip(chart_colors, [label for _, label in metrics])),
        x=width - 90,
        y=height - 10
    )
    drawing.add(legend)

    return [KeepTogether([_chart_title('ESP Comparison (Rates %)'), drawing, Spacer(1, 0.15*inch)])]


def create_volume_share_chart(esp_data: Dict):
    """Pie chart: Share of Total Sent by ESP"""
    if not esp_data:
        return []

    labels = []
    values = []
    colors_list = []
    palette = [
        colors.HexColor('#38BDF8'),  # sky
        colors.HexColor('#0EA5E9'),  # blue
        colors.HexColor('#22D3EE'),  # cyan
        colors.HexColor('#60A5FA'),  # light blue
        colors.HexColor('#93C5FD'),  # pale blue
    ]

    for idx, (esp_name, esp_info) in enumerate(esp_data.items()):
        combined = esp_info.get('combined_summary')
        if not combined:
            continue
        sent = float(combined.get('Total_Sent', 0))
        if sent <= 0:
            continue
        labels.append(esp_name)
        values.append(sent)
        colors_list.append(palette[idx % len(palette)])

    if not values:
        return []

    width = 6.8 * inch
    height = 3.0 * inch
    drawing = Drawing(width, height)

    total_sent = sum(values)
    label_strings = []
    for name, val in zip(labels, values):
        pct = (val / total_sent * 100) if total_sent else 0
        label_strings.append(f'{name} ({pct:.1f}%)')

    pie = Pie()
    pie.x = (width - 220) / 2
    pie.y = 0
    pie.width = 220
    pie.height = 220
    pie.data = values
    pie.labels = label_strings
    pie.slices.strokeWidth = 0.3
    pie.slices.strokeColor = colors.white
    pie.simpleLabels = 0
    pie.sideLabels = 1
    pie.sideLabelsOffset = 0.12

    for i, c in enumerate(colors_list):
        pie.slices[i].fillColor = c

    drawing.add(pie)

    return [KeepTogether([_chart_title('Share of Total Sent by ESP'), Spacer(1, 0.1 * inch), drawing, Spacer(1, 0.15*inch)])]


def create_sent_by_months_chart(monthly_data: Dict):
    """Line chart: Sent by Months for Sparkpost, Mailgun, Sendgrid"""
    if not monthly_data:
        return []

    labels = monthly_data.get('labels', [])
    series = monthly_data.get('series', {})

    sp = series.get('Sparkpost', [])
    mg = series.get('Mailgun', [])
    sg = series.get('Sendgrid', [])

    if not labels or not (sp or mg or sg):
        return []

    max_points = max(len(sp), len(mg), len(sg))
    if max_points <= 1:
        return []

    width = 6.8 * inch
    height = 3.2 * inch
    drawing = Drawing(width, height)

    chart = LinePlot()
    chart.x = 50
    chart.y = 35
    chart.height = height - 70
    chart.width = width - 100

    x_vals = list(range(len(labels)))
    chart.data = [
        list(zip(x_vals, sp)),
        list(zip(x_vals, mg)),
        list(zip(x_vals, sg))
    ]

    chart.xValueAxis.valueMin = 0
    chart.xValueAxis.valueMax = max(len(labels) - 1, 0)
    chart.xValueAxis.valueSteps = x_vals
    chart.xValueAxis.labelTextFormat = lambda v: labels[int(v)] if 0 <= int(v) < len(labels) else ''
    chart.xValueAxis.labels.fontName = 'Helvetica'
    chart.xValueAxis.labels.fontSize = 8

    max_val = max([max(s) if s else 0 for s in [sp, mg, sg]])
    chart.yValueAxis.valueMin = 0
    chart.yValueAxis.valueMax = max_val * 1.1 if max_val > 0 else 1
    chart.yValueAxis.valueSteps = [chart.yValueAxis.valueMax * i / 4 for i in range(5)]
    chart.yValueAxis.labels.fontName = 'Helvetica'
    chart.yValueAxis.labels.fontSize = 8
    chart.yValueAxis.labelTextFormat = lambda v: _format_short_number(v)
    chart.yValueAxis.strokeColor = colors.HexColor('#CBD5E1')
    chart.yValueAxis.gridStrokeColor = colors.HexColor('#E2E8F0')

    colors_list = [
        colors.HexColor('#3B82F6'),  # Sparkpost
        colors.HexColor('#EF4444'),  # Mailgun
        colors.HexColor('#F59E0B')   # Sendgrid
    ]
    for idx, color in enumerate(colors_list):
        chart.lines[idx].strokeColor = color
        chart.lines[idx].strokeWidth = 1.8
        chart.lines[idx].symbol = makeMarker('Circle')
        chart.lines[idx].symbol.size = 4

    drawing.add(chart)

    legend = _build_legend(
        [(colors_list[0], 'Sparkpost'), (colors_list[1], 'Mailgun'), (colors_list[2], 'Sendgrid')],
        x=width - 110,
        y=height - 10
    )
    drawing.add(legend)

    return [KeepTogether([_chart_title('Sparkpost, Mailgun and Sendgrid'), drawing, Spacer(1, 0.1*inch),
                          Paragraph('Sent by Months', ParagraphStyle('ChartSubtitle', parent=getSampleStyleSheet()['Normal'], fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor('#334155'))),
                          Spacer(1, 0.15*inch)])]


def create_regional_split_charts(esp_data: Dict):
    """Grouped bar charts: US vs EU Delivery% and Bounce% by ESP"""
    if not esp_data:
        return []

    esp_names = []
    delivery_us = []
    delivery_eu = []
    bounce_us = []
    bounce_eu = []

    for esp_name, esp_info in esp_data.items():
        us = esp_info.get('us_summary')
        eu = esp_info.get('eu_summary')
        if not us and not eu:
            continue
        esp_names.append(esp_name)
        delivery_us.append(float(us.get('Delivery_Rate_%', 0)) if us else 0)
        delivery_eu.append(float(eu.get('Delivery_Rate_%', 0)) if eu else 0)
        bounce_us.append(float(us.get('Bounce_Rate_%', 0)) if us else 0)
        bounce_eu.append(float(eu.get('Bounce_Rate_%', 0)) if eu else 0)

    if not esp_names:
        return []

    def _build_chart(title, series_a, series_b, max_val=100):
        width = 6.8 * inch
        height = 2.6 * inch
        drawing = Drawing(width, height)

        chart = VerticalBarChart()
        chart.x = 45
        chart.y = 35
        chart.height = height - 70
        chart.width = width - 90
        chart.data = [series_a, series_b]
        chart.categoryAxis.categoryNames = esp_names
        chart.categoryAxis.labels.boxAnchor = 'ne'
        chart.categoryAxis.labels.angle = 30
        chart.categoryAxis.labels.fontSize = 9
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = max_val
        chart.valueAxis.valueStep = 20 if max_val == 100 else max(1, int(max_val / 5))
        chart.valueAxis.labels.fontName = 'Helvetica'
        chart.valueAxis.labels.fontSize = 8
        chart.valueAxis.labelTextFormat = '%d%%'
        chart.valueAxis.strokeColor = colors.HexColor('#CBD5E1')
        chart.valueAxis.gridStrokeColor = colors.HexColor('#E2E8F0')
        chart.barWidth = 10
        chart.groupSpacing = 10
        chart.barSpacing = 2

        us_color = colors.HexColor('#38BDF8')
        eu_color = colors.HexColor('#22D3EE')
        chart.bars[0].fillColor = us_color
        chart.bars[1].fillColor = eu_color
        chart.bars[0].strokeColor = colors.white
        chart.bars[1].strokeColor = colors.white
        chart.bars[0].strokeWidth = 0.3
        chart.bars[1].strokeWidth = 0.3

        chart.barLabels.nudge = 8
        chart.barLabels.fontName = 'Helvetica-Bold'
        chart.barLabels.fontSize = 8
        chart.barLabels.fillColor = colors.HexColor('#0F172A')
        chart.barLabelFormat = lambda v: _format_label_value(v, '%')

        drawing.add(chart)
        legend = _build_legend([(us_color, 'US'), (eu_color, 'EU')], x=width - 90, y=height - 10)
        drawing.add(legend)
        return [KeepTogether([_chart_title(title), drawing, Spacer(1, 0.1*inch)])]

    content = []
    content.extend(_build_chart('Regional Split: Delivery % (US vs EU)', delivery_us, delivery_eu))
    return content


def _format_short_number(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        return "0"
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:.0f}"


def create_top10_domains_chart(domains: List[Dict], title: str):
    """Horizontal bar chart for top domains by sent volume"""
    if not domains:
        return []

    labels = []
    values = []
    for domain in domains[:10]:
        labels.append(domain.get('From_domain', '')[:25])
        values.append(float(domain.get('Sent', 0)))

    if not values:
        return []

    max_val = max(values)
    width = 6.8 * inch
    height = 3.2 * inch
    drawing = Drawing(width, height)

    chart = HorizontalBarChart()
    chart.x = 120
    chart.y = 25
    chart.height = height - 60
    chart.width = width - 150
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.boxAnchor = 'e'
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max_val * 1.1 if max_val > 0 else 1
    chart.valueAxis.valueStep = max(1, int(max_val / 4)) if max_val > 0 else 1
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 8
    chart.valueAxis.labelTextFormat = lambda v: _format_short_number(v)
    chart.valueAxis.strokeColor = colors.HexColor('#CBD5E1')
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E2E8F0')
    chart.bars[0].fillColor = colors.HexColor('#38BDF8')
    chart.bars[0].strokeColor = colors.white
    chart.bars[0].strokeWidth = 0.3
    chart.barSpacing = 3

    chart.barLabels.visible = True
    chart.barLabels.nudge = 6
    chart.barLabels.fontName = 'Helvetica-Bold'
    chart.barLabels.fontSize = 8
    chart.barLabels.fillColor = colors.HexColor('#0F172A')
    chart.barLabels.boxAnchor = 'w'
    chart.barLabelFormat = lambda v: _format_short_number(v)

    drawing.add(chart)
    return [KeepTogether([_chart_title(title), drawing, Spacer(1, 0.2*inch)])]


def create_top10_accounts_chart(accounts: List[Dict], title: str):
    """Horizontal bar chart for top accounts by sent volume"""
    if not accounts:
        return []

    labels = []
    values = []
    for account in accounts[:10]:
        labels.append(account.get('Account', '')[:25])
        values.append(float(account.get('Sent', 0)))

    if not values:
        return []

    max_val = max(values)
    width = 6.8 * inch
    height = 3.2 * inch
    drawing = Drawing(width, height)

    chart = HorizontalBarChart()
    chart.x = 140
    chart.y = 25
    chart.height = height - 60
    chart.width = width - 170
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.boxAnchor = 'e'
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max_val * 1.1 if max_val > 0 else 1
    chart.valueAxis.valueStep = max(1, int(max_val / 4)) if max_val > 0 else 1
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 8
    chart.valueAxis.labelTextFormat = lambda v: _format_short_number(v)
    chart.valueAxis.strokeColor = colors.HexColor('#CBD5E1')
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E2E8F0')
    chart.bars[0].fillColor = colors.HexColor('#38BDF8')
    chart.bars[0].strokeColor = colors.white
    chart.bars[0].strokeWidth = 0.3
    chart.barSpacing = 3

    chart.barLabels.visible = True
    chart.barLabels.nudge = 6
    chart.barLabels.fontName = 'Helvetica-Bold'
    chart.barLabels.fontSize = 8
    chart.barLabels.fillColor = colors.HexColor('#0F172A')
    chart.barLabels.boxAnchor = 'w'
    chart.barLabelFormat = lambda v: _format_short_number(v)

    drawing.add(chart)
    return [KeepTogether([_chart_title(title), drawing, Spacer(1, 0.2*inch)])]


def create_health_score_chart(esp_data: Dict):
    """Bar chart for Health Score by ESP"""
    if not esp_data:
        return []

    esp_names = []
    scores = []
    for esp_name, esp_info in esp_data.items():
        combined = esp_info.get('combined_summary')
        if not combined:
            continue
        esp_names.append(esp_name)
        scores.append(float(combined.get('Health_Score', 0)))

    if not esp_names:
        return []

    width = 6.2 * inch
    height = 2.4 * inch
    drawing = Drawing(width, height)

    chart = VerticalBarChart()
    chart.x = 45
    chart.y = 30
    chart.height = height - 60
    chart.width = width - 90
    chart.data = [scores]
    chart.categoryAxis.categoryNames = esp_names
    chart.categoryAxis.labels.boxAnchor = 'ne'
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.fontSize = 9
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 8
    chart.valueAxis.labelTextFormat = '%d'
    chart.valueAxis.strokeColor = colors.HexColor('#CBD5E1')
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E2E8F0')
    chart.barWidth = 18
    chart.barSpacing = 6
    chart.bars[0].fillColor = colors.HexColor('#38BDF8')
    chart.bars[0].strokeColor = colors.white
    chart.bars[0].strokeWidth = 0.3

    chart.barLabels.nudge = 8
    chart.barLabels.fontName = 'Helvetica-Bold'
    chart.barLabels.fontSize = 8
    chart.barLabels.fillColor = colors.HexColor('#0F172A')
    chart.barLabelFormat = lambda v: _format_label_value(v)

    drawing.add(chart)
    return [KeepTogether([_chart_title('Health Score by ESP'), drawing, Spacer(1, 0.15*inch)])]


def export_to_pdf_reportlab(esp_data: Dict, df_combined: pd.DataFrame, from_date: str, to_date: str,
                            account_data: Dict = None) -> bytes:
    """Export data to PDF file with all domain and account tables (ReportLab fallback)"""
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
        textColor=BRAND["navy"],
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
        textColor=BRAND["navy"],
        spaceAfter=8,
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

    # Visualizations - Executive Summary
    story.extend(create_esp_comparison_chart(esp_data))
    story.extend(create_volume_share_chart(esp_data))
    try:
        monthly_data = get_monthly_sent_by_esp(12)
        story.extend(create_sent_by_months_chart(monthly_data))
    except Exception:
        pass
    story.extend(create_health_score_chart(esp_data))
    story.extend(create_regional_split_charts(esp_data))

    story.append(PageBreak())

    # ===== ESP-WISE METRICS =====
    story.extend(create_section_header('ESP-WISE METRICS'))

    # ESP summary tables (US/EU/Combined breakdown)
    for esp_name, esp_info in esp_data.items():
        if esp_info.get('combined_summary'):
            story.extend(create_esp_summary_table(esp_info, esp_name))

    story.append(PageBreak())

    # ===== DOMAIN-LEVEL DATA =====
    story.extend(create_section_header('DOMAIN-LEVEL ANALYSIS'))

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
        story.extend(create_top10_domains_chart(
            top10_overall.to_dict('records'),
            'Top 10 Domains by Send Volume (All ESPs)'
        ))
        story.extend(create_domain_table(
            top10_overall.to_dict('records'),
            'Top 10 Domains Across All ESPs'
        ))

    # ===== ACCOUNT-LEVEL DATA =====
    if account_data:
        story.append(PageBreak())
        story.extend(create_section_header('ACCOUNT-LEVEL ANALYSIS'))

        # Overall Top 10 Accounts
        if account_data.get('top10_accounts_overall'):
            story.extend(create_top10_accounts_chart(
                account_data['top10_accounts_overall'],
                'Top 10 Accounts by Send Volume (All ESPs)'
            ))
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
            story.extend(create_section_header('AFFILIATE ACCOUNTS ANALYSIS'))
            story.extend(create_account_table(
                account_data['affiliate_accounts'],
                'Affiliate Accounts (IsAffiliate = Yes)'
            ))

    doc.build(story, canvasmaker=NumberedCanvas)

    output.seek(0)
    return output.getvalue()


async def export_to_pdf(esp_data: Dict, df_combined: pd.DataFrame, from_date: str, to_date: str,
                        account_data: Dict = None) -> bytes:
    """Export data to PDF file (default ReportLab). Set USE_HTML_PDF=1 to use HTML rendering."""
    from druid_service import aggregate_region_summary, get_top10_overall
    import os
    overall_summary = aggregate_region_summary(df_combined[df_combined['Delivered'] > 0])
    top10_overall = get_top10_overall(df_combined)
    try:
        if os.getenv('USE_HTML_PDF') != '1':
            return export_to_pdf_reportlab(esp_data, df_combined, from_date, to_date, account_data)
        html = build_mbr_html_report(
            esp_data=esp_data,
            overall_summary=overall_summary or {},
            top10_overall=top10_overall.to_dict('records') if not top10_overall.empty else [],
            account_data=account_data or {},
            from_date=from_date,
            to_date=to_date
        )
        return await export_to_pdf_html(html)
    except Exception as e:
        print(f"HTML PDF export failed: {e}. Falling back to ReportLab.")
        return export_to_pdf_reportlab(esp_data, df_combined, from_date, to_date, account_data)
