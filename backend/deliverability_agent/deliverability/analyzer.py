"""Turn raw DNS records into structured, severity-tagged findings.

The point of this module is to do the deterministic, rule-based part of
deliverability analysis in code (so it's reliable and cheap) and leave the
explanation / prioritization / remediation wording to the LLM.
"""

import re

# severity levels, worst first
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
OK = "ok"


def _finding(check, severity, message):
    return {"check": check, "severity": severity, "message": message}


def analyze_spf(record):
    findings = []
    if not record:
        return [_finding("SPF", CRITICAL, "No SPF record found. Receivers can't verify which servers may send for this domain.")]

    findings.append(_finding("SPF", OK, f"SPF record present: {record}"))

    # "all" mechanism qualifier
    m = re.search(r"([-~?+])all\b", record)
    if not m:
        findings.append(_finding("SPF", WARNING, "SPF has no 'all' mechanism; behavior for unlisted senders is undefined."))
    else:
        q = m.group(1)
        if q == "+":
            findings.append(_finding("SPF", CRITICAL, "'+all' allows ANY server to send as this domain. Remove it."))
        elif q == "?":
            findings.append(_finding("SPF", WARNING, "'?all' (neutral) provides no protection. Prefer '~all' or '-all'."))
        elif q == "~":
            findings.append(_finding("SPF", INFO, "'~all' (softfail) is acceptable; '-all' (hardfail) is stronger once you're confident."))
        elif q == "-":
            findings.append(_finding("SPF", OK, "'-all' (hardfail) — strict and recommended."))

    return findings


def analyze_spf_lookups(evaluation):
    """Findings from a recursive SPF evaluation (spf.SPFEvaluator.evaluate())."""
    if evaluation is None or evaluation.get("record") is None:
        return []

    findings = []
    used, limit = evaluation["lookups"], evaluation["limit"]

    if evaluation["exceeded"]:
        findings.append(_finding("SPF", CRITICAL, f"{used} DNS lookups across the include tree — over the RFC limit of {limit}. SPF returns PermError and many receivers fail authentication. Flatten includes or remove unused senders."))
    elif used >= 8:
        findings.append(_finding("SPF", WARNING, f"{used}/{limit} DNS lookups used across the include tree. Close to the limit — adding another sender may break SPF."))
    else:
        findings.append(_finding("SPF", OK, f"{used}/{limit} DNS lookups used across the include tree — within the limit."))

    if evaluation["void_exceeded"]:
        findings.append(_finding("SPF", CRITICAL, f"{evaluation['void_lookups']} void lookups (includes pointing at domains with no SPF). More than 2 is a PermError."))
    elif evaluation["void_lookups"]:
        findings.append(_finding("SPF", WARNING, f"{evaluation['void_lookups']} void lookup(s) — an include/redirect points at a domain with no SPF record."))

    for err in evaluation["errors"]:
        findings.append(_finding("SPF", CRITICAL, err))
    for warn in evaluation["warnings"]:
        findings.append(_finding("SPF", WARNING, warn))

    return findings


def analyze_dmarc(dmarc):
    """`dmarc` is the dict from DNSChecker.get_dmarc_with_source(), or None."""
    if not dmarc:
        return [_finding("DMARC", CRITICAL, "No DMARC record found (neither on the domain nor its organizational domain). SPF/DKIM alignment isn't enforced and reports aren't collected.")]

    record = dmarc["record"]
    tags = dict(re.findall(r"(\w+)=([^;]+)", record))
    findings = []

    if dmarc["inherited"]:
        findings.append(_finding("DMARC", OK, f"DMARC inherited from organizational domain '{dmarc['source']}': {record}"))
        # For a subdomain, the org domain's sp= tag governs (falls back to p=).
        policy = tags.get("sp", tags.get("p", "")).strip().lower()
        policy_label = "subdomain policy (sp=)"
    else:
        findings.append(_finding("DMARC", OK, f"DMARC record present: {record}"))
        policy = tags.get("p", "").strip().lower()
        policy_label = "policy (p=)"

    if policy == "none":
        findings.append(_finding("DMARC", WARNING, f"Effective {policy_label} is 'none' (monitor only). Move to 'quarantine' then 'reject' after validating reports."))
    elif policy == "quarantine":
        findings.append(_finding("DMARC", INFO, f"Effective {policy_label} is 'quarantine'. 'reject' is the strongest end state."))
    elif policy == "reject":
        findings.append(_finding("DMARC", OK, f"Effective {policy_label} is 'reject' — full enforcement."))
    else:
        findings.append(_finding("DMARC", CRITICAL, "DMARC record has no valid policy tag."))

    if "rua" not in tags:
        findings.append(_finding("DMARC", WARNING, "No 'rua=' aggregate-report address. You're flying blind on auth failures."))

    pct = tags.get("pct")
    if pct and pct.strip() != "100":
        findings.append(_finding("DMARC", INFO, f"'pct={pct.strip()}' applies the policy to only part of mail. Ramp to 100 for full coverage."))

    return findings


