"""Recursive SPF evaluation (RFC 7208 §4.6.4).

SPF caps the number of DNS-querying mechanisms at 10 across the ENTIRE
evaluation — every include/redirect you follow, and the mechanisms inside
those, all count. Blow the limit and SPF yields PermError, which receivers
commonly treat as a failure. A flat regex can't see this; you have to walk
the include tree. This module does that, with loop detection and void-lookup
tracking.

Counted (1 each): include, a, mx, ptr, exists, redirect
Not counted:      ip4, ip6, all, exp, v=spf1
"""

import dns.resolver

from .dns_checker import DNSChecker

QUALIFIERS = "+-~?"


class SPFEvaluator:
    LOOKUP_LIMIT = 10
    VOID_LIMIT = 2       # RFC 7208 §4.6.4: >2 void lookups is a PermError
    MAX_DEPTH = 20       # safety net against pathological trees

    def __init__(self):
        self.lookups = 0
        self.void_lookups = 0
        self.errors = []
        self.warnings = []
        self.seen = set()  # domains already expanded → loop guard

    def evaluate(self, domain):
        record = DNSChecker.get_spf(domain)
        if record is None:
            self.errors.append(f"No SPF record found for {domain}.")
            tree = {"domain": domain, "record": None, "terms": []}
        else:
            tree = self._expand(domain, record, 0)
        return {
            "domain": domain,
            "record": record,
            "lookups": self.lookups,
            "limit": self.LOOKUP_LIMIT,
            "exceeded": self.lookups > self.LOOKUP_LIMIT,
            "void_lookups": self.void_lookups,
            "void_exceeded": self.void_lookups > self.VOID_LIMIT,
            "errors": self.errors,
            "warnings": self.warnings,
            "tree": tree,
        }

    @staticmethod
    def _strip_qualifier(token):
        return token[1:] if token and token[0] in QUALIFIERS else token

    def _expand(self, domain, record, depth):
        node = {"domain": domain, "record": record, "terms": []}
        if depth > self.MAX_DEPTH:
            self.errors.append(f"Max recursion depth exceeded at {domain}.")
            return node
        self.seen.add(domain.lower())

        tokens = [t for t in record.split() if t.lower() != "v=spf1"]
        # 'redirect' is ignored by receivers when an 'all' mechanism is present.
        has_all = any(self._strip_qualifier(t.lower()) == "all" for t in tokens)

        for tok in tokens:
            low = self._strip_qualifier(tok.lower())

            if low.startswith("redirect="):
                target = tok.split("=", 1)[1]
                if has_all:
                    self.warnings.append(f"'redirect={target}' is ignored because an 'all' mechanism is present.")
                    node["terms"].append({"term": tok, "type": "redirect", "cost": 0, "ignored": True})
                else:
                    self.lookups += 1
                    child = self._follow(target, depth)
                    node["terms"].append({"term": tok, "type": "redirect", "cost": 1, "child": child})
            elif low.startswith("exp="):
                node["terms"].append({"term": tok, "type": "exp", "cost": 0})
            elif low.startswith("include:"):
                target = tok.split(":", 1)[1]
                self.lookups += 1
                child = self._follow(target, depth)
                node["terms"].append({"term": tok, "type": "include", "cost": 1, "child": child})
            elif low == "a" or low.startswith("a:") or low.startswith("a/"):
                self.lookups += 1
                node["terms"].append({"term": tok, "type": "a", "cost": 1})
            elif low == "mx" or low.startswith("mx:") or low.startswith("mx/"):
                self.lookups += 1
                self._check_mx(domain if low == "mx" else low.split(":", 1)[1].split("/")[0])
                node["terms"].append({"term": tok, "type": "mx", "cost": 1})
            elif low == "ptr" or low.startswith("ptr:"):
                self.lookups += 1
                self.warnings.append("'ptr' mechanism is deprecated (RFC 7208) and should be removed.")
                node["terms"].append({"term": tok, "type": "ptr", "cost": 1})
            elif low.startswith("exists:"):
                self.lookups += 1
                node["terms"].append({"term": tok, "type": "exists", "cost": 1})
            elif low.startswith("ip4:") or low.startswith("ip6:"):
                node["terms"].append({"term": tok, "type": "ip", "cost": 0})
            elif low == "all":
                node["terms"].append({"term": tok, "type": "all", "cost": 0})
            else:
                self.warnings.append(f"Unrecognized SPF term: {tok}")
                node["terms"].append({"term": tok, "type": "unknown", "cost": 0})

        return node

    def _follow(self, target, depth):
        if target.lower() in self.seen:
            self.errors.append(f"SPF include/redirect loop detected at '{target}'.")
            return {"domain": target, "record": None, "terms": [], "loop": True}
        record = DNSChecker.get_spf(target)
        if record is None:
            self.void_lookups += 1
            self.errors.append(f"'{target}' has no SPF record (void lookup / PermError source).")
            return {"domain": target, "record": None, "terms": [], "void": True}
        return self._expand(target, record, depth + 1)

    def _check_mx(self, target):
        try:
            answers = dns.resolver.resolve(target, "MX")
            if len(answers) > 10:
                self.errors.append(f"'mx' for {target} resolves to {len(answers)} records (>10) → PermError.")
        except Exception:
            self.void_lookups += 1
