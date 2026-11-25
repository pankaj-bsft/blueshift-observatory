"""
DNS Alert Service - SendGrid email alerts for DNS changes
"""
import json
import os
from datetime import date
from html import escape
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

def _value_items(field, raw):
    """Stored record value -> list of display items (one per MX host / tag / mechanism)."""
    if raw in (None, ''):
        return []
    s = str(raw).strip()
    if s.lower() in ('null', 'none', '[]', '{}'):
        return []
    if s[:1] in ('[', '{'):
        try:
            v = json.loads(s)
            if isinstance(v, dict):
                v = [v]
            items = []
            for x in v:
                if isinstance(x, dict) and 'host' in x:
                    items.append(f"{x.get('priority', '')} {x['host']}".strip())
                else:
                    items.append(x if isinstance(x, str) else json.dumps(x))
            return items
        except ValueError:
            pass
    if field in ('DMARC', 'BIMI', 'TLS-RPT', 'MTA-STS'):
        return [t.strip() for t in s.split(';') if t.strip()]   # tag=value pairs
    return s.split()                                            # SPF mechanisms, DKIM, PTR...


def _render_value(field, raw, other_raw, side):
    """HTML for one side; items missing from the other side are highlighted."""
    items = _value_items(field, raw)
    if not items:
        return '<span style="color:#94a3b8;font-style:italic;">no record</span>'
    other = set(_value_items(field, other_raw))
    color = '#b91c1c' if side == 'old' else '#15803d'
    bg = '#fee2e2' if side == 'old' else '#dcfce7'
    sep = '<br>' if field == 'MX' else ('; ' if field in ('DMARC', 'BIMI', 'TLS-RPT', 'MTA-STS') else ' ')
    out = []
    for it in items:
        txt = escape(it if len(it) <= 90 else it[:60] + '…' + it[-20:])   # DKIM keys are long
        if other and it not in other:
            out.append(f'<span style="background:{bg};color:{color};font-weight:700;padding:0 2px;border-radius:3px;">{txt}</span>')
        else:
            out.append(txt)
    return sep.join(out)


def _fmt_date(d):
    """'2026-11-24' -> 'Tue 24 Nov 2026' (falls back to the raw value)."""
    try:
        return date.fromisoformat(str(d)[:10]).strftime('%a %d %b %Y')
    except (TypeError, ValueError):
        return str(d) if d else ''


def _when_html(e):
    """When the change happened: between the previous check and the check that found it."""
    found, prev = _fmt_date(e.get('check_date')), _fmt_date(e.get('prev_check_date'))
    if not found:
        return ''
    if prev:
        return (f'<div style="margin-top:4px;font-size:11px;color:#64748b;">📅 Detected <b>{found}</b>'
                f'<br><span style="color:#94a3b8;">changed after the {prev} check</span></div>')
    return f'<div style="margin-top:4px;font-size:11px;color:#64748b;">📅 Detected <b>{found}</b></div>'


def _side_date(d):
    d = _fmt_date(d)
    return f'<br><span style="font-weight:500;text-transform:none;color:#94a3b8;">{d[4:]}</span>' if d else ''


def _values_row(e):
    old_v, new_v = e.get('old_value'), e.get('new_value')
    if not old_v and not new_v:
        return ''
    cell = 'padding:4px 8px;font-family:Menlo,Consolas,monospace;font-size:11.5px;color:#334155;word-break:break-all;vertical-align:top;'
    lab = 'width:84px;padding:4px 8px;font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;white-space:nowrap;vertical-align:top;'
    return (f'<tr style="border-bottom:1px solid #f1f5f9;"><td></td><td colspan="4" style="padding:0 12px 12px;">'
            f'<table style="width:100%;border-collapse:collapse;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">'
            f'<tr><td style="{lab}">Previous{_side_date(e.get("prev_check_date"))}</td><td style="{cell}">{_render_value(e["field"], old_v, new_v, "old")}</td></tr>'
            f'<tr><td style="{lab}border-top:1px solid #e2e8f0;">Current{_side_date(e.get("check_date"))}</td><td style="{cell}border-top:1px solid #e2e8f0;">{_render_value(e["field"], new_v, old_v, "new")}</td></tr>'
            f'</table></td></tr>')


def build_dns_alert(events, check_date):
    """Return (subject, html) for the alert email."""

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
            rows_html+=f'<tr style="border-top:1px solid #e2e8f0;"><td style="padding:10px 12px;font-size:13px;font-weight:600;color:#1e293b;vertical-align:top;">{escape(domain)}<br><span style="font-size:11px;font-weight:400;color:#94a3b8;">{escape(str(account))} · {escape(str(esp))}</span></td><td style="padding:10px 12px;font-size:13px;font-weight:700;">{e["field"]}</td><td style="padding:10px 12px;"><span style="font-size:11px;font-weight:700;padding:3px 9px;border-radius:12px;background:{bg};color:{color};">{SEVERITY_EMOJI.get(sev,"")} {sev.upper()}</span></td><td style="padding:10px 12px;font-size:12px;color:#475569;">{escape(e.get("detail") or "")}{_when_html(e)}</td><td style="padding:10px 12px;font-size:11px;color:#64748b;"><span style="color:#dc2626;">{e.get("old_status","?")}</span> → <span style="color:#16a34a;">{e.get("new_status","?")}</span></td></tr>'
            rows_html+=_values_row(e)

    html=f'''<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f1f5f9;font-family:Inter,sans-serif;">
<div style="max-width:860px;margin:32px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
<div style="background:linear-gradient(135deg,#1e40af,#3b82f6);padding:28px 32px;">
<div style="font-size:11px;font-weight:700;color:rgba(255,255,255,0.6);text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Blueshift Observatory</div>
<div style="font-size:22px;font-weight:800;color:#fff;">🔔 DNS Change Alert</div>
<div style="font-size:13px;color:rgba(255,255,255,0.75);margin-top:4px;">Checked {_fmt_date(check_date) or check_date} · {len(events)} change(s) across {len(by_domain)} domain(s)</div>
</div>
<div style="padding:28px 32px;">
<table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">
<thead><tr style="background:#f8fafc;">
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Domain</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Record</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Severity</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Details</th>
<th style="padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;border-bottom:1px solid #e2e8f0;">Status</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
<div style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;">
<div style="font-size:11px;color:#94a3b8;">Sent by Blueshift Observatory DNS Looker · Auto-generated</div>
</div></div></body></html>'''
    return subject, html


def send_dns_alert(events, recipients, check_date=None):
    if not events or not recipients: return {'status':'skipped'}
    if not SENDGRID_API_KEY: return {'status':'error','reason':'SENDGRID_API_KEY not set'}
    if check_date is None: check_date = date.today().isoformat()
    subject, html = build_dns_alert(events, check_date)
    try:
        message=Mail(from_email=Email(FROM_EMAIL),to_emails=[To(r) for r in recipients],subject=subject,html_content=Content('text/html',html))
        message.reply_to=Email(REPLY_TO_EMAIL)
        sg=SendGridAPIClient(SENDGRID_API_KEY)
        response=sg.send(message)
        return {'status':'success','recipients':recipients,'events_count':len(events),'status_code':response.status_code}
    except Exception as e:
        return {'status':'error','reason':str(e)}
