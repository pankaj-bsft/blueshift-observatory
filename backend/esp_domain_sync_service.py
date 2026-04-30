
import requests, sqlite3, json, re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    MAILGUN_API_KEY, MAILGUN_US_BASE_URL, MAILGUN_EU_BASE_URL,
    SPARKPOST_API_KEY, SPARKPOST_BASE_URL,
    SENDGRID_API_KEY, SENDGRID_BASE_URL,
)
from data_paths import data_path

DNS_DB_PATH = data_path("dns_looker.db")

def init_esp_domain_registry():
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS esp_domain_registry (
        domain TEXT NOT NULL, esp TEXT NOT NULL,
        selector_ids TEXT, subdomain TEXT,
        is_active INTEGER DEFAULT 1, last_synced TIMESTAMP,
        raw_data TEXT, PRIMARY KEY (domain, esp))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_edr_domain ON esp_domain_registry(domain)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_edr_esp ON esp_domain_registry(esp)")
    conn.commit()
    conn.close()

def _extract_sg_selector(host, domain):
    m = re.match(r"^([^.]+)\._domainkey\." + re.escape(domain) + r"$", host, re.IGNORECASE)
    return m.group(1) if m else None

def _fetch_sg_domains(headers):
    """Fetch whitelabel domains with given headers (main or on-behalf-of subuser)."""
    results = {}
    try:
        resp = requests.get(f"{SENDGRID_BASE_URL}/whitelabel/domains",
            headers=headers, timeout=15)
        if resp.status_code != 200:
            return results
        for entry in resp.json():
            domain = entry.get("domain", "").lower().strip()
            if not domain: continue
            dns = entry.get("dns", {})
            selectors = []
            for key in dns:
                if key.startswith("dkim"):
                    sel = _extract_sg_selector(dns[key].get("host", ""), domain)
                    if sel and sel not in selectors:
                        selectors.append(sel)
            results[domain] = {"esp": "SendGrid", "domain": domain, "selector_ids": selectors,
                "subdomain": entry.get("subdomain", ""), "raw": entry}
    except Exception as e:
        print(f"[ESP Sync] SendGrid fetch error: {e}")
    return results


def sync_sendgrid():
    results = {}
    base_headers = {"Authorization": f"Bearer {SENDGRID_API_KEY}"}

    # Main account domains
    main_domains = _fetch_sg_domains(base_headers)
    results.update(main_domains)
    print(f"[ESP Sync] SendGrid main: {len(main_domains)} domains")

    # Subuser domains
    try:
        resp = requests.get(f"{SENDGRID_BASE_URL}/subusers?limit=500",
            headers=base_headers, timeout=15)
        if resp.status_code == 200:
            subusers = [s.get("username") for s in resp.json() if s.get("username")]
            print(f"[ESP Sync] SendGrid subusers: {len(subusers)}")

            def fetch_subuser(username):
                headers = {**base_headers, "on-behalf-of": username}
                return _fetch_sg_domains(headers)

            with ThreadPoolExecutor(max_workers=10) as ex:
                futs = {ex.submit(fetch_subuser, u): u for u in subusers}
                for f in as_completed(futs):
                    try:
                        sub_domains = f.result()
                        results.update(sub_domains)
                    except Exception as e:
                        print(f"[ESP Sync] SendGrid subuser error: {e}")
        else:
            print(f"[ESP Sync] SendGrid subusers error: {resp.status_code}")
    except Exception as e:
        print(f"[ESP Sync] SendGrid subusers fetch error: {e}")

    print(f"[ESP Sync] SendGrid total: {len(results)} domains")
    return {"domains": results, "errors": []}

def _extract_mg_selector(records, domain):
    selectors = []
    pat = re.compile(r"^([^.]+)\._domainkey\." + re.escape(domain) + r"$", re.IGNORECASE)
    for rec in records:
        m = pat.match(rec.get("name", ""))
        if m and m.group(1) not in selectors:
            selectors.append(m.group(1))
    return selectors

