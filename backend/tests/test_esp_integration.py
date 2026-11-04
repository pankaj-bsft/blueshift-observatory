"""
Tests for esp_integration_service's Mailgun Shared/Dedicated IP classification
and the dedicated-IP compliance check.

Uses unittest.mock to simulate Mailgun's API responses -- the IP Type check
has been wrong twice already:
1. IP Type conflated "exclusive to one domain" with Mailgun's own
   "account-owned, not the generic shared pool" definition, reported via a
   real customer domain: email.rentcafe.com's IPs are account-owned/
   Dedicated in Mailgun but were shown as Shared here because 7 sibling
   domains route through the same 2 IPs.
2. After fixing #1 by checking membership in GET /v3/ips' flat `items`
   list, a second report (mail.lendable.co.uk) showed that `items` is NOT
   a list of dedicated IPs -- it includes both dedicated AND shared-pool
   IPs the account has any relationship with. The real per-IP truth is
   each entry's own `dedicated` boolean in the response's `details` array.
   Confirmed live: 141.193.32.6 has dedicated=false (Shared) while
   204.220.173.55 has dedicated=true (Dedicated), yet both appeared in
   `items`.

Separately, the "Needs Dedicated IP" compliance flag's definition changed
by explicit user request: it used to fire on ANY sibling-domain IP reuse
regardless of ip_type (e.g. email.rentcafe.com, fully Dedicated, still
flagged because 7 sibling domains shared its 2 owned IPs). The user found
that surprising for domains with an all-Dedicated ip_type (e.g.
send-m.usnews.com) and asked for the flag to key off ip_type instead:
fire only when ip_type is "Shared" or "Mixed" (i.e. at least one
non-Dedicated IP is involved), never for an all-Dedicated domain no
matter how many sibling domains share those IPs.

These tests encode the corrected, verified-against-the-real-reports
behavior as a regression guard.

Run with: cd backend && ./venv/bin/pytest tests/test_esp_integration.py -v
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def _ips_response(dedicated_ips=(), shared_ips=()):
    """Builds a GET /v3/ips mock response with both `items` (every IP the
    account has any relationship with) and `details` (the real per-IP
    `dedicated` ground truth) -- mirroring Mailgun's actual shape, where
    `items` alone is NOT a list of dedicated IPs."""
    details = (
        [{'ip': ip, 'dedicated': True, 'enabled': True, 'is_on_warmup': False} for ip in dedicated_ips] +
        [{'ip': ip, 'dedicated': False, 'enabled': True, 'is_on_warmup': False} for ip in shared_ips]
    )
    items = list(dedicated_ips) + list(shared_ips)
    return _mock_response(200, {'items': items, 'details': details})


def _router(domains_payload, owned_ips_payload, domain_ips_payloads):
    """Builds a fake requests.get that correctly distinguishes Mailgun's
    account-level GET /v3/ips (account-owned pool) from a domain-specific
    GET /v3/domains/{name}/ips -- both URLs end with "/ips", so the
    domain-specific (more specific) pattern must be checked FIRST.
    """
    def fake_get(url, **kwargs):
        if '/domains/' in url and url.endswith('/ips'):
            for name, payload in domain_ips_payloads.items():
                if url.endswith(f'/domains/{name}/ips'):
                    return _mock_response(200, {'items': payload})
            return _mock_response(200, {'items': []})
        if url.endswith('/domains'):
            return _mock_response(200, domains_payload)
        if url.endswith('/ips'):
            return owned_ips_payload
        return _mock_response(404, {})
    return fake_get


@pytest.fixture()
def esp(monkeypatch, tmp_path):
    """Fresh esp_integration_service bound to a throwaway account_info.db and
    a tmp pulsation DB (so get_trailing_sent_totals finds no table and
    returns {} cleanly, matching a fresh environment)."""
    monkeypatch.setenv('BS_DATA_DIR', str(tmp_path))
    for mod in ('data_paths', 'esp_integration_service', 'pulsation_service'):
        sys.modules.pop(mod, None)
    import esp_integration_service as esp_svc
    yield esp_svc


class TestMailgunIpTypeClassification:
    def test_ip_on_accounts_owned_pool_is_dedicated_even_when_shared_with_sibling_domains(self, esp, monkeypatch):
        """The exact reported bug: email.rentcafe.com's 2 IPs are owned by
        the account (genuinely Dedicated per Mailgun) but also used by 7
        other domains under the same client -- IP Type must still read
        Dedicated, matching what Mailgun itself shows. And per the later
        policy change, an all-Dedicated ip_type is no longer flagged by the
        compliance check even though sibling domains share those same
        IPs -- ownership, not sibling reuse, is what clears the concern."""
        domains_payload = {'items': [
            {'name': 'email.rentcafe.com', 'state': 'active', 'created_at': 'x'},
            {'name': 'living.rentcafe.com', 'state': 'active', 'created_at': 'x'},
        ]}
        fake_get = _router(
            domains_payload,
            _ips_response(dedicated_ips=['69.72.43.209', '161.38.195.219']),
            {
                'email.rentcafe.com': ['69.72.43.209', '161.38.195.219'],
                'living.rentcafe.com': ['69.72.43.209', '161.38.195.219'],
            },
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={
            ('email.rentcafe.com', 'US'): 3_327_940,
        }):
            results = esp.fetch_mailgun_data()

        # The mock doesn't distinguish US/EU base URLs, so each domain shows
        # up once per region -- pin to US, which is the one given real sent
        # volume above.
        by_domain = {(r['domain'], r['region']): r for r in results}
        assert by_domain[('email.rentcafe.com', 'US')]['ip_type'] == 'Dedicated'
        assert by_domain[('living.rentcafe.com', 'US')]['ip_type'] == 'Dedicated'
        # Fully Dedicated -> not flagged, even with high volume and sibling
        # domains sharing the same owned IPs.
        assert by_domain[('email.rentcafe.com', 'US')]['needs_dedicated_ip'] is False

    def test_domain_with_no_ip_assigned_is_shared(self, esp, monkeypatch):
        domains_payload = {'items': [{'name': 'nopool.example.com', 'state': 'active', 'created_at': 'x'}]}
        fake_get = _router(
            domains_payload,
            _ips_response(dedicated_ips=['1.2.3.4']),
            {'nopool.example.com': []},
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={
            ('nopool.example.com', 'US'): 5_000_000,
        }):
            results = esp.fetch_mailgun_data()

        row = next(r for r in results if r['domain'] == 'nopool.example.com' and r['region'] == 'US')
        assert row['ip_type'] == 'Shared'
        assert row['needs_dedicated_ip'] is True  # no IP at all is the literal shared-pool case

    def test_exclusive_dedicated_ip_is_dedicated_and_not_flagged(self, esp, monkeypatch):
        """A domain alone on its own account-owned IP: Dedicated, and no
        compliance concern regardless of volume."""
        domains_payload = {'items': [{'name': 'solo.example.com', 'state': 'active', 'created_at': 'x'}]}
        fake_get = _router(
            domains_payload,
            _ips_response(dedicated_ips=['9.9.9.9']),
            {'solo.example.com': ['9.9.9.9']},
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={
            ('solo.example.com', 'US'): 5_000_000,
        }):
            results = esp.fetch_mailgun_data()

        row = next(r for r in results if r['domain'] == 'solo.example.com')
        assert row['ip_type'] == 'Dedicated'
        assert row['needs_dedicated_ip'] is False

    def test_ip_not_in_owned_pool_is_shared_even_if_exclusive_to_one_domain(self, esp, monkeypatch):
        """A domain alone on an IP that ISN'T in the account's owned pool
        (Mailgun's generic shared infrastructure, coincidentally not reused
        by another of this account's domains) must read Shared -- exclusivity
        to one domain is not what makes an IP Dedicated in Mailgun's sense."""
        domains_payload = {'items': [{'name': 'onsharedpool.example.com', 'state': 'active', 'created_at': 'x'}]}
        fake_get = _router(
            domains_payload,
            _ips_response(dedicated_ips=['5.5.5.5']),  # account owns a different IP
            {'onsharedpool.example.com': ['8.8.8.8']},  # not in the owned pool at all
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={}):
            results = esp.fetch_mailgun_data()

        row = next(r for r in results if r['domain'] == 'onsharedpool.example.com')
        assert row['ip_type'] == 'Shared'

    def test_falls_back_to_old_heuristic_when_owned_ip_lookup_fails(self, esp, monkeypatch):
        """If GET /v3/ips itself fails, don't confidently mislabel every
        domain "Shared" -- fall back to the per-domain-exclusivity heuristic
        rather than show a wrong answer with full confidence."""
        domains_payload = {'items': [{'name': 'fallback.example.com', 'state': 'active', 'created_at': 'x'}]}
        fake_get = _router(
            domains_payload,
            _mock_response(500, {}),  # account IP lookup fails
            {'fallback.example.com': ['3.3.3.3']},
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={}):
            results = esp.fetch_mailgun_data()

        row = next(r for r in results if r['domain'] == 'fallback.example.com')
        # Exclusive to itself under the fallback heuristic -> Dedicated.
        assert row['ip_type'] == 'Dedicated'

    def test_domain_with_one_owned_and_one_shared_ip_is_mixed_with_per_ip_detail(self, esp, monkeypatch):
        """A domain CAN have both a dedicated and a shared-pool IP at the
        same time -- collapsing that to one domain-wide label hides it.
        ip_type must read "Mixed", and ip_details must show which IP is
        which so the per-IP truth is visible, not just a single verdict.

        Mirrors the real mail.lendable.co.uk report: BOTH IPs appear in
        Mailgun's `items` list, but only `details[].dedicated` tells them
        apart (141.193.32.6=false/Shared, 198.244.62.220=true/Dedicated) --
        a plain items-membership check wrongly calls both "Dedicated"."""
        domains_payload = {'items': [{'name': 'mixed.example.com', 'state': 'active', 'created_at': 'x'}]}
        fake_get = _router(
            domains_payload,
            _ips_response(dedicated_ips=['198.244.62.220'], shared_ips=['141.193.32.6']),
            {'mixed.example.com': ['141.193.32.6', '198.244.62.220']},
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={}):
            results = esp.fetch_mailgun_data()

        row = next(r for r in results if r['domain'] == 'mixed.example.com')
        assert row['ip_type'] == 'Mixed'
        details_by_ip = {d['ip']: d['dedicated'] for d in row['ip_details']}
        assert details_by_ip == {'141.193.32.6': False, '198.244.62.220': True}

    def test_mixed_ip_type_is_flagged_when_over_threshold(self, esp, monkeypatch):
        """A "Mixed" domain (at least one non-Dedicated IP) over the volume
        threshold must still be flagged -- only an all-Dedicated ip_type
        clears the compliance concern, not Mixed."""
        domains_payload = {'items': [{'name': 'mixed-flagged.example.com', 'state': 'active', 'created_at': 'x'}]}
        fake_get = _router(
            domains_payload,
            _ips_response(dedicated_ips=['198.244.62.220'], shared_ips=['141.193.32.6']),
            {'mixed-flagged.example.com': ['141.193.32.6', '198.244.62.220']},
        )

        monkeypatch.setattr(esp.requests, 'get', fake_get)
        with patch.object(esp.pulsation_service, 'get_trailing_sent_totals', return_value={
            ('mixed-flagged.example.com', 'US'): 5_000_000,
        }):
            results = esp.fetch_mailgun_data()

        row = next(r for r in results if r['domain'] == 'mixed-flagged.example.com' and r['region'] == 'US')
        assert row['ip_type'] == 'Mixed'
        assert row['needs_dedicated_ip'] is True
