"""
DNS Alert Service - SendGrid email alerts for DNS changes
"""
import os
from datetime import date
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

load_dotenv()
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
FROM_EMAIL = 'Pankaj@deliverability.getblueshift.com'
REPLY_TO_EMAIL = 'pankaj.kumar@getblueshift.com'

SEVERITY_EMOJI = {'critical':'🔴','warning':'🟡','info':'🟢'}
SEVERITY_COLOR = {'critical':'#dc2626','warning':'#d97706','info':'#16a34a'}
SEVERITY_BG = {'critical':'#fef2f2','warning':'#fffbeb','info':'#f0fdf4'}

def send_dns_alert(events, recipients, check_date=None):
    if not events or not recipients: return {'status':'skipped'}
    if not SENDGRID_API_KEY: return {'status':'error','reason':'SENDGRID_API_KEY not set'}
    if check_date is None: check_date = date.today().isoformat()

    by_domain={}
    for e in events: by_domain.setdefault(e['domain'],[]).append(e)
    critical_count=sum(1 for e in events if e['severity']=='critical')
    warning_count=sum(1 for e in events if e['severity']=='warning')

    if critical_count: subject=f"🔴 DNS Alert: {critical_count} critical change(s) — {check_date}"
    elif warning_count: subject=f"🟡 DNS Alert: {warning_count} warning(s) — {check_date}"
    else: subject=f"🟢 DNS Update: {len(events)} change(s) — {check_date}"

    rows_html=''
    for domain,devents in sorted(by_domain.items()):
        account=devents[0].get('account') or '—'
        esp=devents[0].get('esp') or '—'
        for e in devents:
            sev=e['severity']; color=SEVERITY_COLOR.get(sev,'#334155'); bg=SEVERITY_BG.get(sev,'#f8fafc')
            rows_html+=f'<tr style="border-bottom:1px solid #f1f5f9;"><td style="padding:10px 12px;font-size:13px;font-weight:600;color:#1e293b;">{domain}<br><span style="font-size:11px;font-weight:400;color:#94a3b8;">{account} · {esp}</span></td><td style="padding:10px 12px;font-size:13px;font-weight:700;">{e["field"]}</td><td style="padding:10px 12px;"><span style="font-size:11px;font-weight:700;padding:3px 9px;border-radius:12px;background:{bg};color:{color};">{SEVERITY_EMOJI.get(sev,"")} {sev.upper()}</span></td><td style="padding:10px 12px;font-size:12px;color:#475569;">{e.get("detail","")}</td><td style="padding:10px 12px;font-size:11px;color:#64748b;"><span style="color:#dc2626;">{e.get("old_status","?")}</span> → <span style="color:#16a34a;">{e.get("new_status","?")}</span></td></tr>'

    html=f'''<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f1f5f9;font-family:Inter,sans-serif;">
<div style="max-width:860px;margin:32px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
<div style="background:linear-gradient(135deg,#1e40af,#3b82f6);padding:28px 32px;">
<div style="font-size:11px;font-weight:700;color:rgba(255,255,255,0.6);text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Blueshift Observatory</div>
<div style="font-size:22px;font-weight:800;color:#fff;">🔔 DNS Change Alert</div>
<div style="font-size:13px;color:rgba(255,255,255,0.75);margin-top:4px;">{check_date} · {len(events)} change(s) across {len(by_domain)} domain(s)</div>
</div>
<div style="padding:28px 32px;">
<table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">
<thead><tr style="background:#f8fafc;">
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Domain</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Record</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Severity</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Details</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Change</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
<div style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;">
<div style="font-size:11px;color:#94a3b8;">Sent by Blueshift Observatory DNS Looker · Auto-generated</div>
</div></div></body></html>'''

    try:
        message=Mail(from_email=Email(FROM_EMAIL),to_emails=[To(r) for r in recipients],subject=subject,html_content=Content('text/html',html))
        message.reply_to=Email(REPLY_TO_EMAIL)
        sg=SendGridAPIClient(SENDGRID_API_KEY)
        response=sg.send(message)
        return {'status':'success','recipients':recipients,'events_count':len(events),'status_code':response.status_code}
    except Exception as e:
        return {'status':'error','reason':str(e)}
