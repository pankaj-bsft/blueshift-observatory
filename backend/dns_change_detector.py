"""
DNS Change Detector
"""
import sqlite3, json
from datetime import date, timedelta
from typing import Optional
from data_paths import data_path

DNS_DB_PATH = data_path('dns_looker.db')

COMPARE_FIELDS = [
    ('spf_status','spf_value','SPF'),('dkim_status','dkim_value','DKIM'),
    ('dmarc_status','dmarc_value','DMARC'),('mx_status','mx_value','MX'),
    ('bimi_status','bimi_value','BIMI'),('mta_sts_status','mta_sts_mode','MTA-STS'),
    ('tls_rpt_status','tls_rpt_value','TLS-RPT'),('ptr_status','ptr_value','PTR'),
]
SEVERITY_MAP = {('pass','fail'):'critical',('pass','warn'):'warning',('warn','fail'):'critical',('fail','pass'):'info',('warn','pass'):'info',('fail','warn'):'info'}

def init_change_events_table():
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS dns_change_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, account TEXT, esp TEXT,
        check_date DATE NOT NULL, field TEXT NOT NULL, old_status TEXT, new_status TEXT,
        old_value TEXT, new_value TEXT, severity TEXT NOT NULL, detail TEXT,
        notified INTEGER DEFAULT 0, read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ce_domain ON dns_change_events(domain)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ce_date ON dns_change_events(check_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ce_notified ON dns_change_events(notified)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ce_read ON dns_change_events(read)')
    conn.commit(); conn.close()

def _norm(val) -> Optional[str]:
    if val is None: return None
    if isinstance(val,(dict,list)): return json.dumps(val,sort_keys=True)
    return str(val)

def _build_detail(field,old,new):
    if new=='fail' and old=='pass': return f"{field} broken — was passing, now failing."
    if new=='fail' and old=='warn': return f"{field} critical — degraded from warning to failure."
    if new=='warn' and old=='pass': return f"{field} warning — was passing, now has issues."
    if new=='pass': return f"{field} recovered — now passing."
    return f"{field} status changed: {old} → {new}"

def detect_changes(check_date=None):
    if check_date is None: check_date = date.today()
    conn = sqlite3.connect(DNS_DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
    c.execute('SELECT * FROM dns_history WHERE check_date = ?',(check_date.isoformat(),))
    today_rows = {r['domain']:dict(r) for r in c.fetchall()}
    if not today_rows: conn.close(); return []
    c.execute('SELECT * FROM dns_history WHERE check_date < ? AND check_date >= date(?,"-7 days")',(check_date.isoformat(),check_date.isoformat()))
    all_prev = c.fetchall(); conn.close()
    prev_rows = {}
    for row in all_prev:
        d=row['domain']
        if d not in prev_rows or row['check_date']>prev_rows[d]['check_date']: prev_rows[d]=dict(row)
    events=[]
    for domain,today in today_rows.items():
        prev=prev_rows.get(domain)
        if not prev: continue
        for sf,vf,label in COMPARE_FIELDS:
            os_=prev.get(sf); ns_=today.get(sf)
            if os_ in (None,'skip') and ns_ in (None,'skip'): continue
            if os_==ns_:
                if _norm(prev.get(vf))==_norm(today.get(vf)): continue
                sev='info'; detail=f"{label} record value changed."
            else:
                sev=SEVERITY_MAP.get((os_,ns_))
                if not sev: continue
                detail=_build_detail(label,os_,ns_)
            ov=_norm(prev.get(vf)); nv=_norm(today.get(vf))
            if label=='DMARC':
                ol=prev.get('dmarc_policy_level') or 0; nl=today.get('dmarc_policy_level') or 0
                if ol!=nl:
                    if nl<ol: sev='critical'; detail=f"DMARC downgraded: {prev.get('dmarc_policy','?')} → {today.get('dmarc_policy','?')}"
                    else: sev='info'; detail=f"DMARC upgraded: {prev.get('dmarc_policy','?')} → {today.get('dmarc_policy','?')}"
            events.append({'domain':domain,'account':today.get('account'),'esp':today.get('esp'),'check_date':check_date.isoformat(),'field':label,'old_status':os_,'new_status':ns_,'old_value':(ov or '')[:500],'new_value':(nv or '')[:500],'severity':sev,'detail':detail})
    return events

def save_change_events(events):
    if not events: return 0
    conn=sqlite3.connect(DNS_DB_PATH); c=conn.cursor(); saved=0
    for e in events:
        c.execute('SELECT id FROM dns_change_events WHERE domain=? AND check_date=? AND field=?',(e['domain'],e['check_date'],e['field']))
        if c.fetchone(): continue
        c.execute('INSERT INTO dns_change_events (domain,account,esp,check_date,field,old_status,new_status,old_value,new_value,severity,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(e['domain'],e.get('account'),e.get('esp'),e['check_date'],e['field'],e.get('old_status'),e.get('new_status'),e.get('old_value'),e.get('new_value'),e['severity'],e.get('detail')))
        saved+=1
    conn.commit(); conn.close(); return saved

def get_unread_count():
    conn=sqlite3.connect(DNS_DB_PATH); c=conn.cursor()
    c.execute('SELECT COUNT(*) FROM dns_change_events WHERE read=0'); n=c.fetchone()[0]; conn.close(); return n

def get_recent_events(days=7,include_read=True):
    conn=sqlite3.connect(DNS_DB_PATH); conn.row_factory=sqlite3.Row; c=conn.cursor()
    since=(date.today()-timedelta(days=days)).isoformat()
    q='SELECT * FROM dns_change_events WHERE check_date >= ?'; params=[since]
    if not include_read: q+=' AND read=0'
    q+=' ORDER BY check_date DESC, severity ASC, domain ASC'
    c.execute(q,params); rows=[dict(r) for r in c.fetchall()]; conn.close(); return rows

def get_unnotified_events():
    conn=sqlite3.connect(DNS_DB_PATH); conn.row_factory=sqlite3.Row; c=conn.cursor()
    c.execute('SELECT * FROM dns_change_events WHERE notified=0 ORDER BY severity ASC, domain ASC')
    rows=[dict(r) for r in c.fetchall()]; conn.close(); return rows

def mark_all_read():
    conn=sqlite3.connect(DNS_DB_PATH); c=conn.cursor()
    c.execute('UPDATE dns_change_events SET read=1 WHERE read=0'); conn.commit(); conn.close()

def mark_event_read(event_id):
    conn=sqlite3.connect(DNS_DB_PATH); c=conn.cursor()
    c.execute('UPDATE dns_change_events SET read=1 WHERE id=?',(event_id,)); conn.commit(); conn.close()

def mark_events_notified(event_ids):
    if not event_ids: return
    conn=sqlite3.connect(DNS_DB_PATH); c=conn.cursor()
    ph=','.join('?'*len(event_ids))
    c.execute(f'UPDATE dns_change_events SET notified=1 WHERE id IN ({ph})',event_ids)
    conn.commit(); conn.close()

init_change_events_table()
