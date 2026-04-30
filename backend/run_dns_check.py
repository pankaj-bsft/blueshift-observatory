import sqlite3, os, time
from pathlib import Path
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dns_looker import run_full_check
from dns_storage_service import save_dns_result, get_all_custom_selectors
from account_mapping_service import get_all_mappings

data_dir = Path(os.getenv('BS_DATA_DIR', Path(__file__).resolve().parents[1] / 'data'))
conn = sqlite3.connect(str(data_dir / 'deliverability_history.db'))
c = conn.cursor()
today = date.today()
start = (today - timedelta(days=30)).strftime('%Y-%m-%d')
c.execute("SELECT from_domain, MAX(esp) as esp FROM daily_metrics WHERE report_date >= ? AND from_domain != '' AND from_domain IS NOT NULL GROUP BY from_domain ORDER BY from_domain", (start,))
rows = c.fetchall()
conn.close()

domain_esp_map = {r[0]: r[1] for r in rows}
domains = sorted(domain_esp_map.keys())
print(f'Total: {len(domains)}', flush=True)

account_map = {m['sending_domain']: m['account_name'] for m in get_all_mappings(limit=100000).get('mappings', [])}
custom_selectors = get_all_custom_selectors()

def check_one(domain):
    esp = domain_esp_map.get(domain)
    # custom_selectors take priority; registry handles DKIM automatically in run_full_check
    result = run_full_check(domain, custom_selectors.get(domain), skip_slow=True)
    save_dns_result(result, account=account_map.get(domain), esp=esp, check_date=today)

t0 = time.time()
checked = errors = 0
with ThreadPoolExecutor(max_workers=30) as ex:
    futs = {ex.submit(check_one, d): d for d in domains}
    for f in as_completed(futs):
        try:
            f.result(); checked += 1
            if checked % 50 == 0:
                e = time.time()-t0; print(f'  {checked}/{len(domains)} — {checked/e:.1f}/s — ETA {(len(domains)-checked)*e/checked:.0f}s', flush=True)
        except Exception as ex2:
            errors += 1; print(f'  ERR {futs[f]}: {ex2}', flush=True)

print(f'Done! {checked}/{len(domains)} in {time.time()-t0:.0f}s, errors:{errors}')
