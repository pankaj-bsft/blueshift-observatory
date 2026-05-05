"""
DNS Looker Service
Checks all email deliverability-related DNS records for a domain.
"""

import dns.resolver
import dns.reversename
import dns.exception
import requests
import re
from typing import Optional, List, Tuple

# ─── Constants ────────────────────────────────────────────────────────────────

# Known DKIM selectors by ESP
ESP_DKIM_SELECTORS = {
    "sparkpost": ["scph0226"],
    "mailgun":   ["krs", "pdk1", "pdk2"],
    "sendgrid":  ["s1", "s2"],
}
ALL_DEFAULT_SELECTORS = [s for selectors in ESP_DKIM_SELECTORS.values() for s in selectors]

# Normalized ESP name -> key mapping
ESP_NAME_MAP = {
    "sparkpost": "sparkpost",
    "mailgun":   "mailgun",
    "sendgrid":  "sendgrid",
    "Sparkpost": "sparkpost",
    "Mailgun":   "mailgun",
    "Sendgrid":  "sendgrid",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _query_txt(name: str) -> List[str]:
    """Return list of TXT record strings for a DNS name, or empty list."""
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=3)
        return [b.decode() for rdata in answers for b in rdata.strings]
    except Exception:
        return []


def _query_mx(domain: str) -> List[dict]:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=3)
        return sorted(
            [{"priority": r.preference, "host": str(r.exchange).rstrip(".")} for r in answers],
            key=lambda x: x["priority"]
        )
    except Exception:
        return []


def _query_a(domain: str) -> List[str]:
    try:
        answers = dns.resolver.resolve(domain, "A", lifetime=3)
        return [str(r) for r in answers]
    except Exception:
        return []


def _status(ok: bool, warn: bool = False) -> str:
    if ok:
        return "pass"
    if warn:
        return "warn"
    return "fail"


# ─── SPF ──────────────────────────────────────────────────────────────────────

def check_spf(domain: str) -> dict:
    records = [r for r in _query_txt(domain) if r.startswith("v=spf1")]

    if not records:
        return {
            "status": "fail",
            "value": None,
            "explanation": "No SPF record found.",
            "fix": f'Add a TXT record on {domain}:\n  v=spf1 include:YOUR_ESP ~all',
            "lookup_count": 0,
            "multiple": False,
        }

    if len(records) > 1:
        return {
            "status": "fail",
            "value": records,
            "explanation": "Multiple SPF records found — only one is allowed.",
            "fix": "Merge all SPF includes into a single TXT record.",
            "lookup_count": None,
            "multiple": True,
        }

    spf = records[0]
    lookup_count, warn_lookup = _count_spf_lookups(domain)
    has_all = re.search(r"[~\-\+\?]all", spf)

    issues = []
    if not has_all:
        issues.append("No 'all' mechanism found — add '~all' or '-all'.")
    if lookup_count > 10:
        issues.append(f"SPF lookup count is {lookup_count} — exceeds the 10-lookup limit.")
    elif lookup_count >= 8:
        issues.append(f"SPF lookup count is {lookup_count} — approaching the 10-lookup limit.")

    status = "fail" if lookup_count > 10 else ("warn" if issues else "pass")

    fix = None
    if issues:
        fix_lines = []
        if lookup_count > 10:
            fix_lines.append("Reduce 'include:' mechanisms or use SPF flattening.")
        if not has_all:
            fix_lines.append("Add '~all' (softfail) or '-all' (hardfail) at the end of your SPF record.")
        fix = "\n".join(fix_lines)

    return {
        "status": status,
        "value": spf,
        "explanation": " ".join(issues) if issues else "SPF record is valid.",
        "fix": fix,
        "lookup_count": lookup_count,
        "multiple": False,
    }


