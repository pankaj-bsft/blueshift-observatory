import os
import json
import re

from langchain.tools import tool

from .deliverability import DNSChecker, SPFEvaluator, analyzer, postmaster, metrics
from .deliverability import ec2_data, accounts, mbr

SEV_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵", "ok": "✅"}


def _render(findings):
    counts = analyzer.summarize(findings)
    header = (
        f"Summary: {counts['critical']} critical, {counts['warning']} warning, "
        f"{counts['info']} info, {counts['ok']} ok"
    )
    lines = [header, ""]
    for f in findings:
        icon = SEV_ICON.get(f["severity"], "•")
        lines.append(f"{icon} [{f['check']}] {f['message']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# UI payload channel. Tools use LangChain's content_and_artifact response
# format: they return (content, artifact). The LLM only sees `content` (short
# readable text) — the structured UI payload (tiles/findings/charts/tables)
# rides along as the ToolMessage.artifact, which run_agent reads for the
# frontend. Keeping the big JSON OUT of the LLM's context prevents the small
# model from looping and improves follow-up handling.
# --------------------------------------------------------------------------

def _emit(text, stats=None, findings=None, charts=None, tables=None):
    payload = {
        "stats": stats or [], "findings": findings or [],
        "charts": charts or [], "tables": tables or [],
    }
    return text, payload


def _text(message):
    """A content_and_artifact return with no UI payload."""
    return message, {"stats": [], "findings": [], "charts": [], "tables": []}


# --- stat-tile helpers (tone drives the frontend color) -------------------

def _stat(label, value, tone="neutral", sub=""):
    return {"label": label, "value": value, "tone": tone, "sub": sub}


def _tone_delivery(dr):
    return "good" if dr >= 98 else "warn" if dr >= 95 else "bad"


def _tone_bounce(br):
    return "good" if br < 2 else "warn" if br < 5 else "bad"


def _tone_spam(sr):
    return "good" if sr < 0.1 else "warn" if sr < 0.3 else "bad"


def _tone_rep(rep):
    return {"HIGH": "good", "MEDIUM": "neutral", "LOW": "warn", "BAD": "bad"}.get(rep, "neutral")


def _metric_stats(summary):
    latest = summary["latest"]
    # On trivially small volume, rates aren't meaningful — keep tones neutral.
    low_vol = latest["sent"] < 100
    dtone = "neutral" if low_vol else _tone_delivery(latest["delivery_rate"])
    btone = "neutral" if low_vol else _tone_bounce(latest["bounce_rate"])
    stone = "neutral" if low_vol else _tone_spam(latest["spam_rate"])
    return [
        _stat("Sent", f"{int(latest['sent']):,}", "neutral", summary["latest_date"]),
        _stat("Delivered", f"{int(latest['delivered']):,}", "neutral", summary["latest_date"]),
        _stat("Delivery rate", f"{latest['delivery_rate']}%", dtone),
        _stat("Bounce rate", f"{latest['bounce_rate']}%", btone),
        _stat("Complaint rate", f"{latest['spam_rate']}%", stone),
    ]


# --------------------------------------------------------------------------
# Shared finding builders — used by both the single-purpose tools and the
# composite full_deliverability_report. Each returns a list of findings and
# degrades gracefully (never raises) so the composite always completes.
# --------------------------------------------------------------------------

def _auth_findings(domain, dkim_selector=""):
    findings = []
    spf_record = DNSChecker.get_spf(domain)
    findings += analyzer.analyze_spf(spf_record)
    if spf_record:
        findings += analyzer.analyze_spf_lookups(SPFEvaluator().evaluate(domain))

    if dkim_selector:
        hit = DNSChecker.get_dkim(domain, dkim_selector)
        dkim_hits = [hit] if hit else []
    else:
        dkim_hits = DNSChecker.probe_dkim(domain)
    findings += analyzer.analyze_dkim(dkim_hits)

    findings += analyzer.analyze_dmarc(DNSChecker.get_dmarc_with_source(domain))
    findings += analyzer.analyze_mx(DNSChecker.get_mx(domain))
    findings += analyzer.analyze_optional("BIMI", DNSChecker.get_bimi(domain))
    findings += analyzer.analyze_optional("MTA-STS", DNSChecker.get_mta_sts(domain))
    findings += analyzer.analyze_optional("TLS-RPT", DNSChecker.get_tls_rpt(domain))
    return findings


def _postmaster_bundle(domain):
    """Return (findings, stat_tiles) for Gmail Postmaster; never raises."""
    try:
        stats = postmaster.get_stats(domain)
    except postmaster.PostmasterError as e:
        return ([{"check": "Postmaster", "severity": "info", "message": f"Reputation data unavailable: {e}"}], [])
    findings = analyzer.analyze_postmaster(stats)
    tiles = []
    if stats:
        rep = stats.get("domainReputation") or ""
        tiles.append(_stat("Gmail reputation", rep.title() if rep else "—", _tone_rep(rep)))
        spam = stats.get("userReportedSpamRatio")
        if spam is not None:
            tiles.append(_stat("Spam rate", f"{spam * 100:.2f}%", _tone_spam(spam * 100)))
    return (findings, tiles)


def _metrics_bundle(domain):
    """Return (findings, stat_tiles) for send/delivery metrics; never raises."""
    try:
        summary = metrics.get_domain_metrics(domain)
    except ec2_data.EC2DataError as e:
        return ([{"check": "Metrics", "severity": "info", "message": f"Send/delivery data unavailable: {e}"}], [])
    findings = analyzer.analyze_metrics(summary)
    tiles = _metric_stats(summary) if summary else []
    return (findings, tiles)


@tool(response_format="content_and_artifact")
def check_email_authentication(domain: str, dkim_selector: str = "") -> str:
    """Run a full email-deliverability authentication audit for a domain.

    Checks SPF, DKIM, DMARC, MX, BIMI, MTA-STS and TLS-RPT, then returns
    severity-tagged findings (critical/warning/info/ok). Use this whenever a
    user asks about a domain's email setup, deliverability, or authentication.

    Args:
        domain: the domain to audit, e.g. "example.com".
        dkim_selector: optional DKIM selector if known. If omitted, common
            ESP selectors are probed automatically.
    """
    print(f"\n🛠  Deliverability audit: {domain} (selector={dkim_selector or 'auto'})")
    findings = _auth_findings(domain, dkim_selector)
    return _emit(f"Domain: {domain}\n\n" + _render(findings), findings=findings)


def _render_spf_tree(node, prefix=""):
    lines = []
    for t in node["terms"]:
        cost = f"  ({t['cost']})" if t.get("cost") else ""
        tag = " [ignored]" if t.get("ignored") else ""
        lines.append(f"{prefix}{t['term']}{cost}{tag}")
        child = t.get("child")
        if child:
            if child.get("loop"):
                lines.append(f"{prefix}    ↳ ⚠ LOOP")
            elif child.get("void"):
                lines.append(f"{prefix}    ↳ ⚠ no SPF record")
            else:
                lines += _render_spf_tree(child, prefix + "    ")
    return lines


@tool
def check_spf(domain: str) -> str:
    """Recursively evaluate a domain's SPF record and count DNS lookups.

    Walks the entire include/redirect tree and counts DNS-querying mechanisms
    against the RFC 7208 limit of 10 (exceeding it causes PermError and SPF
    failure). Use this when the user asks specifically about SPF, SPF lookups,
    "too many DNS lookups", PermError, or flattening SPF.
    """
    print(f"\n🛠  SPF recursive evaluation: {domain}")
    ev = SPFEvaluator().evaluate(domain)
    if ev["record"] is None:
        return f"No SPF record found for {domain}."

    findings = analyzer.analyze_spf(ev["record"]) + analyzer.analyze_spf_lookups(ev)
    out = [f"Domain: {domain}", f"Record: {ev['record']}", ""]
    out.append(f"DNS lookups: {ev['lookups']}/{ev['limit']}"
               + ("  🔴 OVER LIMIT (PermError)" if ev["exceeded"] else "  ✅"))
    out.append("")
    out.append("Include tree (numbers = lookup cost):")
    out += _render_spf_tree(ev["tree"], "  ")
    out.append("")
    out.append(_render(findings))
    return "\n".join(out)


@tool
def lookup_dkim(domain: str, selector: str) -> str:
    """Look up a specific DKIM key for a domain and selector.

    Use when the user knows their DKIM selector (e.g. 'google', 'selector1')
    and wants to verify that key directly.
    """
    print(f"\n🛠  DKIM lookup: {selector}._domainkey.{domain}")
    hit = DNSChecker.get_dkim(domain, selector)
    if not hit:
        return f"No DKIM record at {selector}._domainkey.{domain}"
    return _render(analyzer.analyze_dkim([hit])) + f"\n\nRaw: {hit['record']}"


@tool(response_format="content_and_artifact")
def check_gmail_reputation(domain: str) -> str:
    """Fetch real Gmail reputation & delivery data from Google Postmaster Tools.

    Returns domain/IP reputation, user-reported spam rate, SPF/DKIM/DMARC pass
    ratios on actual mail, and delivery errors. Use this when the user asks
    about their Gmail reputation, spam rate, inbox placement, or why mail is
    going to spam. Requires the domain to be verified in Postmaster Tools.
    """
    print(f"\n🛠  Gmail Postmaster lookup: {domain} (source={postmaster.SOURCE})")
    findings, tiles = _postmaster_bundle(domain)
    return _emit(f"Domain: {domain}\n\n" + _render(findings), findings=findings, stats=tiles)


@tool(response_format="content_and_artifact")
def check_email_metrics(domain: str) -> str:
    """Fetch send/delivery metrics for a domain from EC2 (Blueshift data).

    Returns volume (sent/delivered), delivery rate, bounce rate, and complaint
    rate — per ESP for the latest day plus a recent-period rollup. Use this when
    the user asks about sends, delivered, volume, bounce rate, delivery rate, or
    how a domain's mail is performing.
    """
    print(f"\n🛠  Email metrics (EC2): {domain}")
    try:
        summary = metrics.get_domain_metrics(domain)
    except ec2_data.EC2DataError as e:
        return _text(f"⚠ {e}")

    findings = analyzer.analyze_metrics(summary)
    tiles = _metric_stats(summary) if summary else []

    out = [f"Domain: {domain}"]
    if summary:
        out.append(f"Data source: {summary['source']}")
        out.append("")
        out.append(f"Latest day ({summary['latest_date']}) by ESP:")
        for e in summary["by_esp"]:
            out.append(f"  {e['esp']}/{e['region']}: sent {e['sent']:,}, delivered {e['delivered']:,} "
                       f"({e['delivery_rate']}% delivery, {e['bounce_rate']}% bounce)")
    out.append("")
    out.append(_render(findings))
    return _emit("\n".join(out), findings=findings, stats=tiles)


@tool(response_format="content_and_artifact")
def full_deliverability_report(domain: str, dkim_selector: str = "") -> str:
    """Run a COMPLETE deliverability analysis for a domain in one call.

    Combines everything: DNS authentication (SPF/DKIM/DMARC/MX/BIMI/MTA-STS/
    TLS-RPT), Gmail reputation (Postmaster), and send/delivery metrics
    (sends, delivered, bounce & complaint rates). Use this whenever the user
    asks for a "complete", "full", "overall", or "end-to-end" deliverability
    check/analysis/report/health for a domain — it is the single source for
    all of it, so you do NOT need to call the other check_* tools too.
    """
    print(f"\n🛠  FULL deliverability report: {domain}")
    auth = _auth_findings(domain, dkim_selector)
    rep, rep_tiles = _postmaster_bundle(domain)
    met, met_tiles = _metrics_bundle(domain)

    overall = analyzer.summarize(auth + rep + met)
    out = [
        f"COMPLETE DELIVERABILITY REPORT — {domain}",
        f"Overall: {overall['critical']} critical, {overall['warning']} warning, "
        f"{overall['info']} info, {overall['ok']} ok",
        "",
        "═══ 1. Authentication (DNS) ═══",
        _render(auth),
        "",
        "═══ 2. Gmail Reputation (Postmaster) ═══",
        _render(rep),
        "",
        "═══ 3. Send / Delivery Metrics (EC2) ═══",
        _render(met),
    ]
    return _emit("\n".join(out), findings=auth + rep + met, stats=met_tiles + rep_tiles)


def _trend_charts(trend):
    s = trend["series"]
    labels = [row["date"][5:] for row in s]  # MM-DD
    return [
        {
            "type": "line", "title": "Volume — sent vs delivered", "labels": labels,
            "datasets": [
                {"label": "Sent", "data": [int(r["sent"]) for r in s], "color": "#93c5fd"},
                {"label": "Delivered", "data": [int(r["delivered"]) for r in s], "color": "#3b82f6"},
            ],
        },
        {
            "type": "line", "title": "Delivery rate (%)", "labels": labels,
            "datasets": [
                {"label": "Delivery rate", "data": [r["delivery_rate"] for r in s], "color": "#22c55e"},
            ],
        },
        {
            "type": "line", "title": "Bounce & complaint rate (%)", "labels": labels,
            "datasets": [
                {"label": "Bounce rate", "data": [r["bounce_rate"] for r in s], "color": "#f59e0b"},
                {"label": "Complaint rate", "data": [r["spam_rate"] for r in s], "color": "#ef4444"},
            ],
        },
    ]


@tool(response_format="content_and_artifact")
def email_metrics_trend(domain: str, days: int = 7) -> str:
    """Show email-metric TRENDS over time for a domain, with charts.

    Use whenever the user asks to see a trend, chart, graph, "over time",
    "past N days", "last week", or how metrics are changing. Returns sent,
    delivered and delivery/bounce/complaint rates per day and renders trend
    charts. Default window is 7 days; pass `days` for a different window.
    """
    print(f"\n🛠  Email metrics trend (EC2): {domain} ({days}d)")
    try:
        trend = metrics.get_domain_trend(domain, days)
    except ec2_data.EC2DataError as e:
        return _text(f"⚠ {e}")
    if not trend:
        return _text(f"No send/delivery metrics found for {domain}.")

    charts = _trend_charts(trend)
    tiles = []
    try:
        summary = metrics.get_domain_metrics(domain)
        if summary:
            tiles = _metric_stats(summary)
    except ec2_data.EC2DataError:
        pass

    first, last = trend["series"][0], trend["series"][-1]
    lines = [f"Domain: {domain} — {trend['days']}-day trend ({first['date']} → {last['date']})", ""]
    for r in trend["series"]:
        lines.append(
            f"  {r['date']}: sent {int(r['sent']):,}, delivered {int(r['delivered']):,}, "
            f"delivery {r['delivery_rate']}%, bounce {r['bounce_rate']}%, complaint {r['spam_rate']}%"
        )
    return _emit("\n".join(lines), charts=charts, stats=tiles)


_METRIC_LABEL = {
    "bounce_rate": "Bounce rate %", "delivery_rate": "Delivery rate %",
    "spam_rate": "Complaint rate %", "unsub_rate": "Unsub rate %",
    "sent": "Sent", "delivered": "Delivered", "bounces": "Bounces",
}
_METRIC_COLOR = {
    "bounce_rate": "#f59e0b", "spam_rate": "#ef4444", "unsub_rate": "#a855f7",
    "delivery_rate": "#22c55e", "sent": "#3b82f6", "delivered": "#3b82f6", "bounces": "#f59e0b",
}


@tool(response_format="content_and_artifact")
def rank_sending_domains(metric: str = "bounce_rate", min_sent: int = 0,
                         limit: int = 10, days: int = 7, order: str = "desc",
                         start_date: str = "", end_date: str = "") -> str:
    """Rank or compare sending domains across ALL domains (leaderboards).

    Use for cross-domain questions like "top 10 domains by bounce rate",
    "which domains have POOR delivery" (metric=delivery_rate, order=asc),
    "highest complaint rate", or "biggest senders" — optionally for a specific
    date or date range, and optionally filtered by minimum volume.

    Args:
        metric: bounce_rate, delivery_rate, spam_rate, unsub_rate, sent,
            delivered, bounces. ("poor delivery/deliverability" -> delivery_rate
            with order=asc; "poor" bounce/spam -> those with order=desc.)
        min_sent: only include domains whose TOTAL sends in the window are >= this.
        limit: how many domains to return (default 10).
        days: rolling lookback window in days (used only if no dates given).
        order: "desc" (highest-first) or "asc" (lowest-first).
        start_date: YYYY-MM-DD. With end_date, ranks that inclusive range; alone,
            ranks just that day. Empty = use the rolling `days` window.
        end_date: YYYY-MM-DD upper bound of the range.
    """
    print(f"\n🛠  Rank domains: metric={metric} min_sent={min_sent} limit={limit} "
          f"days={days} order={order} range={start_date or '-'}..{end_date or '-'}")
    try:
        result = metrics.rank_domains(metric, min_sent, limit, days, order,
                                      start_date or None, end_date or None)
    except ec2_data.EC2DataError as e:
        return _text(f"⚠ {e}")
    if not result or not result["rows"]:
        try:
            mn, mx = metrics.available_range()
        except ec2_data.EC2DataError:
            mn = mx = "unknown"
        rng = f"{start_date or 'window'}" + (f" → {end_date}" if end_date else "")
        return _text(f"No sending data found for {rng} (min sends {max(int(min_sent or 0),1):,}). "
                f"Available data covers {mn} → {mx}.")

    m = result["metric"]
    label = _METRIC_LABEL.get(m, m)
    rows = result["rows"]
    is_rate = m.endswith("_rate")
    window = result["window"]

    chart = {
        "type": "bar", "horizontal": True,
        "title": f"{'Bottom' if order == 'asc' else 'Top'} {len(rows)} domains by {label} ({window})",
        "labels": [r["domain"] for r in rows],
        "datasets": [{"label": label, "data": [r[m] for r in rows], "color": _METRIC_COLOR.get(m, "#3b82f6")}],
    }

    # Join Gmail Postmaster reputation for the ranked domains (one batch query).
    try:
        reps = metrics.reputation_for([r["domain"] for r in rows])
    except ec2_data.EC2DataError:
        reps = {}

    table = {
        "title": f"{'Bottom' if order == 'asc' else 'Top'} {len(rows)} of "
                 f"{result['total_domains']} domains by {label} ({window})",
        "columns": [
            {"label": "#", "align": "right"},
            {"label": "Domain", "align": "left"},
            {"label": "Sent", "align": "right"},
            {"label": "Bounce %", "align": "right"},
            {"label": "Delivery %", "align": "right"},
            {"label": "Complaint %", "align": "right"},
            {"label": "Gmail rep", "align": "left"},
            {"label": "IP rep", "align": "left"},
        ],
        "rows": [
            [i, r["domain"], f"{r['sent']:,}",
             f"{r['bounce_rate']}%", f"{r['delivery_rate']}%", f"{r['spam_rate']}%",
             (reps.get(r["domain"], {}).get("reputation") or "—"),
             (reps.get(r["domain"], {}).get("ip") or "—")]
            for i, r in enumerate(rows, 1)
        ],
    }
    n_with_rep = sum(1 for r in rows if reps.get(r["domain"], {}).get("reputation"))

    top = rows[0]
    top_val = f"{top[m]}%" if is_rate else f"{int(top[m]):,}"
    summary = (
        f"Ranked {result['total_domains']} domains by {label} ({window}, "
        f"{'lowest' if order == 'asc' else 'highest'} first). "
        f"Worst/Top: {top['domain']} at {top_val}. "
        f"Gmail domain & IP reputation are already joined into the table "
        f"({n_with_rep}/{len(rows)} domains have Postmaster data; the rest show '—' "
        "because they aren't in Postmaster Tools). "
        "The full table and chart are shown to the user — give a brief takeaway "
        "(outliers, patterns). Do NOT re-list every row and do NOT invent reputation "
        "for domains marked '—'."
    )
    return _emit(summary, charts=[chart], tables=[table])


def _fmt_pct(v):
    return f"{v}%" if v is not None and v != "" else "—"


@tool(response_format="content_and_artifact")
def mbr_deliverability(month: str = "", year: str = "", report_type: str = "account",
                       entity: str = "") -> str:
    """Fetch a Monthly Business Review (MBR) deliverability report.

    Monthly rollups of deliverability & engagement per account (or domain):
    sent, delivered, delivery/bounce/spam rates, opens, clicks, and
    month-over-month send change. Use for "MBR", "monthly business review",
    "monthly report", "top accounts this month", or a specific month.

    The reply includes each ranked row's real figures plus period aggregates, so you can
    compare entities, spot outliers and answer "who is worst on bounce/spam" or "who grew
    most month-over-month" directly from it.

    Args:
        month: month name or number (e.g. "June" or "6"); empty = latest.
        year: e.g. "2026"; empty = latest.
        report_type: "account" (default) or "domain". Use "domain" when the user asks
            about sending domains, "account" when they ask about accounts or customers.
        entity: optional account or domain name to focus on (e.g. "fivebelow.com").
            Set this when the user asks how ONE account/domain performed. The MBR only
            holds the ranked top entities, so if the name is not there the reply says so —
            use rank_sending_domains for arbitrary domains outside the ranking.
    """
    print(f"\n🛠  MBR report: month={month or 'latest'} year={year or 'latest'} "
          f"type={report_type} entity={entity or '-'}")
    rtype = report_type or "account"
    try:
        rep = mbr.get_report(mbr.parse_month(month), year or None, rtype)
    except ec2_data.EC2DataError as e:
        return _text(f"⚠ {e}")
    if not rep:
        avail = mbr.list_reports(rtype)
        periods = "; ".join(f"{r['from_date']}→{r['to_date']}" for r in avail[:12]) or "none"
        return _text(f"No MBR '{rtype}' report found for that period. Available periods: {periods}")

    meta, data = rep["meta"], rep["data"]
    entities, name_field = mbr.top_entities(data)

    tiles = [
        _stat("Period", f"{meta['from_date']} → {meta['to_date']}", "neutral"),
        _stat(f"{'Accounts' if rtype == 'account' else 'Domains'}",
              f"{(meta.get('total_accounts') or meta.get('total_domains') or 0):,}", "neutral"),
    ]

    table = {
        "title": f"MBR top {name_field.lower()}s — {meta['from_date']} → {meta['to_date']}",
        "columns": [
            {"label": "#", "align": "right"},
            {"label": name_field, "align": "left"},
            {"label": "Sent", "align": "right"},
            {"label": "Delivered", "align": "right"},
            {"label": "Delivery %", "align": "right"},
            {"label": "Bounce %", "align": "right"},
            {"label": "Spam %", "align": "right"},
            {"label": "Open %", "align": "right"},
            {"label": "Click %", "align": "right"},
            {"label": "MoM send %", "align": "right"},
        ],
        "rows": [
            [a.get("Rank", i), a.get(name_field, "—"),
             f"{int(a.get('Sent', 0) or 0):,}", f"{int(a.get('Delivered', 0) or 0):,}",
             _fmt_pct(a.get("Delivery_Rate_%")), _fmt_pct(a.get("Bounce_Rate_%")),
             _fmt_pct(a.get("Spam_Rate_%")), _fmt_pct(a.get("Open_Rate_%")),
             _fmt_pct(a.get("Click_Rate_%")), _fmt_pct(a.get("MoM_Send_Change_%"))]
            for i, a in enumerate(entities, 1)
        ],
    }

    chart = {
        "type": "bar", "horizontal": True,
        "title": f"Top {name_field.lower()}s by volume (Sent)",
        "labels": [a.get(name_field, "—") for a in entities],
        "datasets": [{"label": "Sent", "data": [a.get("Sent", 0) or 0 for a in entities], "color": "#3b82f6"}],
    }

    top = entities[0] if entities else {}
    total_count = meta.get('total_accounts') or meta.get('total_domains') or 0
    lines = [
        f"MBR {rtype} report for {meta['from_date']} → {meta['to_date']} "
        f"({total_count:,} {name_field.lower()}s tracked; the ranking below holds the "
        f"top {len(entities)} by volume)."
    ]

    # (B) Focused lookup when the user asked about one account/domain. Exact match first,
    # then a substring match so "fivebelow" finds "fivebelow.com".
    if entity:
        needle = entity.strip().lower()
        match = next((e for e in entities if str(e.get(name_field, '')).lower() == needle), None)
        if match is None:
            match = next((e for e in entities if needle in str(e.get(name_field, '')).lower()), None)
        if match is None:
            # Account names in this data look like domains (e.g. "fivebelow.com" is an
            # account), so a lookup easily lands on the wrong report_type. Check the other
            # report before declaring the entity absent.
            other = "domain" if rtype == "account" else "account"
            alt_match, alt_field = None, None
            try:
                alt_rep = mbr.get_report(mbr.parse_month(month), year or None, other)
                if alt_rep:
                    alt_entities, alt_field = mbr.top_entities(alt_rep["data"])
                    alt_match = next(
                        (e for e in alt_entities
                         if str(e.get(alt_field, '')).lower() == needle
                         or needle in str(e.get(alt_field, '')).lower()),
                        None,
                    )
            except Exception:
                alt_match = None

            if alt_match is not None:
                lines.append(
                    f"'{entity}' is not in the {rtype} ranking, but IT IS in the {other} "
                    f"ranking — {alt_match.get(alt_field)}: rank {alt_match.get('Rank', '—')}, "
                    f"sent {int(alt_match.get('Sent', 0) or 0):,}, "
                    f"{_fmt_pct(alt_match.get('Delivery_Rate_%'))} delivery, "
                    f"bounce {_fmt_pct(alt_match.get('Bounce_Rate_%'))}, "
                    f"spam {_fmt_pct(alt_match.get('Spam_Rate_%'))}, "
                    f"MoM {_fmt_pct(alt_match.get('MoM_Send_Change_%'))}. "
                    f"Quote these figures and say they come from the {other}-level MBR. "
                    f"The table shown is the {rtype} view."
                )
            else:
                names = ", ".join(str(e.get(name_field, '—')) for e in entities)
                lines.append(
                    f"'{entity}' is NOT in the ranked {name_field.lower()}s of either the "
                    f"{rtype} or {other} MBR, so its MBR figures are unavailable. Say so "
                    f"plainly — do not estimate. Ranked {name_field.lower()}s are: {names}. "
                    f"For a domain outside the ranking, use rank_sending_domains instead."
                )
        else:
            lines.append(
                f"FOCUS — {match.get(name_field)}: rank {match.get('Rank', '—')} of "
                f"{len(entities)}, sent {int(match.get('Sent', 0) or 0):,}, delivered "
                f"{int(match.get('Delivered', 0) or 0):,} "
                f"({_fmt_pct(match.get('Delivery_Rate_%'))} delivery), "
                f"bounce {_fmt_pct(match.get('Bounce_Rate_%'))}, "
                f"spam {_fmt_pct(match.get('Spam_Rate_%'))}, "
                f"open {_fmt_pct(match.get('Open_Rate_%'))}, "
                f"click {_fmt_pct(match.get('Click_Rate_%'))}, "
                f"MoM send change {_fmt_pct(match.get('MoM_Send_Change_%'))}."
            )

    # (A) Compact digest of the ranked rows so the model can reason over real figures
    # instead of only seeing the top name. Bounded by the ranking size (10 rows).
    if entities:
        lines.append(f"Ranked {name_field.lower()}s (rank | name | sent | delivery% | bounce% | spam% | MoM%):")
        for i, e in enumerate(entities, 1):
            lines.append(
                f"  {e.get('Rank', i)} | {e.get(name_field, '—')} | "
                f"{int(e.get('Sent', 0) or 0):,} | {_fmt_pct(e.get('Delivery_Rate_%'))} | "
                f"{_fmt_pct(e.get('Bounce_Rate_%'))} | {_fmt_pct(e.get('Spam_Rate_%'))} | "
                f"{_fmt_pct(e.get('MoM_Send_Change_%'))}"
            )

        # (C) Aggregates over the ranked rows, so "how many are concerning" is answerable.
        def _num(row, key):
            try:
                return float(row.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        sent_total = sum(int(e.get('Sent', 0) or 0) for e in entities)
        delivered_total = sum(int(e.get('Delivered', 0) or 0) for e in entities)
        blended = round(delivered_total / sent_total * 100, 2) if sent_total else 0.0
        worst_del = min(entities, key=lambda e: _num(e, 'Delivery_Rate_%'))
        worst_bounce = max(entities, key=lambda e: _num(e, 'Bounce_Rate_%'))
        worst_spam = max(entities, key=lambda e: _num(e, 'Spam_Rate_%'))
        best_growth = max(entities, key=lambda e: _num(e, 'MoM_Send_Change_%'))
        worst_growth = min(entities, key=lambda e: _num(e, 'MoM_Send_Change_%'))
        bounce_over_2 = [e for e in entities if _num(e, 'Bounce_Rate_%') > 2]
        spam_over_03 = [e for e in entities if _num(e, 'Spam_Rate_%') > 0.3]
        del_under_95 = [e for e in entities if _num(e, 'Delivery_Rate_%') < 95]

        lines += [
            f"Aggregates across these {len(entities)} ranked {name_field.lower()}s: "
            f"{sent_total:,} sent, {delivered_total:,} delivered, {blended}% blended delivery.",
            f"  Weakest delivery: {worst_del.get(name_field)} at {_fmt_pct(worst_del.get('Delivery_Rate_%'))}. "
            f"Highest bounce: {worst_bounce.get(name_field)} at {_fmt_pct(worst_bounce.get('Bounce_Rate_%'))}. "
            f"Highest spam: {worst_spam.get(name_field)} at {_fmt_pct(worst_spam.get('Spam_Rate_%'))}.",
            f"  Biggest MoM growth: {best_growth.get(name_field)} "
            f"({_fmt_pct(best_growth.get('MoM_Send_Change_%'))}); biggest drop: "
            f"{worst_growth.get(name_field)} ({_fmt_pct(worst_growth.get('MoM_Send_Change_%'))}).",
            f"  Counts of concern: bounce >2% = {len(bounce_over_2)}, spam >0.3% = "
            f"{len(spam_over_03)}, delivery <95% = {len(del_under_95)}.",
        ]

    lines.append(
        "The ranked table and chart are already shown to the user. Use the figures above to "
        "give an analytical takeaway (leaders, outliers, notable MoM moves, weak "
        "delivery/spam) — do NOT transcribe every row back to them."
    )
    return _emit("\n".join(lines), stats=tiles, tables=[table], charts=[chart])


@tool(response_format="content_and_artifact")
def lookup_account_info(query: str) -> str:
    """Look up account / IP / sending-domain info from Blueshift's mapping data.

    Answers "who owns this domain/IP", "what IPs does this account send from",
    "what ESP/IP pool does X use", and per-IP SNDS reputation. Accepts an IP
    address, a sending domain, or an account name.

    Sources: account_mappings (domain→account), SNDS (IP→account/ESP + per-IP
    reputation), and account_info (ESP / IP addresses / IP pool).
    """
    q = (query or "").strip()
    print(f"\n🛠  Account/IP lookup: {q}")
    if not q:
        return _text("Please provide an IP address, sending domain, or account name.")

    tables = []
    lines = []

    if accounts.is_ip(q):
        mapping = accounts.ip_mapping(ip=q)
        snds = accounts.ip_snds_latest(q)
        acct = mapping[0]["account_name"] if mapping else (snds["account_name"] if snds else "—")
        esp = mapping[0]["esp"] if mapping else "—"
        lines.append(f"IP {q} → account: {acct}, ESP: {esp}")
        rows = [["Account", acct], ["ESP", esp]]
        if snds:
            rows += [
                ["SNDS date", snds.get("data_date", "—")],
                ["Volume", f"{int(accounts_num(snds.get('message_volume'))):,}"],
                ["Spam rate", str(snds.get("spam_rate", "—"))],
                ["Complaint rate", str(snds.get("complaint_rate", "—"))],
                ["Trap hits", str(snds.get("trap_hits", "—"))],
                ["Filter result", str(snds.get("filter_result", "—"))],
            ]
            lines.append(f"SNDS: volume {snds.get('message_volume')}, spam {snds.get('spam_rate')}, "
                         f"filter {snds.get('filter_result')}")
        else:
            lines.append("No SNDS reputation record for this IP.")
        tables.append({"title": f"IP info — {q}",
                       "columns": [{"label": "Field", "align": "left"}, {"label": "Value", "align": "left"}],
                       "rows": rows})
        for rec in accounts.account_info_matches(q)[:5]:
            lines.append(f"account_info: {rec.get('domain')} ({rec.get('esp')}) ip_pool={rec.get('ip_pool')}")

    else:
        # Treat as domain first, then account name.
        dmap = accounts.domain_to_account(q)
        info = accounts.account_info_matches(q)
        if dmap:
            m = dmap[0]
            lines.append(f"Domain {m['sending_domain']} → account: {m['account_name']}"
                         + (" (affiliate)" if m.get("is_affiliate") else ""))
        acct_name = dmap[0]["account_name"] if dmap else q
        adomains = accounts.account_to_domains(acct_name)
        aips = accounts.ip_mapping(account=acct_name)

        if adomains:
            tables.append({
                "title": f"Domains for account '{acct_name}' ({len(adomains)})",
                "columns": [{"label": "Sending domain", "align": "left"},
                            {"label": "Affiliate", "align": "left"}],
                "rows": [[d["sending_domain"], "yes" if d.get("is_affiliate") else "no"] for d in adomains[:50]],
            })
        if aips:
            tables.append({
                "title": f"Sending IPs for account '{acct_name}' ({len(aips)})",
                "columns": [{"label": "IP address", "align": "left"}, {"label": "ESP", "align": "left"}],
                "rows": [[ip["ip_address"], ip.get("esp", "—")] for ip in aips],
            })
        if info:
            tables.append({
                "title": f"Account info records ({len(info)})",
                "columns": [{"label": "Domain", "align": "left"}, {"label": "ESP", "align": "left"},
                            {"label": "IP addresses", "align": "left"}, {"label": "IP pool", "align": "left"},
                            {"label": "Status", "align": "left"}],
                "rows": [[r.get("domain", "—"), r.get("esp", "—"), r.get("ip_addresses", "—"),
                          r.get("ip_pool", "—"), r.get("status", "—")] for r in info[:50]],
            })
        if not (dmap or adomains or aips or info):
            return _text(f"No account/IP/domain records found for '{q}'.")
        lines.append(f"Found {len(adomains)} domain(s) and {len(aips)} IP(s) for '{acct_name}'.")

    return _emit("\n".join(lines) or f"Results for {q}.", tables=tables)


def accounts_num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression (numbers and + - * / ( ) only).

    Use ONLY for arithmetic, e.g. "45 * 98" or "(100 / 5) + 12".
    """
    print(f"\n🛠  Calculator: {expression}")
    if not re.fullmatch(r"[0-9+\-*/().\s]+", expression):
        return "ERROR: This is not a mathematical expression."
    try:
        return str(eval(expression))
    except Exception:
        return "ERROR: Invalid mathematical expression."


# --- Email domain (ISP) breakdown ----------------------------------------
#
# The only tool that can reach Druid. Two guards keep that load deliberate:
#   * cache-only by default -- answering costs nothing unless live=True is passed
#   * AGENT_DRUID_ENABLED=0 in .env hard-disables live pulls regardless of live=
# Set AGENT_DRUID_ENABLED=0 to stop the agent querying Druid entirely.

_ISP_RANGES = {"past_7_days", "past_30_days", "past_24h"}


def _druid_enabled() -> bool:
    return os.getenv("AGENT_DRUID_ENABLED", "1").strip().lower() not in ("0", "false", "no")


@tool(response_format="content_and_artifact")
def email_domain_isp_breakdown(domain: str, date_range: str = "past_7_days",
                               live: bool = False) -> str:
    """Break a sending domain's volume down BY RECIPIENT MAILBOX PROVIDER (Gmail, Outlook, Yahoo, iCloud...).

    Use this ONLY when the question is about per-provider or per-ISP performance —
    e.g. "how is delivery to Gmail vs Outlook", "which mailbox provider is bouncing",
    "is Microsoft throttling us", "ISP breakdown". Each provider row shows sent,
    delivered, delivery %, bounces, soft bounces, opens, clicks, spam and unsubs.
    Providers outside the tracked list are combined into a single 'Other' row.

    Do NOT use this tool for a domain's overall totals, trends or health — use
    check_email_metrics or email_metrics_trend instead. Those are cheaper and answer
    "how is this domain doing" fully.

    date_range: "past_7_days" (default), "past_30_days", or "past_24h".
      Only pass "past_24h" if the user explicitly asked for the last 24 hours; it
      cannot be served from cache and always costs a live query.

    live: leave False. False serves locally cached data only and never queries Druid,
      reporting any days it could not cover. Pass True ONLY if the user explicitly
      asks for fresh/live/up-to-date numbers, or if a cache-only answer came back with
      missing days and the user then asks you to fetch them.
    """
    print(f"\n🛠  ISP breakdown: {domain} range={date_range} live={live}")

    if date_range not in _ISP_RANGES:
        return _text(f"⚠ Unsupported date_range {date_range!r}. Use one of: "
                     f"{', '.join(sorted(_ISP_RANGES))}.")

    want_live = bool(live)
    if want_live and not _druid_enabled():
        want_live = False
        blocked_note = ("\n\nNote: live Druid queries are disabled for the agent "
                        "(AGENT_DRUID_ENABLED=0), so this is cached data only.")
    else:
        blocked_note = ""

    if date_range == "past_24h" and not want_live:
        reason = ("live Druid queries are disabled (AGENT_DRUID_ENABLED=0)"
                  if not _druid_enabled() else "live=True was not requested")
        return _text(
            f"⚠ The past-24-hours window is a rolling window with no cached form, so it "
            f"needs a live Druid query, but {reason}. Ask the user to confirm they want a "
            f"live pull, then call this tool again with live=True — or use past_7_days, "
            f"which is served from cache."
        )

    try:
        from email_domain_stats_service import get_email_domain_stats
        result = get_email_domain_stats(
            domain, range_type=date_range, cached_only=not want_live
        )
    except Exception as e:
        return _text(f"⚠ Could not fetch ISP breakdown for {domain}: {e}")

    rows = result.get("rows") or []
    cache = result.get("cache") or {}
    missing = cache.get("missing_dates") or []
    errors = result.get("errors") or []

    if not rows:
        if missing:
            return _text(
                f"No cached ISP data for {domain} over {result['window']['label']} "
                f"({len(missing)} day(s) not cached, and live querying was not requested). "
                f"Tell the user this needs a live Druid query and ask whether to run it; "
                f"if they agree, call this tool again with live=True.{blocked_note}"
            )
        if errors:
            return _text(f"⚠ ISP breakdown for {domain} failed: {'; '.join(errors)}")
        return _text(f"No sending activity to tracked mailbox providers for {domain} "
                     f"over {result['window']['label']}.")

    totals = result["totals"]
    tiles = [
        _stat("Sent", f"{totals['sent_count']:,}"),
        _stat("Delivered", f"{totals['delivered_count']:,}"),
        _stat("Delivery rate", f"{totals['delivery_rate']}%", _tone_delivery(totals["delivery_rate"])),
        _stat("Bounce rate", f"{totals['bounce_rate']}%", _tone_bounce(totals["bounce_rate"])),
    ]

    table = {
        "title": f"{domain} — by recipient mailbox provider ({result['window']['label']})",
        "columns": ["Email domain", "Sent", "Delivered", "Delivery %", "Bounces",
                    "Soft bounces", "Opens", "Clicks", "Spam", "Unsub"],
        "rows": [
            [r["email_domain"], f"{r['sent_count']:,}", f"{r['delivered_count']:,}",
             f"{r['delivery_rate']}%", f"{r['bounce_count']:,}",
             f"{r['unique_soft_bounce_count']:,}", f"{r['unique_open_count_user']:,}",
             f"{r['click_count']:,}", f"{r['spam_report_count']:,}",
             f"{r['unsubscribe_count']:,}"]
            for r in rows
        ],
    }

    chart = {
        "title": f"Delivery rate by mailbox provider — {domain}",
        "type": "bar",
        "labels": [r["email_domain"] for r in rows[:12]],
        "series": [{"name": "Delivery %", "data": [r["delivery_rate"] for r in rows[:12]]}],
    }

    worst = min((r for r in rows if r["sent_count"] > 0),
                key=lambda r: r["delivery_rate"], default=None)

    lines = [
        f"{domain} — recipient mailbox provider breakdown for {result['window']['label']}.",
        f"Source: {cache.get('days_from_cache', 0)} day(s) from local cache, "
        f"{cache.get('days_from_druid', 0)} from Druid.",
        f"Totals: {totals['sent_count']:,} sent, {totals['delivery_rate']}% delivery, "
        f"{totals['bounce_rate']}% bounce across {len(rows)} providers.",
    ]
    if worst is not None:
        lines.append(f"Weakest provider by delivery: {worst['email_domain']} at "
                     f"{worst['delivery_rate']}% on {worst['sent_count']:,} sent.")
    if missing:
        lines.append(f"CAVEAT: {len(missing)} day(s) in this range are not cached and were "
                     f"NOT fetched ({missing[0]}..{missing[-1]}), so these numbers cover only "
                     f"the cached days. Say so, and offer a live pull.")
    if result.get("uniques_are_summed"):
        lines.append("Note: opens and soft-bounce figures are summed from daily values and "
                     "so run a few percent high; sent/delivered/clicks/spam/unsub are exact.")
    if errors:
        lines.append(f"Partial errors: {'; '.join(errors)}")
    lines.append("The table and chart are shown to the user — give a short takeaway "
                 "(which providers lag, likely cause) rather than re-listing every row.")

    return _emit("\n".join(lines) + blocked_note, stats=tiles, tables=[table], charts=[chart])


# --- Jira ticket creation -------------------------------------------------
#
# The only tool that WRITES to an external system. A created ticket is visible to
# other people and cannot be quietly undone, so:
#   * issue_type has no default -- the agent must ask the user which type to use
#   * AGENT_JIRA_ENABLED=0 in .env disables creation entirely
#   * every description gets a footer marking it as agent-filed, for audit
# Set AGENT_JIRA_ENABLED=0 to stop the agent filing tickets.

# Matches the issue types the Bounce Logs UI offers. The DEL project also allows
# Task, Issue, Onboarding, Ongoing, Setup and Support case if these are widened.
_JIRA_ISSUE_TYPES = ("Remediation", "Compliance", "Monitoring")

_AGENT_FOOTER = "Ticket filed by Observatory Agent"


def _jira_enabled() -> bool:
    return os.getenv("AGENT_JIRA_ENABLED", "1").strip().lower() not in ("0", "false", "no")


@tool(response_format="content_and_artifact")
def file_jira_ticket(issue_type: str, summary: str, description: str) -> str:
    """File a Jira ticket in the DEL (Deliverability Services) project. THIS CREATES A REAL TICKET.

    Only call this when the user has clearly asked for a ticket to be raised or filed.
    Never file one on your own initiative, and never file more than one per user request.

    All three arguments are REQUIRED. Do not ask the user which issue type to use —
    choose it yourself from the nature of the problem, using these rules:

      * "Remediation" — an active delivery or reputation problem that needs fixing:
        bounces, blocks, throttling, poor delivery or inbox placement, spam
        complaints, blocklisting, an ISP rejecting mail. This is the usual choice.
      * "Compliance" — an authentication, alignment or policy problem: SPF, DKIM,
        DMARC, alignment failures, DMARC still at p=none, MTA-STS, TLS-RPT, BIMI.
      * "Monitoring" — nothing is broken yet and no fix is being asked for; the ask
        is to watch, track or alert on something over time.

    If a request could be more than one, prefer Remediation when mail is actually
    being affected now, and Compliance when the root cause is a DNS/auth record.
    If the user does name a type, use theirs instead of your own choice.

    issue_type: exactly one of "Remediation", "Compliance", "Monitoring".
    summary: one-line ticket title. Be specific — include the domain and the problem,
      e.g. "carmoola.co.uk — 72% delivery to Outlook/Hotmail (soft bounces)".
    description: the full detail. Include the evidence you have already gathered —
      domain, metrics, dates, affected mailbox providers, and suggested next steps.
      A line marking the ticket as agent-filed is appended automatically.
    """
    print(f"\n🛠  Jira ticket: type={issue_type!r} summary={summary[:60]!r}")

    if not _jira_enabled():
        return _text("⚠ Jira ticket creation is disabled for the agent "
                     "(AGENT_JIRA_ENABLED=0). Tell the user the ticket was NOT filed.")

    if not summary or not summary.strip():
        return _text("⚠ A ticket summary is required. Ask the user what the ticket should say.")

    if not description or not description.strip():
        return _text("⚠ A ticket description is required. Summarise the problem and evidence.")

    chosen = (issue_type or "").strip()
    if chosen not in _JIRA_ISSUE_TYPES:
        return _text(
            f"⚠ {issue_type!r} is not a valid issue type. Call this tool again with exactly "
            f"one of: {', '.join(_JIRA_ISSUE_TYPES)} — pick the one that fits the problem "
            f"(Remediation for delivery issues, Compliance for SPF/DKIM/DMARC, Monitoring "
            f"for tracking only). Do not ask the user."
        )

    body = f"{description.strip()}\n\n— {_AGENT_FOOTER}"

    try:
        from jira_service import create_jira_ticket, JIRA_BASE_URL
        result = create_jira_ticket(chosen, summary.strip(), body)
    except Exception as e:
        return _text(f"⚠ Could not file the Jira ticket: {e}\n"
                     f"Tell the user it was NOT created and report this reason.")

    key = result.get("key") or "(unknown)"
    url = f"{JIRA_BASE_URL}/browse/{key}" if JIRA_BASE_URL and key != "(unknown)" else ""

    tiles = [
        _stat("Ticket", key, "good"),
        _stat("Type", chosen),
        _stat("Project", "DEL"),
    ]
    lines = [
        f"Jira ticket {key} created in DEL as a {chosen} issue.",
        f"Summary: {summary.strip()}",
    ]
    if url:
        lines.append(f"Link: {url}")
    lines.append(f"The description was filed with the footer '{_AGENT_FOOTER}'.")
    lines.append("Confirm the ticket key to the user and include the link. Do not file another.")
    return _emit("\n".join(lines), stats=tiles)
