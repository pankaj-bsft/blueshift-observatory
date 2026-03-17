import json
from datetime import datetime
from typing import Dict, List


def _fmt_int(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def _fmt_pct(value, digits=2):
    try:
        return f"{float(value):.{digits}f}%"
    except Exception:
        return "0.00%"


def _build_table(headers: List[str], rows: List[List[str]]) -> str:
    head = "".join([f"<th>{h}</th>" for h in headers])
    body = "".join(
        [
            "<tr>" + "".join([f"<td>{cell}</td>" for cell in row]) + "</tr>"
            for row in rows
        ]
    )
    return f"""
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr>{head}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    """


def build_mbr_html_report(esp_data: Dict, overall_summary: Dict, top10_overall: List[Dict],
                          account_data: Dict, from_date: str, to_date: str) -> str:
    generated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    # Executive KPIs
    kpis = [
        ("Total Sent", _fmt_int(overall_summary.get("Total_Sent", 0)), "emails"),
        ("Total Delivered", _fmt_int(overall_summary.get("Total_Delivered", 0)), f"{_fmt_pct(overall_summary.get('Delivery_Rate_%', 0), 2)} rate"),
        ("Total Bounces", _fmt_int(overall_summary.get("Total_Bounces", 0)), f"{_fmt_pct(overall_summary.get('Bounce_Rate_%', 0), 4)} rate"),
        ("Spam Reports", _fmt_int(overall_summary.get("Total_Spam_Reports", 0)), f"{_fmt_pct(overall_summary.get('Spam_Rate_%', 0), 4)} rate"),
        ("Unsubscribes", _fmt_int(overall_summary.get("Total_Unsubscribes", 0)), f"{_fmt_pct(overall_summary.get('Unsub_Rate_%', 0), 4)} rate"),
        ("Unique Opens", _fmt_int(overall_summary.get("Total_Unique_Opens", 0)), f"{_fmt_pct(overall_summary.get('Open_Rate_%', 0), 2)} rate"),
        ("Unique Clicks", _fmt_int(overall_summary.get("Total_Unique_Clicks", 0)), f"{_fmt_pct(overall_summary.get('Click_Rate_%', 0), 2)} rate"),
        ("Click-to-Open", _fmt_pct(overall_summary.get("CTOR_%", 0), 2), "CTOR"),
    ]

    kpi_cards = "".join(
        [
            f"""
            <div class="kpi-card">
              <div class="kpi-label">{label}</div>
              <div class="kpi-value">{value}</div>
              <div class="kpi-sub">{sub}</div>
            </div>
            """
            for label, value, sub in kpis
        ]
    )

    # Executive summary raw table
    summary_rows = [
        ["Total Sent", _fmt_int(overall_summary.get("Total_Sent", 0))],
        ["Total Delivered", _fmt_int(overall_summary.get("Total_Delivered", 0))],
        ["Delivery Rate", _fmt_pct(overall_summary.get("Delivery_Rate_%", 0), 2)],
        ["Total Bounces", _fmt_int(overall_summary.get("Total_Bounces", 0))],
        ["Bounce Rate", _fmt_pct(overall_summary.get("Bounce_Rate_%", 0), 4)],
        ["Total Spam Reports", _fmt_int(overall_summary.get("Total_Spam_Reports", 0))],
        ["Spam Rate", _fmt_pct(overall_summary.get("Spam_Rate_%", 0), 4)],
        ["Total Unsubscribes", _fmt_int(overall_summary.get("Total_Unsubscribes", 0))],
        ["Unsubscribe Rate", _fmt_pct(overall_summary.get("Unsub_Rate_%", 0), 4)],
        ["Total Unique Opens", _fmt_int(overall_summary.get("Total_Unique_Opens", 0))],
        ["Open Rate", _fmt_pct(overall_summary.get("Open_Rate_%", 0), 2)],
        ["Total Unique Clicks", _fmt_int(overall_summary.get("Total_Unique_Clicks", 0))],
        ["Click Rate", _fmt_pct(overall_summary.get("Click_Rate_%", 0), 2)],
        ["Click-to-Open Rate", _fmt_pct(overall_summary.get("CTOR_%", 0), 2)],
    ]

    executive_table = _build_table(["Metric", "Value"], summary_rows)

    # ESP summary tables
    esp_cards = []
    esp_chart_data = []
    esp_sent_share = []
    for esp_name, esp_info in esp_data.items():
        combined = esp_info.get("combined_summary")
        if not combined:
            continue
        esp_sent_share.append({
            "esp": esp_name,
            "sent": combined.get("Total_Sent", 0)
        })
        esp_chart_data.append({
            "esp": esp_name,
            "delivery": combined.get("Delivery_Rate_%", 0),
            "open": combined.get("Open_Rate_%", 0),
            "click": combined.get("Click_Rate_%", 0),
            "ctor": combined.get("CTOR_%", 0),
            "health": combined.get("Health_Score", 0)
        })

        rows = []
        if esp_info.get("us_summary"):
            us = esp_info["us_summary"]
            rows.append([
                "US",
                _fmt_int(us.get("Total_Sent", 0)),
                _fmt_int(us.get("Total_Delivered", 0)),
                _fmt_pct(us.get("Delivery_Rate_%", 0), 2),
                _fmt_pct(us.get("Bounce_Rate_%", 0), 2),
                _fmt_pct(us.get("Open_Rate_%", 0), 2),
                _fmt_pct(us.get("Click_Rate_%", 0), 2),
                _fmt_pct(us.get("CTOR_%", 0), 2),
                f"{us.get('Health_Score', 0)}",
            ])
        if esp_info.get("eu_summary"):
            eu = esp_info["eu_summary"]
            rows.append([
                "EU",
                _fmt_int(eu.get("Total_Sent", 0)),
                _fmt_int(eu.get("Total_Delivered", 0)),
                _fmt_pct(eu.get("Delivery_Rate_%", 0), 2),
                _fmt_pct(eu.get("Bounce_Rate_%", 0), 2),
                _fmt_pct(eu.get("Open_Rate_%", 0), 2),
                _fmt_pct(eu.get("Click_Rate_%", 0), 2),
                _fmt_pct(eu.get("CTOR_%", 0), 2),
                f"{eu.get('Health_Score', 0)}",
            ])
        rows.append([
            "Total",
            _fmt_int(combined.get("Total_Sent", 0)),
            _fmt_int(combined.get("Total_Delivered", 0)),
            _fmt_pct(combined.get("Delivery_Rate_%", 0), 2),
            _fmt_pct(combined.get("Bounce_Rate_%", 0), 2),
            _fmt_pct(combined.get("Open_Rate_%", 0), 2),
            _fmt_pct(combined.get("Click_Rate_%", 0), 2),
            _fmt_pct(combined.get("CTOR_%", 0), 2),
            f"{combined.get('Health_Score', 0)}",
        ])

        esp_cards.append(f"""
        <div class="card">
          <div class="section-title">{esp_name} — Summary Metrics</div>
          {_build_table(
              ["Region", "Sent", "Delivered", "Delivery %", "Bounce %", "Open %", "Click %", "CTOR %", "Health"],
              rows
          )}
        </div>
        """)

    # Top 10 Domains overall
    top10_rows = []
    for domain in top10_overall[:10]:
        top10_rows.append([
            domain.get("From_domain", ""),
            _fmt_int(domain.get("Sent", 0)),
            _fmt_int(domain.get("Delivered", 0)),
            _fmt_pct(domain.get("Delivery_Rate_%", 0), 2),
            _fmt_pct(domain.get("Bounce_Rate_%", 0), 2),
            _fmt_pct(domain.get("Open_Rate_%", 0), 2),
            _fmt_pct(domain.get("Click_Rate_%", 0), 2),
        ])

    top10_table = _build_table(
        ["Domain", "Sent", "Delivered", "Delivery %", "Bounce %", "Open %", "Click %"],
        top10_rows
    )

    # Account analysis tables
    account_sections = []
    if account_data:
        top_accounts = account_data.get("top10_accounts_overall", [])
        if top_accounts:
            rows = []
            for row in top_accounts[:10]:
                rows.append([
                    str(row.get("Rank", "")),
                    row.get("Account", ""),
                    _fmt_int(row.get("Sent", 0)),
                    _fmt_int(row.get("Delivered", 0)),
                    _fmt_pct(row.get("Delivery_Rate_%", 0), 2),
                    _fmt_pct(row.get("Open_Rate_%", 0), 2),
                    _fmt_pct(row.get("Click_Rate_%", 0), 2),
                ])
            account_sections.append(f"""
            <div class="card">
              <div class="section-title">Top 10 Accounts — All ESPs</div>
              {_build_table(["#", "Account", "Sent", "Delivered", "Delivery %", "Open %", "Click %"], rows)}
            </div>
            """)

        esp_account_data = account_data.get("esp_data", {})
        for esp_name, esp_info in esp_account_data.items():
            accounts = esp_info.get("top10_accounts", [])
            if not accounts:
                continue
            rows = []
            for row in accounts[:10]:
                rows.append([
                    str(row.get("Rank", "")),
                    row.get("Account", ""),
                    _fmt_int(row.get("Sent", 0)),
                    _fmt_int(row.get("Delivered", 0)),
                    _fmt_pct(row.get("Delivery_Rate_%", 0), 2),
                    _fmt_pct(row.get("Open_Rate_%", 0), 2),
                    _fmt_pct(row.get("Click_Rate_%", 0), 2),
                ])
            account_sections.append(f"""
            <div class="card">
              <div class="section-title">{esp_name} — Top 10 Accounts</div>
              {_build_table(["#", "Account", "Sent", "Delivered", "Delivery %", "Open %", "Click %"], rows)}
            </div>
            """)

    # Affiliate section
    affiliate_section = ""
    affiliate_accounts = account_data.get("affiliate_accounts", []) if account_data else []
    if affiliate_accounts:
        rows = []
        for row in affiliate_accounts[:10]:
            rows.append([
                str(row.get("Rank", "")),
                row.get("Account", ""),
                _fmt_int(row.get("Sent", 0)),
                _fmt_int(row.get("Delivered", 0)),
                _fmt_pct(row.get("Delivery_Rate_%", 0), 2),
                _fmt_pct(row.get("Open_Rate_%", 0), 2),
                _fmt_pct(row.get("Click_Rate_%", 0), 2),
            ])
        affiliate_section = f"""
        <div class="card">
          <div class="section-title">Affiliate Accounts — IsAffiliate = Yes</div>
          {_build_table(["#", "Account", "Sent", "Delivered", "Delivery %", "Open %", "Click %"], rows)}
        </div>
        """

    data_payload = {
        "espPerformance": esp_chart_data,
        "espShare": esp_sent_share,
    }

    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MBR Deliverability Report</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
      background: #0a0e1a;
      color: #e2e8f0;
    }}
    .page {{ padding: 26px 28px; }}
    .header {{
      background: linear-gradient(135deg,#1e1b4b,#0f172a);
      border-bottom: 1px solid #1e293b;
      padding: 18px 24px;
    }}
    .header h1 {{ margin: 0; font-size: 18px; font-weight: 800; color: #fff; }}
    .header p {{ margin: 4px 0 0; font-size: 11px; color: #94a3b8; }}
    .section-title {{
      font-size: 12px;
      font-weight: 700;
      color: #94a3b8;
      text-transform: uppercase;
      letter-spacing: .1em;
      margin: 0 0 12px;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .kpi-card {{
      background: linear-gradient(135deg,#111827,#1e293b);
      border: 1px solid #1f2937;
      border-radius: 14px;
      padding: 14px 12px;
    }}
    .kpi-label {{ font-size: 11px; color: #94a3b8; font-weight: 600; }}
    .kpi-value {{ font-size: 18px; font-weight: 800; color: #e2e8f0; margin-top: 4px; }}
    .kpi-sub {{ font-size: 10px; color: #64748b; margin-top: 2px; }}
    .card {{
      background: #111827;
      border: 1px solid #1e293b;
      border-radius: 16px;
      padding: 16px;
      margin-bottom: 18px;
    }}
    .grid-2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }}
    .chart-wrap {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 12px;
      padding: 10px 12px;
    }}
    .table-wrap {{ overflow: hidden; border-radius: 10px; border: 1px solid #1e293b; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
    thead tr {{ background: #0f172a; }}
    th, td {{ padding: 8px 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ color: #64748b; font-weight: 600; border-bottom: 1px solid #1e293b; }}
    td {{ color: #94a3b8; border-bottom: 1px solid #1e293b22; }}
    .footer {{ text-align: center; color: #334155; font-size: 10px; margin-top: 16px; }}
    @page {{ margin: 0; }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0"></script>
</head>
<body>
  <div class="header">
    <h1>Monthly Business Review — Deliverability Report</h1>
    <p>Period: {from_date} to {to_date} · Generated: {generated}</p>
  </div>
  <div class="page">
    <div class="section-title">Executive Summary</div>
    <div class="kpi-grid">{kpi_cards}</div>
    <div class="card">
      <div class="section-title">Executive Summary — All ESPs (Raw Data)</div>
      {executive_table}
    </div>

    <div class="grid-2">
      <div class="card chart-wrap">
        <div class="section-title">Share of Total Sent by ESP</div>
        <canvas id="chartShare" height="220"></canvas>
      </div>
      <div class="card chart-wrap">
        <div class="section-title">ESP Performance Rates (%)</div>
        <canvas id="chartPerf" height="220"></canvas>
      </div>
    </div>

    <div class="card chart-wrap" style="margin-top:16px;">
      <div class="section-title">Health Score by ESP</div>
      <canvas id="chartHealth" height="200"></canvas>
    </div>

    <div style="page-break-after: always;"></div>

    <div class="section-title">ESP Metrics</div>
    {''.join(esp_cards)}

    <div style="page-break-after: always;"></div>

    <div class="section-title">Domain Analysis</div>
    <div class="card">
      <div class="section-title">Top 10 Domains Across All ESPs</div>
      {top10_table}
    </div>

    <div style="page-break-after: always;"></div>

    <div class="section-title">Account Analysis</div>
    {''.join(account_sections)}

    <div style="page-break-after: always;"></div>

    <div class="section-title">Affiliates</div>
    {affiliate_section}

    <div class="footer">Blueshift Deliverability MBR · All data sourced from ESP APIs</div>
  </div>

  <script>
    const DATA = {json.dumps(data_payload)};
    Chart.register(ChartDataLabels);

    const shareLabels = DATA.espShare.map(d => d.esp);
    const shareValues = DATA.espShare.map(d => d.sent);
    const shareTotal = shareValues.reduce((a,b) => a+b, 0);

    new Chart(document.getElementById('chartShare'), {{
      type: 'doughnut',
      data: {{
        labels: shareLabels,
        datasets: [{{
          data: shareValues,
          backgroundColor: ['#6366f1','#06b6d4','#f59e0b'],
          borderWidth: 0
        }}]
      }},
      options: {{
        plugins: {{
          legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 10 }} }} }},
          tooltip: {{
            backgroundColor: '#1e293b',
            borderColor: '#334155',
            borderWidth: 1,
            callbacks: {{
              label: (ctx) => {{
                const val = ctx.raw || 0;
                const pct = shareTotal ? (val / shareTotal * 100) : 0;
                return `${{ctx.label}}: ${{pct.toFixed(1)}}%`;
              }}
            }}
          }},
          datalabels: {{
            color: '#e2e8f0',
            font: {{ weight: '700', size: 10 }},
            formatter: (value, ctx) => {{
              const pct = shareTotal ? (value / shareTotal * 100) : 0;
              return `${{pct.toFixed(1)}}%`;
            }}
          }}
        }}
      }}
    }});

    new Chart(document.getElementById('chartPerf'), {{
      type: 'bar',
      data: {{
        labels: DATA.espPerformance.map(d => d.esp),
        datasets: [
          {{ label: 'Delivery %', data: DATA.espPerformance.map(d => d.delivery), backgroundColor: '#10b981' }},
          {{ label: 'Open %', data: DATA.espPerformance.map(d => d.open), backgroundColor: '#3b82f6' }},
          {{ label: 'Click %', data: DATA.espPerformance.map(d => d.click), backgroundColor: '#f59e0b' }},
          {{ label: 'CTOR %', data: DATA.espPerformance.map(d => d.ctor), backgroundColor: '#8b5cf6' }},
        ]
      }},
      options: {{
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ display: false }} }},
          y: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }}, callback: v => v + '%' }}, grid: {{ color: '#1e293b' }}, min: 0, max: 100 }}
        }},
        plugins: {{
          legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 10 }} }} }},
          tooltip: {{
            backgroundColor: '#1e293b',
            borderColor: '#334155',
            borderWidth: 1
          }},
          datalabels: {{
            color: '#e2e8f0',
            font: {{ weight: '700', size: 9 }},
            formatter: v => `${{v.toFixed(1)}}%`,
            anchor: 'end',
            align: 'end'
          }}
        }}
      }}
    }});

    new Chart(document.getElementById('chartHealth'), {{
      type: 'bar',
      data: {{
        labels: DATA.espPerformance.map(d => d.esp),
        datasets: [{{
          label: 'Health Score',
          data: DATA.espPerformance.map(d => d.health),
          backgroundColor: '#10b981'
        }}]
      }},
      options: {{
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ display: false }} }},
          y: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }}, min: 0, max: 100 }}
        }},
        plugins: {{
          legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 10 }} }} }},
          datalabels: {{
            color: '#e2e8f0',
            font: {{ weight: '700', size: 9 }},
            formatter: v => `${{v.toFixed(1)}}`,
            anchor: 'end',
            align: 'end'
          }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""


async def export_to_pdf_html(html: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        pdf_bytes = await page.pdf(
            format="Letter",
            print_background=True,
            margin={"top": "0.4in", "right": "0.4in", "bottom": "0.4in", "left": "0.4in"}
        )
        await browser.close()
        return pdf_bytes
