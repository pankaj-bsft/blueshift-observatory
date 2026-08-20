"""Account & IP lookups from the observatory's mapping / SNDS / account-info DBs.

Sources (on EC2 unless noted):
  account_mappings.db  domain_account_mapping   sending_domain -> account
  snds_data.db         snds_ip_mapping          IP -> account, ESP
  snds_data.db         snds_data                per-IP SNDS reputation
  account_info.db      account_info_snapshot    esp/domain/ip_addresses/ip_pool
                                                (empty on EC2 -> local fallback)
"""

import json
import os
import re
import sqlite3
from pathlib import Path

from . import ec2_data

MAP_DB = os.getenv("EC2_MAP_DB", f"{ec2_data.DATA_DIR.rstrip('/')}/account_mappings.db")
SNDS_DB = os.getenv("EC2_SNDS_DB", f"{ec2_data.DATA_DIR.rstrip('/')}/snds_data.db")
ACCT_INFO_DB = os.getenv("EC2_ACCT_INFO_DB", f"{ec2_data.DATA_DIR.rstrip('/')}/account_info.db")
LOCAL_ACCT_INFO_DB = os.path.expanduser(
    os.getenv("LOCAL_ACCT_INFO_DB", "/Users/pankaj/pani/data/account_info.db")
)

IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def is_ip(value):
    return bool(IP_RE.match((value or "").strip()))


def domain_to_account(domain):
    return ec2_data.query(
        MAP_DB,
        "SELECT sending_domain, account_name, is_affiliate, notes "
        "FROM domain_account_mapping WHERE sending_domain = ? OR sending_domain LIKE ?",
        (domain, domain + "{%"),
    )


def account_to_domains(account):
    return ec2_data.query(
        MAP_DB,
        "SELECT sending_domain, account_name, is_affiliate FROM domain_account_mapping "
        "WHERE account_name LIKE ? ORDER BY sending_domain",
        (f"%{account}%",),
    )


def ip_mapping(ip=None, account=None):
    if ip:
        return ec2_data.query(
            SNDS_DB, "SELECT ip_address, account_name, esp FROM snds_ip_mapping WHERE ip_address = ?", (ip,)
        )
    if account:
        return ec2_data.query(
            SNDS_DB,
            "SELECT ip_address, account_name, esp FROM snds_ip_mapping WHERE account_name LIKE ? ORDER BY ip_address",
            (f"%{account}%",),
        )
    return []


def ip_snds_latest(ip):
    rows = ec2_data.query(
        SNDS_DB,
        "SELECT ip_address, account_name, data_date, message_volume, spam_rate, "
        "complaint_rate, trap_hits, filter_result FROM snds_data "
        "WHERE ip_address = ? ORDER BY data_date DESC LIMIT 1",
        (ip,),
    )
    return rows[0] if rows else None


def _account_info_all():
    """Latest account_info snapshot as a list of dicts. EC2 first, local fallback."""
    try:
        rows = ec2_data.query(
            ACCT_INFO_DB, "SELECT data_json FROM account_info_snapshot ORDER BY id DESC LIMIT 1"
        )
    except ec2_data.EC2DataError:
        rows = []
    raw = rows[0]["data_json"] if rows and rows[0].get("data_json") else None

    if not raw and Path(LOCAL_ACCT_INFO_DB).exists():
        conn = sqlite3.connect(LOCAL_ACCT_INFO_DB)
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT data_json FROM account_info_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        raw = r["data_json"] if r and r["data_json"] else None

    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def account_info_matches(query):
    """account_info records where domain/account/ip_addresses/subaccount contain query."""
    q = (query or "").lower()
    out = []
    for rec in _account_info_all():
        hay = " ".join(str(rec.get(k, "")) for k in
                       ("domain", "account_name", "ip_addresses", "ip_pool", "subaccount", "esp")).lower()
        if q in hay:
            out.append(rec)
    return out
