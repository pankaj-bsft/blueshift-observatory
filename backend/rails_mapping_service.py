"""
Read account -> sending domain mappings from the Rails (Blueshift) MySQL database.

Previously this shelled out to scripts/bsft_account_mappings.rb, which loaded the
bsft-models gem via ActiveRecord. That never worked here: the gem chain
(activerecord, mysql2, devise, attr_encrypted, ...) was not installed under macOS
system Ruby, and the script hardcoded an absolute path to a local bsft-models
checkout that does not exist on the EC2 host. The Ruby script only performed a join
and a string split, so it is done directly in SQL instead - no Ruby, no gems, and
the same code path works on both machines.

The Ruby script is left in place for reference but is no longer used.
"""

import os
import time
from typing import Dict, List, Optional

import pymysql

# Mirrors the Ruby version: AccountAdapter -> Account -> BillingAccount, preferring the
# billing account name and falling back to the account name.
MAPPING_SQL = """
SELECT
    aa.id                AS id,
    aa.from_address      AS from_address,
    aa.created_at        AS created_at,
    COALESCE(NULLIF(TRIM(ba.name), ''), a.name) AS account_name
FROM account_adapters aa
JOIN accounts a          ON a.id = aa.account_id
LEFT JOIN billing_accounts ba ON ba.id = a.billing_account_id
WHERE aa.adapter_id IN ({adapter_placeholders})
  AND aa.from_address IS NOT NULL
"""

CACHE_TTL_SECONDS = 300
_cache: Dict = {"mappings": None, "fetched_at": 0.0}


def _required_env() -> Dict[str, str]:
    required = ["BSFT_DB_HOST", "BSFT_DB_NAME", "BSFT_DB_USER", "BSFT_DB_PASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return {
        "host": os.getenv("BSFT_DB_HOST"),
        "port": int(os.getenv("BSFT_DB_PORT") or 3306),
        "database": os.getenv("BSFT_DB_NAME"),
        "user": os.getenv("BSFT_DB_USER"),
        "password": os.getenv("BSFT_DB_PASSWORD"),
    }


def _adapter_ids() -> List[int]:
    """
    Which Rails adapter_id values count as sending adapters.

    Required, matching the Ruby script's behaviour - without it the query would return
    every adapter, silently producing wrong mappings rather than an obvious failure.
    """
    raw = os.getenv("BSFT_ADAPTER_IDS", "")
    ids = [int(part.strip()) for part in raw.split(",") if part.strip().isdigit()]
    if not ids:
        raise RuntimeError(
            'BSFT_ADAPTER_IDS must be set to the sending adapter ids, e.g. BSFT_ADAPTER_IDS="8,9,10". '
            "Ask the Blueshift engineering team which adapter_id values are authoritative."
        )
    return ids


def extract_domain(from_address: Optional[str]) -> Optional[str]:
    """Take the domain from a from_address, matching the Ruby extract_domain."""
    if not from_address:
        return None
    address = str(from_address).strip().lower()
    if not address:
        return None
    if "@" in address:
        return address.split("@", 1)[1] or None
    return address


def _connect(config: Dict):
    """
    Open a read-only connection, translating driver errors into actionable messages.

    The failure modes here are genuinely different problems and were previously
    indistinguishable, so they are reported separately.
    """
    try:
        return pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            connect_timeout=15,
            read_timeout=120,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        if code in (2003, 2002):
            raise RuntimeError(
                f"Cannot reach the Rails MySQL server at {config['host']}:{config['port']} - "
                "the connection was refused or timed out. The host needs to accept connections "
                "from this machine (ask engineering to allowlist the source IP)."
            ) from exc
        if code in (1045, 1044):
            raise RuntimeError(
                "The Rails MySQL server rejected these credentials (BSFT_DB_USER / BSFT_DB_PASSWORD)."
            ) from exc
        if code == 1049:
            raise RuntimeError(
                f"The Rails MySQL server has no database named '{config['database']}' (BSFT_DB_NAME)."
            ) from exc
        raise RuntimeError(f"Rails MySQL connection failed: {exc}") from exc


def _fetch_mappings(force_refresh: bool = False) -> List[Dict]:
    """Fetch mappings from the Rails DB, cached briefly so one page load is one query."""
    now = time.time()
    if (
        not force_refresh
        and _cache["mappings"] is not None
        and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS
    ):
        return _cache["mappings"]

    config = _required_env()
    ids = _adapter_ids()
    sql = MAPPING_SQL.format(adapter_placeholders=", ".join(["%s"] * len(ids)))

    connection = _connect(config)
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute(sql, ids)
            except pymysql.err.ProgrammingError as exc:
                raise RuntimeError(
                    f"The Rails schema does not match what this query expects ({exc}). "
                    "Confirm the account_adapters / accounts / billing_accounts table and "
                    "column names with engineering, then adjust MAPPING_SQL."
                ) from exc
            rows = cursor.fetchall()
    finally:
        connection.close()

    mappings = []
    for row in rows:
        domain = extract_domain(row.get("from_address"))
        account_name = (row.get("account_name") or "").strip()
        # Same skips as the Ruby version: no domain or no account name means no mapping.
        if not domain or not account_name:
            continue
        created_at = row.get("created_at")
        mappings.append({
            "id": row.get("id"),
            "sending_domain": domain,
            "account_name": account_name,
            "is_affiliate": False,
            "notes": "",
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        })

    _cache["mappings"] = mappings
    _cache["fetched_at"] = now
    return mappings


def get_rails_mappings(search: str = "", limit: int = 1000, offset: int = 0) -> Dict:
    """Fetch mappings from the Rails source."""
    mappings = _fetch_mappings()

    if search:
        query = search.lower()
        mappings = [
            m for m in mappings
            if query in (m.get("sending_domain") or "").lower()
            or query in (m.get("account_name") or "").lower()
        ]

    total = len(mappings)
    sliced = mappings[offset: offset + limit]

    return {
        "mappings": sliced,
        "total": total,
        "limit": limit,
        "offset": offset
    }


def get_rails_mapping_stats() -> Dict:
    """Compute stats from Rails mappings."""
    mappings = _fetch_mappings()
    total_mappings = len(mappings)
    total_accounts = len({m.get("account_name") for m in mappings if m.get("account_name")})
    return {
        "total_mappings": total_mappings,
        "total_accounts": total_accounts
    }
