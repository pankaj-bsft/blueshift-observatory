"""Email volume/engagement metrics from EC2 (deliverability_history.db).

Reads the `daily_metrics` table — per domain / region / ESP daily rows with
sent, delivered, bounces, spam complaints, unsubscribes — cached from Druid.
Rates are recomputed from summed counts (weighted correctly), not by averaging
the stored per-row rate columns.
"""

import json
import os

from . import ec2_data

DELIV_DB = os.getenv("EC2_DELIV_DB", f"{ec2_data.DATA_DIR.rstrip('/')}/deliverability_history.db")
GPT_DB = os.getenv("EC2_GPT_DB", f"{ec2_data.DATA_DIR.rstrip('/')}/gpt_data.db")

_COLS = ("report_date", "region", "esp", "sent", "delivered", "bounces",
         "soft_bounce_count", "spam_report", "unsubscribe")


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _rate(numerator, denominator):
    return round(numerator / denominator * 100, 4) if denominator else 0.0


def _totals(rows):
    t = {k: sum(_num(r.get(k)) for r in rows) for k in ("sent", "delivered", "bounces", "spam_report", "unsubscribe")}
    t["delivery_rate"] = _rate(t["delivered"], t["sent"])
    t["bounce_rate"] = _rate(t["bounces"], t["sent"])
    t["spam_rate"] = _rate(t["spam_report"], t["delivered"])
    t["unsub_rate"] = _rate(t["unsubscribe"], t["delivered"])
    return t


def get_domain_metrics(domain, days=30):
    """Return a metrics summary for a domain, or None if there's no data.

    Handles the pipeline's '{% endif %}'-suffixed from_domain values.
    """
    sql = (
        f"SELECT {', '.join(_COLS)} FROM daily_metrics "
        "WHERE from_domain = ? OR from_domain LIKE ? "
        "ORDER BY report_date DESC"
    )
    rows = ec2_data.query(DELIV_DB, sql, (domain, domain + "{%"))
    if not rows:
        return None

    latest_date = rows[0]["report_date"]
    latest_rows = [r for r in rows if r["report_date"] == latest_date]

    recent_dates = sorted({r["report_date"] for r in rows}, reverse=True)[:days]
    period_rows = [r for r in rows if r["report_date"] in recent_dates]

    by_esp = [
        {
            "esp": r.get("esp"),
            "region": r.get("region"),
            "sent": int(_num(r.get("sent"))),
            "delivered": int(_num(r.get("delivered"))),
            "delivery_rate": _rate(_num(r.get("delivered")), _num(r.get("sent"))),
            "bounce_rate": _rate(_num(r.get("bounces")), _num(r.get("sent"))),
        }
        for r in latest_rows
    ]

    return {
        "domain": domain,
        "latest_date": latest_date,
        "latest": _totals(latest_rows),
        "by_esp": by_esp,
        "period_days": len(recent_dates),
        "period": _totals(period_rows),
        "source": "ec2-db (deliverability_history.daily_metrics)",
    }


def get_domain_trend(domain, days=7):
    """Return a per-date time series for a domain (aggregated across ESP/region)."""
    sql = (
        f"SELECT {', '.join(_COLS)} FROM daily_metrics "
        "WHERE from_domain = ? OR from_domain LIKE ? "
        "ORDER BY report_date ASC"
    )
    rows = ec2_data.query(DELIV_DB, sql, (domain, domain + "{%"))
    if not rows:
        return None

    by_date = {}
    for r in rows:
        by_date.setdefault(r["report_date"], []).append(r)

    dates = sorted(by_date.keys())[-days:]
    series = [dict(date=d, **_totals(by_date[d])) for d in dates]
    return {"domain": domain, "days": len(dates), "series": series}


_RANK_METRICS = {"bounce_rate", "delivery_rate", "spam_rate", "unsub_rate", "sent", "delivered", "bounces"}


def available_range():
    r = ec2_data.query(DELIV_DB, "SELECT MIN(report_date) mn, MAX(report_date) mx FROM daily_metrics")
    return (r[0]["mn"], r[0]["mx"]) if r else (None, None)


def rank_domains(metric="bounce_rate", min_sent=0, limit=10, days=7, order="desc",
                 start_date=None, end_date=None):
    """Rank ALL sending domains by a metric over a window or explicit date range.

    Aggregates daily_metrics across domains (merging the pipeline's
    '{% endif %}' suffix), filters by total sends, and sorts. Provide
    start_date (+optional end_date, YYYY-MM-DD) for a specific range, else the
    rolling last-`days` window is used. Zero-send domains are always excluded.
    """
    if metric not in _RANK_METRICS:
        metric = "bounce_rate"
    min_sent_eff = max(int(min_sent or 0), 1)  # never surface 0-send domains
    base = (
        "SELECT REPLACE(from_domain, '{% endif %}', '') AS domain, "
        "SUM(sent) sent, SUM(delivered) delivered, SUM(bounces) bounces, "
        "SUM(spam_report) spam_report, SUM(unsubscribe) unsubscribe "
        "FROM daily_metrics "
    )
    if start_date and end_date:
        where, params, window = "WHERE report_date BETWEEN ? AND ? ", [start_date, end_date, min_sent_eff], f"{start_date} → {end_date}"
    elif start_date:
        where, params, window = "WHERE report_date = ? ", [start_date, min_sent_eff], start_date
    else:
        where, params, window = ("WHERE report_date >= date((SELECT MAX(report_date) FROM daily_metrics), ?) ",
                                 [f"-{max(days - 1, 0)} day", min_sent_eff], f"last {days}d")

    rows = ec2_data.query(DELIV_DB, base + where + "GROUP BY domain HAVING SUM(sent) >= ?", tuple(params))
    if not rows:
        return None

    ranked = []
    for r in rows:
        sent, delivered = _num(r["sent"]), _num(r["delivered"])
        ranked.append({
            "domain": r["domain"],
            "sent": int(sent),
            "delivered": int(delivered),
            "bounces": int(_num(r["bounces"])),
            "delivery_rate": _rate(delivered, sent),
            "bounce_rate": _rate(_num(r["bounces"]), sent),
            "spam_rate": _rate(_num(r["spam_report"]), delivered),
            "unsub_rate": _rate(_num(r["unsubscribe"]), delivered),
        })
    ranked.sort(key=lambda d: d.get(metric, 0), reverse=(order != "asc"))
    return {
        "metric": metric, "days": days, "min_sent": min_sent, "order": order,
        "window": window, "total_domains": len(ranked), "rows": ranked[:limit],
    }


def reputation_for(domains):
    """Batch-fetch latest Gmail Postmaster reputation for a list of domains.

    Returns {domain: {"reputation": str, "ip": str}}. Domains without Postmaster
    data are simply absent from the dict (caller shows '—').
    """
    if not domains:
        return {}
    placeholders = ",".join("?" * len(domains))
    rows = ec2_data.query(
        GPT_DB,
        f"SELECT domain, reputation, ip_reputation, MAX(data_date) AS d "
        f"FROM gpt_data WHERE domain IN ({placeholders}) GROUP BY domain",
        tuple(domains),
    )
    out = {}
    for r in rows:
        ip = ""
        raw = r.get("ip_reputation")
        if raw:
            try:
                breakdown = (json.loads(raw).get("breakdown") or {})
                ip = ", ".join(f"{k}×{v}" for k, v in breakdown.items())
            except (ValueError, TypeError):
                pass
        out[r["domain"]] = {"reputation": r.get("reputation") or "", "ip": ip}
    return out
