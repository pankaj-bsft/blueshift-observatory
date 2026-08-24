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

    # Fetch every sending domain (>=1 send) so population stats can describe what the
    # min_sent threshold excludes; min_sent is then applied to the ranked rows only.
    params_all = list(params)
    params_all[-1] = 1
    rows = ec2_data.query(DELIV_DB, base + where + "GROUP BY domain HAVING SUM(sent) >= ?", tuple(params_all))
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
    stats = _rank_population_stats(ranked, metric, order, min_sent_eff)

    # Rank only domains meeting the volume threshold; the rest stay in `stats`.
    qualifying = [d for d in ranked if d["sent"] >= min_sent_eff]
    qualifying.sort(key=lambda d: d.get(metric, 0), reverse=(order != "asc"))
    if not qualifying:
        return None

    return {
        "metric": metric, "days": days, "min_sent": min_sent, "order": order,
        "window": window, "total_domains": len(qualifying), "rows": qualifying[:limit],
        "population_domains": len(ranked),
        "stats": stats,
    }


# A rate computed on a handful of sends is noise: one bounce out of 4 reads as 25%.
# Rankings and threshold counts are reported against domains at or above this daily
# volume so a 12-send domain cannot outrank a million-send outage.
SIGNIFICANT_SEND_THRESHOLD = 1000

# Thresholds used for the "how many domains are actually in trouble" counts.
POOR_DELIVERY_PCT = 95.0
HIGH_BOUNCE_PCT = 2.0
HIGH_SPAM_PCT = 0.1


def _rank_population_stats(ranked, metric, order, min_sent_eff=None):
    """
    Summarise the WHOLE ranked population, not just the rows being returned.

    Without this the agent only ever sees `limit` rows, which on a normal day are
    dominated by domains sending a handful of messages, and it cannot say how much
    volume is affected or how many domains are genuinely in trouble.
    """
    total_domains = len(ranked)
    total_sent = sum(d["sent"] for d in ranked)
    total_delivered = sum(d["delivered"] for d in ranked)
    total_bounces = sum(d["bounces"] for d in ranked)

    cutoff = SIGNIFICANT_SEND_THRESHOLD if min_sent_eff in (None, 1) else min_sent_eff
    significant = [d for d in ranked if d["sent"] >= cutoff]
    minor = [d for d in ranked if d["sent"] < cutoff]
    minor_sent = sum(d["sent"] for d in minor)

    # delivered > sent is impossible; it comes from delivery events being attributed
    # to a day after the send. Flag rather than presenting a >100% delivery rate.
    suspect = [d for d in ranked if d["delivered"] > d["sent"]]

    def _band(lo, hi):
        rows = [d for d in ranked if d["sent"] >= lo and (hi is None or d["sent"] < hi)]
        return {"domains": len(rows), "sent": sum(d["sent"] for d in rows)}

    # Impact ordering: a bad rate matters in proportion to the volume behind it.
    def _impact(d):
        if metric == "delivery_rate":
            return (100.0 - d["delivery_rate"]) / 100.0 * d["sent"]  # undelivered mail
        if metric in ("bounce_rate", "spam_rate", "unsub_rate"):
            return d.get(metric, 0) / 100.0 * d["sent"]
        return d.get(metric, 0)

    by_impact = sorted(significant, key=_impact, reverse=True)[:10]

    return {
        "total_domains": total_domains,
        "total_sent": total_sent,
        # Volume-weighted, so one tiny domain cannot move the headline figure.
        "overall_delivery_rate": _rate(total_delivered, total_sent),
        "overall_bounce_rate": _rate(total_bounces, total_sent),
        "significant_threshold": cutoff,
        "significant_domains": len(significant),
        "minor_domains": len(minor),
        "minor_sent": minor_sent,
        "minor_pct_of_volume": _rate(minor_sent, total_sent),
        "bands": {
            "under_100": _band(1, 100),
            "100_to_999": _band(100, 1000),
            "1k_to_10k": _band(1000, 10000),
            "over_10k": _band(10000, None),
        },
        "concern_counts_significant_only": {
            "delivery_under_95": len([d for d in significant if d["delivery_rate"] < POOR_DELIVERY_PCT]),
            "bounce_over_2": len([d for d in significant if d["bounce_rate"] > HIGH_BOUNCE_PCT]),
            "spam_over_0_1": len([d for d in significant if d["spam_rate"] > HIGH_SPAM_PCT]),
        },
        "suspect_rows": [
            {"domain": d["domain"], "sent": d["sent"], "delivered": d["delivered"]}
            for d in suspect[:10]
        ],
        "suspect_count": len(suspect),
        "top_by_impact": [
            {
                "domain": d["domain"], "sent": d["sent"],
                "delivery_rate": d["delivery_rate"], "bounce_rate": d["bounce_rate"],
                "spam_rate": d["spam_rate"],
                "affected_messages": int(round(_impact(d))),
            }
            for d in by_impact
        ],
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