def _fetch_mg_detail(base_url, domain_name):
    try:
        resp = requests.get(f"{base_url}/domains/{domain_name}",
            auth=("api", MAILGUN_API_KEY), timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[ESP Sync] MG detail error {domain_name}: {e}")
    return {}

def sync_mailgun():
    all_domains = {}
    errors = []
    for region, base_url in [("US", MAILGUN_US_BASE_URL), ("EU", MAILGUN_EU_BASE_URL)]:
        try:
            resp = requests.get(f"{base_url}/domains?limit=1000",
                auth=("api", MAILGUN_API_KEY), timeout=15)
            if resp.status_code != 200:
                errors.append(f"Mailgun {region}: {resp.status_code}")
                continue
            domain_names = [d.get("name","") for d in resp.json().get("items",[]) if d.get("name")]
            print(f"[ESP Sync] Mailgun {region}: {len(domain_names)} domains")
            def fetch_one(name):
                data = _fetch_mg_detail(base_url, name)
                sels = _extract_mg_selector(data.get("sending_dns_records",[]), name)
                return name, sels, data
            with ThreadPoolExecutor(max_workers=20) as ex:
                futs = {ex.submit(fetch_one, n): n for n in domain_names}
                for f in as_completed(futs):
                    try:
                        name, sels, raw = f.result()
                        if name:
                            all_domains[name.lower()] = {"esp":"Mailgun","domain":name.lower(),
                                "selector_ids":sels,"subdomain":"","raw":raw,"region":region}
                    except Exception as e:
                        print(f"[ESP Sync] MG domain error: {e}")
        except Exception as e:
            errors.append(str(e))
            print(f"[ESP Sync] Mailgun {region} error: {e}")
    print(f"[ESP Sync] Mailgun: {len(all_domains)} domains")
    return {"domains": all_domains, "errors": errors}

def _fetch_sp_detail(domain_name):
    try:
        resp = requests.get(f"{SPARKPOST_BASE_URL}/sending-domains/{domain_name}",
            headers={"Authorization": SPARKPOST_API_KEY}, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("results", {})
    except Exception as e:
        print(f"[ESP Sync] SP detail error {domain_name}: {e}")
    return {}

def sync_sparkpost():
    all_domains = {}
    errors = []
    try:
        resp = requests.get(f"{SPARKPOST_BASE_URL}/sending-domains",
            headers={"Authorization": SPARKPOST_API_KEY}, timeout=15)
        if resp.status_code != 200:
            return {"domains": {}, "errors": [f"SparkPost {resp.status_code}"]}
        domain_names = [d.get("domain","") for d in resp.json().get("results",[]) if d.get("domain")]
        print(f"[ESP Sync] SparkPost: {len(domain_names)} domains")
        def fetch_one(name):
            detail = _fetch_sp_detail(name)
            sel = detail.get("dkim", {}).get("selector", "")
            return name, [sel] if sel else [], detail
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(fetch_one, n): n for n in domain_names}
            for f in as_completed(futs):
                try:
                    name, sels, raw = f.result()
                    if name:
                        all_domains[name.lower()] = {"esp":"SparkPost","domain":name.lower(),
                            "selector_ids":sels,"subdomain":"","raw":raw}
                except Exception as e:
                    print(f"[ESP Sync] SP domain error: {e}")
    except Exception as e:
        errors.append(str(e))
        print(f"[ESP Sync] SparkPost error: {e}")
    print(f"[ESP Sync] SparkPost: {len(all_domains)} domains")
    return {"domains": all_domains, "errors": errors}

def save_esp_domains(esp_results):
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    upserted = 0
    active_keys = set()
    for domain, info in esp_results.items():
        esp = info["esp"]
        active_keys.add((domain, esp))
        c.execute("""INSERT INTO esp_domain_registry (domain,esp,selector_ids,subdomain,is_active,last_synced,raw_data)
            VALUES (?,?,?,?,1,?,?)
            ON CONFLICT(domain,esp) DO UPDATE SET selector_ids=excluded.selector_ids,
            subdomain=excluded.subdomain,is_active=1,last_synced=excluded.last_synced,raw_data=excluded.raw_data""",
            (domain, esp, json.dumps(info.get("selector_ids",[])),
             info.get("subdomain",""), now, json.dumps(info.get("raw",{}))))
        upserted += 1
    esps_seen = set(v["esp"] for v in esp_results.values())
    deactivated = 0
    for esp in esps_seen:
        c.execute("SELECT domain FROM esp_domain_registry WHERE esp=? AND is_active=1", (esp,))
        existing = {(row[0], esp) for row in c.fetchall()}
        for domain, _ in existing - active_keys:
            c.execute("UPDATE esp_domain_registry SET is_active=0,last_synced=? WHERE domain=? AND esp=?",
                (now, domain, esp))
            deactivated += 1
    conn.commit()
    conn.close()
    return {"upserted": upserted, "deactivated": deactivated}

def get_selector_for_domain(domain):
    conn = sqlite3.connect(DNS_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT selector_ids FROM esp_domain_registry WHERE domain=? AND is_active=1 LIMIT 1",
        (domain.lower(),))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]: return []
    try: return json.loads(row[0])
    except: return []

def get_all_registry_domains():
    conn = sqlite3.connect(DNS_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT domain,esp,selector_ids,subdomain,last_synced FROM esp_domain_registry WHERE is_active=1 ORDER BY domain")
    rows = []
    for r in c.fetchall():
        d = dict(r)
        try: d["selector_ids"] = json.loads(d["selector_ids"] or "[]")
        except: d["selector_ids"] = []
        rows.append(d)
    conn.close()
    return rows

def run_esp_domain_sync():
    print("[ESP Sync] Starting full ESP domain sync...")
    all_domains = {}
    all_errors = {}
    for name, fn in [("SendGrid", sync_sendgrid), ("Mailgun", sync_mailgun), ("SparkPost", sync_sparkpost)]:
        try:
            result = fn()
            all_domains.update(result.get("domains", {}))
            errs = result.get("errors", [])
            if errs: all_errors[name] = errs
        except Exception as e:
            all_errors[name] = [str(e)]
            print(f"[ESP Sync] {name} failed: {e}")
    stats = save_esp_domains(all_domains)
    print(f"[ESP Sync] Done. {stats['upserted']} upserted, {stats['deactivated']} deactivated.")
    return {"status":"success","total_domains":len(all_domains),"stats":stats,"errors":all_errors}

init_esp_domain_registry()