def _count_spf_lookups(domain: str, _visited: set = None, _depth: int = 0) -> Tuple[int, bool]:
    """Recursively count SPF DNS lookups. Returns (count, over_limit)."""
    if _visited is None:
        _visited = set()
    if domain in _visited or _depth > 15:
        return 0, False
    _visited.add(domain)

    records = [r for r in _query_txt(domain) if r.startswith("v=spf1")]
    if not records:
        return 0, False

    spf = records[0]
    count = 0
    mechanisms = re.findall(r"(?:include|redirect|a|mx|exists|ptr):([^\s]+)", spf)

    for mech in mechanisms:
        count += 1
        if count > 10:
            return count, True
        sub_count, over = _count_spf_lookups(mech, _visited, _depth + 1)
        count += sub_count
        if over or count > 10:
            return count, True

    return count, False


def get_spf_chain(domain: str, _visited: set = None, _depth: int = 0) -> dict:
    """Build SPF include chain tree for visualization."""
    if _visited is None:
        _visited = set()
    if domain in _visited or _depth > 10:
        return {"domain": domain, "record": "(circular/too deep)", "children": []}
    _visited.add(domain)

    records = [r for r in _query_txt(domain) if r.startswith("v=spf1")]
    record = records[0] if records else None
    children = []

    if record:
        includes = re.findall(r"include:([^\s]+)", record)
        redirects = re.findall(r"redirect=([^\s]+)", record)
        for inc in includes + redirects:
            children.append(get_spf_chain(inc, _visited, _depth + 1))

    return {
        "domain": domain,
        "record": record,
        "lookup_depth": _depth,
        "children": children,
    }


# ─── DKIM ─────────────────────────────────────────────────────────────────────

def check_dkim(domain: str, selector: str) -> dict:
    name = f"{selector}._domainkey.{domain}"
    records = _query_txt(name)

    if not records:
        return {
            "status": "fail",
            "selector": selector,
            "value": None,
            "explanation": f"No DKIM record found for selector '{selector}'.",
            "fix": f"Publish a DKIM TXT record at:\n  {name}\nObtain the public key from your ESP.",
        }

    value = " ".join(records)
    issues = []

    if "p=" not in value:
        issues.append("Public key (p=) missing in DKIM record.")
    elif "p=;" in value or "p= " in value:
        issues.append("DKIM public key is empty — record has been revoked.")

    key_type = re.search(r"k=(\w+)", value)
    if key_type and key_type.group(1) not in ("rsa", "ed25519"):
        issues.append(f"Unknown key type: {key_type.group(1)}.")

    status = "fail" if issues else "pass"
    return {
        "status": status,
        "selector": selector,
        "value": value,
        "explanation": " ".join(issues) if issues else f"DKIM valid for selector '{selector}'.",
        "fix": "\n".join(issues) if issues else None,
    }