def analyze_dkim(dkim_hits):
    if not dkim_hits:
        return [_finding("DKIM", WARNING, "No DKIM key found via common selectors. If you sign mail, the selector may be custom — supply it explicitly.")]
    findings = [_finding("DKIM", OK, f"Found {len(dkim_hits)} DKIM key(s): " + ", ".join(h["selector"] for h in dkim_hits))]
    for h in dkim_hits:
        rec = h["record"].lower()
        if "p=" in rec and re.search(r"p=\s*(;|$)", rec):
            findings.append(_finding("DKIM", CRITICAL, f"Selector '{h['selector']}' has an empty public key (p=), meaning the key is revoked."))
    return findings


def analyze_mx(mx):
    if not mx:
        return [_finding("MX", CRITICAL, "No MX records; this domain can't receive mail (and DMARC reports won't arrive).")]
    return [_finding("MX", OK, f"{len(mx)} MX host(s): " + "; ".join(mx))]


def analyze_optional(name, record, missing_severity=INFO):
    label = {
        "BIMI": "BIMI (brand logo in inbox)",
        "MTA-STS": "MTA-STS (enforced TLS for inbound mail)",
        "TLS-RPT": "TLS-RPT (TLS failure reporting)",
    }.get(name, name)
    if record:
        return [_finding(name, OK, f"{label} present: {record}")]
    return [_finding(name, missing_severity, f"{label} not configured.")]


def _pct(ratio):
    return f"{ratio * 100:.2f}%"


def analyze_postmaster(stats):
    """Findings from a Google Postmaster TrafficStats dict (or None)."""
    if stats is None:
        return [_finding("Postmaster", INFO, "No Gmail Postmaster data available. The domain may be unverified, too new, or below Gmail's minimum volume threshold.")]

    findings = []
    date = stats["name"].rsplit("/", 1)[-1]
    findings.append(_finding("Postmaster", INFO, f"Gmail Postmaster data as of {date}."))

    rep = stats.get("domainReputation", "REPUTATION_CATEGORY_UNSPECIFIED")
    rep_sev = {"HIGH": OK, "MEDIUM": INFO, "LOW": WARNING, "BAD": CRITICAL}.get(rep, INFO)
    findings.append(_finding("Postmaster", rep_sev, f"Gmail domain reputation: {rep}."))

    for ip in stats.get("ipReputations", []):
        cat = ip.get("reputation", "")
        count = ip.get("ipCount", 0)
        if cat in ("LOW", "BAD") and int(count) > 0:
            sev = CRITICAL if cat == "BAD" else WARNING
            samples = ", ".join(ip.get("sampleIps", [])[:3])
            findings.append(_finding("Postmaster", sev, f"{count} sending IP(s) at {cat} reputation" + (f" (e.g. {samples})" if samples else "") + "."))

    spam = stats.get("userReportedSpamRatio")
    if spam is not None:
        if spam >= 0.003:
            findings.append(_finding("Postmaster", CRITICAL, f"User-reported spam rate {_pct(spam)} — at/above Gmail's 0.3% danger line. Expect throttling/spam-foldering."))
        elif spam >= 0.001:
            findings.append(_finding("Postmaster", WARNING, f"User-reported spam rate {_pct(spam)} — trending toward the 0.3% limit. Keep it under 0.1%."))
        else:
            findings.append(_finding("Postmaster", OK, f"User-reported spam rate {_pct(spam)} — healthy (target <0.1%)."))

    for key, label in (("spfSuccessRatio", "SPF"), ("dkimSuccessRatio", "DKIM"), ("dmarcSuccessRatio", "DMARC")):
        ratio = stats.get(key)
        if ratio is None:
            continue
        if ratio < 0.80:
            findings.append(_finding("Postmaster", CRITICAL, f"{label} passes on only {_pct(ratio)} of mail — a large share is unauthenticated."))
        elif ratio < 0.95:
            findings.append(_finding("Postmaster", WARNING, f"{label} passes on {_pct(ratio)} of mail — some senders aren't aligned."))
        else:
            findings.append(_finding("Postmaster", OK, f"{label} passes on {_pct(ratio)} of mail."))

    errors = sorted(stats.get("deliveryErrors", []), key=lambda e: e.get("errorRatio", 0), reverse=True)
    for err in errors[:3]:
        ratio = err.get("errorRatio", 0)
        if ratio >= 0.01:
            findings.append(_finding("Postmaster", WARNING, f"Delivery error {err.get('errorType', '?')} ({err.get('errorClass', '?')}) affects {_pct(ratio)} of mail."))

    return findings


