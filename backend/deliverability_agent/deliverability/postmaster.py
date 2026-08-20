"""Google Postmaster Tools data access — multi-source.

The agent can get Postmaster stats from three sources, chosen by the
POSTMASTER_SOURCE env var:

  ec2   (default) — query the EC2 gpt_data.db over SSH. Always the live EC2
                    data your cron collects; works headless (no browser) and
                    never touches a stale local copy.
  local           — read a local gpt_data.db copy (LOCAL_GPT_DB).
  live            — call the Google Postmaster API directly (needs
                    credentials.json + one-time browser consent).

Every source is normalized to the SAME dict shape (the Postmaster REST
"TrafficStats" resource), so analyzer.analyze_postmaster() works unchanged.
"""

import json
import os
import re
import sqlite3
from pathlib import Path

from . import ec2_data

SCOPES = ["https://www.googleapis.com/auth/postmaster.readonly"]

_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = _ROOT / "credentials.json"
TOKEN_PATH = _ROOT / "token.json"


def _load_env():
    """Load .env from the project root so this module works standalone."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

SOURCE = os.getenv("POSTMASTER_SOURCE", "ec2").lower()
EC2_HOST = os.getenv("EC2_HOST", "")
EC2_USER = os.getenv("EC2_USER", "ec2-user")
EC2_KEY = os.path.expanduser(os.getenv("EC2_KEY", "~/.ssh/id_rsa"))
EC2_GPT_DB = os.getenv("EC2_GPT_DB", "/home/ec2-user/pani/blueshift_observatory/data/gpt_data.db")
LOCAL_GPT_DB = os.path.expanduser(os.getenv("LOCAL_GPT_DB", ""))


class PostmasterError(RuntimeError):
    """Human-friendly error the agent can relay to the user."""


# --------------------------------------------------------------------------
# Normalization: gpt_data table row -> Postmaster TrafficStats shape
# --------------------------------------------------------------------------

def _ratio(value):
    """DB stores rates as percentages (0-100); the analyzer wants 0-1."""
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def _normalize_db_row(row):
    if not row:
        return None
    date = str(row.get("data_date", "")).replace("-", "")

    try:
        delivery = json.loads(row.get("delivery_errors") or "[]")
    except (ValueError, TypeError):
        delivery = []
    if not isinstance(delivery, list):
        delivery = []

    # ip_reputation JSON shape: {"breakdown": {"HIGH": 4}, "samples": {"HIGH": [...]}}
    ip_reps = []
    raw_ip = row.get("ip_reputation")
    if raw_ip:
        try:
            data = json.loads(raw_ip)
            breakdown = data.get("breakdown", {}) or {}
            samples = data.get("samples", {}) or {}
            for cat, count in breakdown.items():
                ip_reps.append({"reputation": cat, "ipCount": count, "sampleIps": samples.get(cat, [])})
        except (ValueError, TypeError):
            pass

    return {
        "name": f"domains/{row.get('domain')}/trafficStats/{date}",
        "domainReputation": row.get("reputation") or "REPUTATION_CATEGORY_UNSPECIFIED",
        "userReportedSpamRatio": _ratio(row.get("user_reported_spam_rate")),
        "spfSuccessRatio": _ratio(row.get("spf_success_rate")),
        "dkimSuccessRatio": _ratio(row.get("dkim_success_rate")),
        "dmarcSuccessRatio": _ratio(row.get("dmarc_success_rate")),
        "deliveryErrors": delivery,
        "ipReputations": ip_reps,
        "_source": row.get("_source", "db"),
        "_messageVolume": row.get("message_volume"),
    }


def _validate_domain(domain):
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", domain or ""):
        raise PostmasterError(f"Invalid domain: {domain!r}")


# --------------------------------------------------------------------------
# Source: EC2 over SSH
# --------------------------------------------------------------------------

def _ec2_row(domain):
    _validate_domain(domain)
    try:
        rows = ec2_data.query(
            EC2_GPT_DB,
            "SELECT * FROM gpt_data WHERE domain=? ORDER BY data_date DESC LIMIT 1",
            (domain,),
        )
    except ec2_data.EC2DataError as e:
        raise PostmasterError(str(e))
    row = rows[0] if rows else None
    if row is not None:
        row["_source"] = "ec2-db"
    return row


# --------------------------------------------------------------------------
# Source: local SQLite copy
# --------------------------------------------------------------------------

def _local_row(domain):
    _validate_domain(domain)
    if not LOCAL_GPT_DB or not Path(LOCAL_GPT_DB).exists():
        raise PostmasterError(f"Local gpt_data.db not found at {LOCAL_GPT_DB or '(unset)'}.")
    conn = sqlite3.connect(LOCAL_GPT_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM gpt_data WHERE domain=? ORDER BY data_date DESC LIMIT 1", (domain,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["_source"] = "local-db"
    return d


# --------------------------------------------------------------------------
# Source: live Google API (OAuth)
# --------------------------------------------------------------------------

def _get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise PostmasterError(
                    f"Missing {CREDENTIALS_PATH.name}. Needed only for POSTMASTER_SOURCE=live."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return creds


def _live_stats(domain):
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    service = build("gmailpostmastertools", "v1", credentials=_get_credentials(), cache_discovery=False)
    parent = f"domains/{domain}"
    try:
        stats = []
        request = service.domains().trafficStats().list(parent=parent)
        while request is not None:
            resp = request.execute()
            stats.extend(resp.get("trafficStats", []))
            request = service.domains().trafficStats().list_next(request, resp)
    except HttpError as e:
        status = getattr(e, "status_code", None) or getattr(e.resp, "status", None)
        if status in (403, 404):
            raise PostmasterError(f"No Postmaster access to '{domain}'. Verify it at postmaster.google.com.")
        raise PostmasterError(f"Postmaster API error for '{domain}': {e}")
    if not stats:
        return None
    stats.sort(key=lambda s: s["name"].rsplit("/", 1)[-1])
    latest = stats[-1]
    latest["_source"] = "google-api"
    return latest


# --------------------------------------------------------------------------
# Public dispatcher
# --------------------------------------------------------------------------

def get_stats(domain):
    """Return normalized Postmaster stats for a domain (or None), per SOURCE."""
    if SOURCE == "ec2":
        return _normalize_db_row(_ec2_row(domain))
    if SOURCE == "local":
        return _normalize_db_row(_local_row(domain))
    if SOURCE == "live":
        return _live_stats(domain)  # already in TrafficStats shape
    raise PostmasterError(f"Unknown POSTMASTER_SOURCE: {SOURCE!r} (use ec2, local, or live).")


def list_domains():
    """List domains that have Postmaster data in the active DB source."""
    if SOURCE == "ec2":
        rows = ec2_data.query(EC2_GPT_DB, "SELECT DISTINCT domain FROM gpt_data ORDER BY domain")
        return [r["domain"] for r in rows]
    if SOURCE == "local":
        conn = sqlite3.connect(LOCAL_GPT_DB)
        rows = [r[0] for r in conn.execute("SELECT DISTINCT domain FROM gpt_data ORDER BY domain")]
        conn.close()
        return rows
    return []