def check_dkim_auto(domain: str) -> dict:
    """Try all known ESP selectors in parallel, return first match or not-found."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _try(selector):
        return selector, check_dkim(domain, selector)

    with ThreadPoolExecutor(max_workers=len(ALL_DEFAULT_SELECTORS)) as ex:
        futures = {ex.submit(_try, s): s for s in ALL_DEFAULT_SELECTORS}
        for future in as_completed(futures):
            try:
                selector, result = future.result()
                if result["status"] == "pass":
                    # cancel remaining
                    for f in futures:
                        f.cancel()
                    return result
            except Exception:
                pass

    return {
        "status": "warn",
        "selector": None,
        "value": None,
        "explanation": "No DKIM record found with known ESP selectors.",
        "fix": "Provide the DKIM selector used by your ESP to check manually.",
        "needs_selector": True,
    }


def check_dkim_for_esp(domain: str, esp: str) -> dict:
    """
    Try only the selectors for a known ESP.
    Falls back to full auto-check if ESP unknown.
    """
    esp_key = ESP_NAME_MAP.get(esp, '').lower() if esp else ''
    selectors = ESP_DKIM_SELECTORS.get(esp_key)

    if not selectors:
        # Unknown ESP — fall back to full check
        return check_dkim_auto(domain)

    if len(selectors) == 1:
        # Only one selector — no need for parallel
        return check_dkim(domain, selectors[0])

    # Multiple selectors — check in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _try(selector):
        return selector, check_dkim(domain, selector)

    with ThreadPoolExecutor(max_workers=len(selectors)) as ex:
        futures = {ex.submit(_try, s): s for s in selectors}
        for future in as_completed(futures):
            try:
                selector, result = future.result()
                if result["status"] == "pass":
                    for f in futures:
                        f.cancel()
                    return result
            except Exception:
                pass

    return {
        "status": "warn",
        "selector": None,
        "value": None,
        "explanation": f"No DKIM record found for {esp} selectors ({', '.join(selectors)}).",
        "fix": "Provide the DKIM selector manually if using a custom one.",
        "needs_selector": True,
    }


# ─── DMARC ────────────────────────────────────────────────────────────────────

def check_dmarc(domain: str) -> dict:
    name = f"_dmarc.{domain}"
    records = _query_txt(name)

    if not records:
        return {
            "status": "fail",
            "value": None,
            "policy": None,
            "policy_level": 0,
            "pct": None,
            "rua": None,
            "ruf": None,
            "explanation": "No DMARC record found.",
            "fix": f'Add a TXT record at _dmarc.{domain}:\n  v=DMARC1; p=none; rua=mailto:dmarc-reports@{domain}',
        }

    value = records[0]
    policy_match = re.search(r"p=(\w+)", value)
    policy = policy_match.group(1) if policy_match else None
    policy_levels = {"none": 1, "quarantine": 2, "reject": 3}
    policy_level = policy_levels.get(policy, 0)

    pct_match = re.search(r"pct=(\d+)", value)
    pct = int(pct_match.group(1)) if pct_match else 100

    rua_match = re.search(r"rua=([^\s;]+)", value)
    ruf_match = re.search(r"ruf=([^\s;]+)", value)
    rua = rua_match.group(1) if rua_match else None
    ruf = ruf_match.group(1) if ruf_match else None

    issues = []
    if policy == "none":
        issues.append("Policy is 'none' — no enforcement. Consider moving to 'quarantine' or 'reject'.")
    if not rua:
        issues.append("No aggregate report address (rua=) — you won't receive DMARC reports.")
    if pct < 100 and policy != "none":
        issues.append(f"pct={pct} — policy only applied to {pct}% of mail.")

    status = "pass" if policy in ("quarantine", "reject") and rua else \
             "warn" if policy == "none" or not rua else "fail"

    fix = None
    if issues:
        fix_parts = []
        if policy == "none":
            fix_parts.append("Upgrade: p=quarantine (then p=reject once reports look clean).")
        if not rua:
            fix_parts.append(f"Add rua=mailto:dmarc@{domain} to receive aggregate reports.")
        fix = "\n".join(fix_parts)

    return {
        "status": status,
        "value": value,
        "policy": policy,
        "policy_level": policy_level,   # 0=missing, 1=none, 2=quarantine, 3=reject
        "pct": pct,
        "rua": rua,
        "ruf": ruf,
        "explanation": " ".join(issues) if issues else f"DMARC valid. Policy: {policy}.",
        "fix": fix,
    }


# ─── MX ───────────────────────────────────────────────────────────────────────

def check_mx(domain: str) -> dict:
    records = _query_mx(domain)

    if not records:
        return {
            "status": "fail",
            "value": None,
            "explanation": "No MX records found.",
            "fix": f"Add MX records for {domain} pointing to your mail server.",
        }

    return {
        "status": "pass",
        "value": records,
        "explanation": f"{len(records)} MX record(s) found.",
        "fix": None,
    }


# ─── BIMI ─────────────────────────────────────────────────────────────────────

def check_bimi(domain: str) -> dict:
    name = f"default._bimi.{domain}"
    records = _query_txt(name)

    if not records:
        return {
            "status": "fail",
            "value": None,
            "explanation": "No BIMI record found.",
            "fix": f'Add TXT record at default._bimi.{domain}:\n  v=BIMI1; l=https://{domain}/logo.svg; a=https://{domain}/vmc.pem',
        }

    value = records[0]
    l_match = re.search(r"l=([^\s;]+)", value)
    a_match = re.search(r"a=([^\s;]+)", value)
    logo_url = l_match.group(1) if l_match else None
    vmc_url = a_match.group(1) if a_match else None

    issues = []
    if not logo_url:
        issues.append("No logo URL (l=) in BIMI record.")
    if not vmc_url:
        issues.append("No VMC/authority URL (a=) — required by Gmail for logo display.")

    status = "warn" if issues else "pass"
    return {
        "status": status,
        "value": value,
        "logo_url": logo_url,
        "vmc_url": vmc_url,
        "explanation": " ".join(issues) if issues else "BIMI record found.",
        "fix": "\n".join(issues) if issues else None,
    }


# ─── MTA-STS ──────────────────────────────────────────────────────────────────

def check_mta_sts(domain: str) -> dict:
    # Step 1: Check DNS TXT record
    name = f"_mta-sts.{domain}"
    records = _query_txt(name)

    dns_found = any("v=STSv1" in r for r in records)
    dns_value = records[0] if records else None

    # Step 2: Try fetching the policy file
    policy_url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    policy_content = None
    policy_mode = None
    policy_reachable = False

    try:
        resp = requests.get(policy_url, timeout=3)
        if resp.status_code == 200:
            policy_reachable = True
            policy_content = resp.text
            mode_match = re.search(r"mode:\s*(\w+)", policy_content)
            policy_mode = mode_match.group(1) if mode_match else None
    except Exception:
        pass

    if not dns_found and not policy_reachable:
        return {
            "status": "fail",
            "value": None,
            "mode": None,
            "explanation": "MTA-STS not configured.",
            "fix": f"1. Add TXT at _mta-sts.{domain}: v=STSv1; id=20240101\n2. Host policy file at https://mta-sts.{domain}/.well-known/mta-sts.txt",
        }

    issues = []
    if not dns_found:
        issues.append("DNS TXT record missing.")
    if not policy_reachable:
        issues.append("Policy file not reachable at mta-sts subdomain.")
    if policy_mode == "testing":
        issues.append("Mode is 'testing' — not enforcing yet.")

    status = "warn" if issues else "pass"
    return {
        "status": status,
        "value": dns_value,
        "mode": policy_mode,
        "explanation": " ".join(issues) if issues else f"MTA-STS active. Mode: {policy_mode}.",
        "fix": "\n".join(issues) if issues else None,
    }


# ─── TLS-RPT ──────────────────────────────────────────────────────────────────

def check_tls_rpt(domain: str) -> dict:
    name = f"_smtp._tls.{domain}"
    records = _query_txt(name)

    if not records:
        return {
            "status": "fail",
            "value": None,
            "explanation": "No TLS-RPT record found.",
            "fix": f'Add TXT at _smtp._tls.{domain}:\n  v=TLSRPTv1; rua=mailto:tls-reports@{domain}',
        }

    value = records[0]
    rua_match = re.search(r"rua=([^\s;]+)", value)
    rua = rua_match.group(1) if rua_match else None

    if not rua:
        return {
            "status": "warn",
            "value": value,
            "explanation": "TLS-RPT record found but no reporting address (rua=).",
            "fix": f"Add rua=mailto:tls-reports@{domain} to your TLS-RPT record.",
        }

    return {
        "status": "pass",
        "value": value,
        "explanation": f"TLS-RPT record valid. Reports sent to: {rua}.",
        "fix": None,
    }


# ─── PTR / rDNS ───────────────────────────────────────────────────────────────

def check_ptr(domain: str) -> dict:
    """Check PTR records for IPs of MX hosts."""
    mx_records = _query_mx(domain)
    if not mx_records:
        return {
            "status": "fail",
            "value": None,
            "explanation": "No MX records to check PTR for.",
            "fix": "Set up MX records first.",
        }

    results = []
    all_pass = True

    for mx in mx_records[:3]:   # check top 3 MX hosts
        host = mx["host"]
        ips = _query_a(host)
        for ip in ips[:2]:
            try:
                rev_name = dns.reversename.from_address(ip)
                ptr_answers = dns.resolver.resolve(rev_name, "PTR", lifetime=5)
                ptr_hostname = str(ptr_answers[0]).rstrip(".")
                match = ptr_hostname == host or ptr_hostname.endswith(f".{domain}")
                results.append({
                    "ip": ip,
                    "ptr": ptr_hostname,
                    "matches_mx": match,
                })
                if not match:
                    all_pass = False
            except Exception:
                results.append({"ip": ip, "ptr": None, "matches_mx": False})
                all_pass = False

    status = "pass" if all_pass else "warn"
    explanation = "PTR records match MX hostnames." if all_pass else \
                  "Some PTR records missing or don't match MX hostnames."

    return {
        "status": status,
        "value": results,
        "explanation": explanation,
        "fix": None if all_pass else "Ensure reverse DNS (PTR) is set by your hosting/ESP provider for each sending IP.",
    }


# ─── Full Check ───────────────────────────────────────────────────────────────

def run_full_check(domain: str, dkim_selector: Optional[str] = None, skip_slow: bool = False, esp: Optional[str] = None) -> dict:
    """Run all DNS checks for a domain. Returns consolidated result dict."""
    spf = check_spf(domain)

    # DKIM selector priority:
    # 1. Explicitly passed selector (manual lookup / custom selector)
    # 2. ESP domain registry (most accurate)
    # 3. Warn — no fallback guessing
    if dkim_selector:
        dkim = check_dkim(domain, dkim_selector)
    else:
        try:
            from esp_domain_sync_service import get_selector_for_domain
            registry_selectors = get_selector_for_domain(domain)
        except Exception:
            registry_selectors = []

        if registry_selectors:
            # Try each selector, use first that passes
            dkim = None
            for sel in registry_selectors:
                result = check_dkim(domain, sel)
                if result['status'] == 'pass':
                    dkim = result
                    break
            if dkim is None:
                # All selectors tried, use last result
                dkim = result
        else:
            # Not in registry — warn, no lookup
            dkim = {
                'status': 'warn',
                'selector': None,
                'value': None,
                'explanation': 'Selector ID not found in ESP registry. Run ESP domain sync or provide selector manually.',
                'fix': 'Trigger ESP domain sync or add selector manually in DNS Looker settings.',
                'needs_selector': True,
            }
    dmarc   = check_dmarc(domain)
    mx      = check_mx(domain)
    bimi    = check_bimi(domain)
    mta_sts = check_mta_sts(domain) if not skip_slow else {'status': 'skip', 'value': None, 'mode': None, 'explanation': 'Skipped in bulk mode.', 'fix': None}
    tls_rpt = check_tls_rpt(domain)
    ptr     = check_ptr(domain) if not skip_slow else {'status': 'skip', 'value': None, 'explanation': 'Skipped in bulk mode.', 'fix': None}
    spf_chain = get_spf_chain(domain)

    return {
        "domain": domain,
        "spf":     spf,
        "dkim":    dkim,
        "dmarc":   dmarc,
        "mx":      mx,
        "bimi":    bimi,
        "mta_sts": mta_sts,
        "tls_rpt": tls_rpt,
        "ptr":     ptr,
        "spf_chain": spf_chain,
    }
