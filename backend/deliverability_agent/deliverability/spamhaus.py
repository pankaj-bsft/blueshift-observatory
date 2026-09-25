"""Spamhaus DBL (domain blocklist) status/history from EC2 (deliverability_history.db).

Reads the same `spamhaus_cache` / `spamhaus_history` tables that power the
Pulsation page's Spamhaus badge (backend/spamhaus_service.py), through the
dual-mode ec2_data reader so the agent works the same from a laptop (SSH) or
on the EC2 host itself (local file). This module never writes to those
tables — refreshing the cache stays the job of the existing daily cron /
Pulsation view in the main app.

Domain blocklist (DBL) status only. There is no stored history for
IP-based Spamhaus lists (Zen/SBL/XBL) anywhere in the codebase today.
"""

from datetime import datetime, timedelta

from . import accounts, ec2_data
from .metrics import DELIV_DB


def _normalize(domain):
    return (domain or "").strip().lower()


def _today():
    return datetime.utcnow().date()


def get_current_status(domain):
    """Return {"status": ..., "checked_at": ...} for a domain, or None if never checked."""
    domain = _normalize(domain)
    if not domain:
        return None
    rows = ec2_data.query(
        DELIV_DB, "SELECT status, checked_at FROM spamhaus_cache WHERE domain = ?", (domain,)
    )
    return rows[0] if rows else None


def get_history(domain, days=30):
    """Return the daily [{checked_at, status}, ...] history for a domain in the last `days`."""
    domain = _normalize(domain)
    if not domain:
        return []
    cutoff = (_today() - timedelta(days=days)).strftime("%Y-%m-%d")
    return ec2_data.query(
        DELIV_DB,
        "SELECT checked_at, status FROM spamhaus_history "
        "WHERE domain = ? AND checked_at >= ? ORDER BY checked_at ASC",
        (domain, cutoff),
    )


def summarize_domain(domain):
    """Current + all-time listing summary for one domain.

    Returns None if the domain has never been checked (no cache row and no
    history), so callers can distinguish "never listed" from "no data".
    """
    domain = _normalize(domain)
    if not domain:
        return None
    current = get_current_status(domain)
    full_history = ec2_data.query(
        DELIV_DB,
        "SELECT checked_at, status FROM spamhaus_history WHERE domain = ? ORDER BY checked_at ASC",
        (domain,),
    )
    if not current and not full_history:
        return None

    history_map = {r["checked_at"]: r["status"] for r in full_history}
    first_listed = next((chk for chk in sorted(history_map) if history_map[chk] == "listed"), None)

    consecutive = 0
    day = _today()
    while history_map.get(day.strftime("%Y-%m-%d")) == "listed":
        consecutive += 1
        day -= timedelta(days=1)

    return {
        "domain": domain,
        "current_status": current["status"] if current else "unknown",
        "checked_at": current["checked_at"] if current else None,
        "ever_listed": first_listed is not None,
        "first_listed_date": first_listed,
        "consecutive_days_listed": consecutive,
        "history_days_available": len(history_map),
    }


def summarize_account(account_name, days=30):
    """Roll up Spamhaus status/trend across every sending domain mapped to an account."""
    mapped = accounts.account_to_domains(account_name)
    domains = sorted({_normalize(d["sending_domain"]) for d in mapped if d.get("sending_domain")})
    if not domains:
        return None

    placeholders = ",".join(["?"] * len(domains))
    status_rows = ec2_data.query(
        DELIV_DB, f"SELECT domain, status FROM spamhaus_cache WHERE domain IN ({placeholders})",
        tuple(domains),
    )
    status_map = {r["domain"]: r["status"] for r in status_rows}

    cutoff = (_today() - timedelta(days=days)).strftime("%Y-%m-%d")
    hist_rows = ec2_data.query(
        DELIV_DB,
        f"SELECT checked_at, domain, status FROM spamhaus_history "
        f"WHERE domain IN ({placeholders}) AND checked_at >= ? ORDER BY checked_at ASC",
        tuple(domains) + (cutoff,),
    )
    by_date = {}
    for r in hist_rows:
        by_date.setdefault(r["checked_at"], {})[r["domain"]] = r["status"]

    dates, listed_counts = [], []
    day, end = _today() - timedelta(days=days), _today()
    while day <= end:
        d_str = day.strftime("%Y-%m-%d")
        dates.append(d_str)
        listed_counts.append(sum(1 for d in domains if by_date.get(d_str, {}).get(d) == "listed"))
        day += timedelta(days=1)

    return {
        "account": account_name,
        "domains": domains,
        "status_map": status_map,
        "currently_listed": [d for d in domains if status_map.get(d) == "listed"],
        "trend": {"dates": dates, "listed_count": listed_counts},
    }


def recent_domains(days=30):
    """Distinct sending domains active in daily_metrics over the last `days` days."""
    cutoff = (_today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = ec2_data.query(
        DELIV_DB,
        "SELECT DISTINCT REPLACE(from_domain, '{% endif %}', '') AS domain "
        "FROM daily_metrics WHERE report_date >= ?",
        (cutoff,),
    )
    return sorted({_normalize(r["domain"]) for r in rows if r.get("domain")})


def historical_listings_summary(days=30):
    """Domains (among recently active senders) listed on Spamhaus DBL at any
    point in the last `days` days — covers both "listed right now" (pass a
    small window) and "listed at some point in the past N months" (pass a
    larger one). Each row reports the first/last listed date within the
    window, how many days it was listed, and its CURRENT status, so a
    since-resolved listing is distinguishable from an ongoing one.

    Domain scope matches recent_domains(days): only sending domains active
    in that same window are checked, mirroring the Pulsation page's badge.
    Spamhaus history is only available back to when collection started, so a
    window larger than that simply returns whatever history exists.
    """
    domains = recent_domains(days)
    if not domains:
        return []

    cutoff = (_today() - timedelta(days=days)).strftime("%Y-%m-%d")
    placeholders = ",".join(["?"] * len(domains))
    hist_rows = ec2_data.query(
        DELIV_DB,
        f"SELECT domain, checked_at FROM spamhaus_history "
        f"WHERE domain IN ({placeholders}) AND checked_at >= ? AND status = 'listed' "
        f"ORDER BY checked_at ASC",
        tuple(domains) + (cutoff,),
    )
    if not hist_rows:
        return []

    by_domain = {}
    for r in hist_rows:
        by_domain.setdefault(r["domain"], []).append(r["checked_at"])

    listed_domains = sorted(by_domain)
    placeholders2 = ",".join(["?"] * len(listed_domains))
    status_rows = ec2_data.query(
        DELIV_DB, f"SELECT domain, status FROM spamhaus_cache WHERE domain IN ({placeholders2})",
        tuple(listed_domains),
    )
    status_map = {r["domain"]: r["status"] for r in status_rows}

    summary = []
    for domain, dates in by_domain.items():
        dates_sorted = sorted(dates)
        summary.append({
            "domain": domain,
            "first_listed_in_window": dates_sorted[0],
            "last_listed_in_window": dates_sorted[-1],
            "days_listed_in_window": len(dates_sorted),
            "current_status": status_map.get(domain, "unknown"),
            "listed_dates": dates_sorted,
        })

    summary.sort(key=lambda s: s["last_listed_in_window"], reverse=True)
    return summary