def _int(n):
    return f"{int(n):,}"


def analyze_metrics(summary):
    """Findings from a metrics.get_domain_metrics() summary (or None)."""
    if summary is None:
        return [_finding("Metrics", INFO, "No send/delivery metrics found for this domain in the EC2 data.")]

    findings = []
    latest = summary["latest"]
    findings.append(_finding("Metrics", INFO,
        f"As of {summary['latest_date']}: sent {_int(latest['sent'])}, delivered {_int(latest['delivered'])} "
        f"({latest['delivery_rate']}% delivery) across {len(summary['by_esp'])} ESP/region row(s)."))

    dr = latest["delivery_rate"]
    if latest["sent"] == 0:
        findings.append(_finding("Metrics", INFO, "No mail sent on the latest date."))
    elif dr < 90:
        findings.append(_finding("Metrics", CRITICAL, f"Delivery rate {dr}% — well below healthy (>98%). Large share not accepted."))
    elif dr < 95:
        findings.append(_finding("Metrics", WARNING, f"Delivery rate {dr}% — below the ~98% healthy range."))
    elif dr < 98:
        findings.append(_finding("Metrics", INFO, f"Delivery rate {dr}% — acceptable; aim for >98%."))
    else:
        findings.append(_finding("Metrics", OK, f"Delivery rate {dr}% — healthy."))

    br = latest["bounce_rate"]
    if br >= 5:
        findings.append(_finding("Metrics", CRITICAL, f"Bounce rate {br}% — very high (keep <2%). Risks reputation/blocking."))
    elif br >= 2:
        findings.append(_finding("Metrics", WARNING, f"Bounce rate {br}% — elevated (keep <2%). Check list hygiene."))
    else:
        findings.append(_finding("Metrics", OK, f"Bounce rate {br}% — within range."))

    sr = latest["spam_rate"]
    if sr >= 0.3:
        findings.append(_finding("Metrics", CRITICAL, f"Complaint rate {sr}% — at/above the 0.3% danger line."))
    elif sr >= 0.1:
        findings.append(_finding("Metrics", WARNING, f"Complaint rate {sr}% — trending high (keep <0.1%)."))
    else:
        findings.append(_finding("Metrics", OK, f"Complaint rate {sr}% — healthy."))

    period = summary["period"]
    findings.append(_finding("Metrics", INFO,
        f"Last {summary['period_days']} day(s): sent {_int(period['sent'])}, delivered {_int(period['delivered'])} "
        f"({period['delivery_rate']}% delivery, {period['bounce_rate']}% bounce)."))

    return findings


def summarize(findings):
    """Count findings by severity for a quick headline."""
    counts = {CRITICAL: 0, WARNING: 0, INFO: 0, OK: 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return counts
