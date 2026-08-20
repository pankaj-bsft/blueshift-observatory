import re

import dns.resolver

# Selectors commonly used by major ESPs. Used to auto-probe DKIM when the
# caller doesn't know the selector (DKIM has no way to be discovered from DNS).
COMMON_DKIM_SELECTORS = [
    "google", "default", "selector1", "selector2", "k1", "k2", "k3",
    "mail", "dkim", "s1", "s2", "smtp", "mandrill", "mxvault",
    "dkim1", "sig1", "zoho", "pm", "fdm", "sm", "scph0620",
    "sendgrid", "litesrv", "ctct1", "ctct2", "mte1", "turbo-smtp",
    "everlytickey1", "everlytickey2", "krs",
]


class DNSChecker:
    """Thin, exception-safe wrapper around dnspython for email-auth lookups."""

    @staticmethod
    def get_txt(domain):
        try:
            answers = dns.resolver.resolve(domain, "TXT")
            records = []
            for answer in answers:
                txt = "".join(
                    part.decode() if isinstance(part, bytes) else part
                    for part in answer.strings
                )
                records.append(txt)
            return records
        except Exception:
            return []

    @staticmethod
    def get_spf(domain):
        for record in DNSChecker.get_txt(domain):
            if record.lower().startswith("v=spf1"):
                return record
        return None

    @staticmethod
    def get_dmarc(domain):
        for record in DNSChecker.get_txt(f"_dmarc.{domain}"):
            if record.lower().startswith("v=dmarc1"):
                return record
        return None

    @staticmethod
    def get_dmarc_with_source(domain):
        """Resolve DMARC the way receivers do (RFC 7489).

        Check the exact domain first; if it has no record, walk up to the
        organizational domain and inherit its policy (the 'sp=' subdomain tag
        then governs). Returns a dict with the record and where it came from,
        so callers can tell an inherited policy from a domain-level one.
        """
        labels = domain.split(".")
        for i in range(len(labels) - 1):  # stops before the bare TLD
            candidate = ".".join(labels[i:])
            record = DNSChecker.get_dmarc(candidate)
            if record:
                return {
                    "record": record,
                    "source": candidate,
                    "inherited": candidate != domain,
                }
        return None

    @staticmethod
    def get_mx(domain):
        try:
            answers = dns.resolver.resolve(domain, "MX")
            results = []
            for x in answers:
                exchange = str(x.exchange).rstrip(".")
                if not exchange:  # null MX (RFC 7505): target is "."
                    results.append(f"{x.preference} (null MX — domain intentionally sends/receives no mail)")
                else:
                    results.append(f"{x.preference} {exchange}")
            return sorted(results)
        except Exception:
            return []

    @staticmethod
    def get_dkim(domain, selector):
        """Look up a single DKIM key at <selector>._domainkey.<domain>."""
        host = f"{selector}._domainkey.{domain}"
        for record in DNSChecker.get_txt(host):
            if "v=dkim1" in record.lower() or "p=" in record.lower():
                return {"selector": selector, "host": host, "record": record}
        return None

    @staticmethod
    def dkim_key_is_empty(record):
        """True for a published-but-revoked DKIM record (empty p= value)."""
        return bool(re.search(r"p=\s*(;|$)", record.lower()))

    @staticmethod
    def probe_dkim(domain, selectors=None):
        """Try common selectors; return only selectors with an ACTIVE key.

        Some domains publish a wildcard/revoked record (empty p=) at every
        selector name; those aren't real keys, so we skip them here to avoid
        reporting dozens of phantom "revoked" selectors. Use get_dkim() for an
        explicit selector if you want to see a revoked record.
        """
        found = []
        for selector in (selectors or COMMON_DKIM_SELECTORS):
            hit = DNSChecker.get_dkim(domain, selector)
            if hit and not DNSChecker.dkim_key_is_empty(hit["record"]):
                found.append(hit)
        return found

    @staticmethod
    def get_bimi(domain, selector="default"):
        for record in DNSChecker.get_txt(f"{selector}._bimi.{domain}"):
            if record.lower().startswith("v=bimi1"):
                return record
        return None

    @staticmethod
    def get_mta_sts(domain):
        """DNS TXT indicator record. Full policy lives at an HTTPS URL."""
        for record in DNSChecker.get_txt(f"_mta-sts.{domain}"):
            if record.lower().startswith("v=stsv1"):
                return record
        return None

    @staticmethod
    def get_tls_rpt(domain):
        for record in DNSChecker.get_txt(f"_smtp._tls.{domain}"):
            if record.lower().startswith("v=tlsrptv1"):
                return record
        return None
