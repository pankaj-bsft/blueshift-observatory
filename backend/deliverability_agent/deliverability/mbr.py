"""Monthly Business Review (MBR) deliverability reports from EC2.

mbr_reports.db stores saved monthly reports (report_type 'account' or 'domain').
Each row's `report_data` is a JSON blob with per-ESP breakdowns and ranked
top-account/domain metrics (sent, delivered, rates, opens, clicks, MoM change).
"""

import json
import os

from . import ec2_data

MBR_DB = os.getenv("EC2_MBR_DB", f"{ec2_data.DATA_DIR.rstrip('/')}/mbr_reports.db")

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"], 1)}


def parse_month(value):
    """Accept '6', 6, 'June', 'jun' -> month number, or None."""
    if value in (None, ""):
        return None
    s = str(value).strip().lower()
    if s.isdigit():
        return int(s)
    return MONTHS.get(s) or MONTHS.get(s[:3] and next((k for k in MONTHS if k.startswith(s[:3])), ""), None)


def list_reports(report_type=None):
    sql = ("SELECT id, report_type, from_date, to_date, month, year, "
           "total_accounts, total_domains FROM mbr_reports")
    params = ()
    if report_type:
        sql += " WHERE report_type = ?"
        params = (report_type,)
    sql += " ORDER BY year DESC, month DESC"
    return ec2_data.query(MBR_DB, sql, params)


def get_report(month=None, year=None, report_type="account"):
    where, params = ["report_type = ?"], [report_type]
    if month:
        where.append("month = ?")
        params.append(int(month))
    if year:
        where.append("year = ?")
        params.append(int(year))
    sql = (f"SELECT * FROM mbr_reports WHERE {' AND '.join(where)} "
           "ORDER BY year DESC, month DESC LIMIT 1")
    rows = ec2_data.query(MBR_DB, sql, tuple(params))
    if not rows:
        return None
    row = rows[0]
    try:
        data = json.loads(row.get("report_data") or "{}")
    except (ValueError, TypeError):
        data = {}
    meta = {k: row.get(k) for k in
            ("report_type", "from_date", "to_date", "month", "year", "total_accounts", "total_domains")}
    return {"meta": meta, "data": data}


def top_entities(data):
    """
    Return (entities_list, name_field) for account or domain reports.

    Domain reports store their rows under "top10_overall" and name the column
    "From_domain", neither of which the original key/field lists covered — so domain
    MBRs silently returned no rows at all. Candidate keys and name fields are both
    checked now, and the name field is taken from the row itself.
    """
    name_candidates = ("Account", "Domain", "From_domain", "account_name", "domain")

    def _name_field(row):
        for field in name_candidates:
            if field in row:
                return field
        return None

    for key in ("top10_accounts_overall", "top10_domains_overall", "top10_overall"):
        rows = data.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            field = _name_field(rows[0])
            if field:
                return rows, field

    # fallback: first list-of-dicts value carrying a recognisable name column
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            field = _name_field(value[0])
            if field:
                return value, field
    return [], "Account"
