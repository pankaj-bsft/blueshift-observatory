import json
import os
import subprocess
from typing import Dict, List


SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "scripts", "bsft_account_mappings.rb")


def _run_bsft_script() -> List[Dict]:
    """Run the bsft-models Ruby script and return mappings list."""
    required = ["BSFT_DB_HOST", "BSFT_DB_NAME", "BSFT_DB_USER", "BSFT_DB_PASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    result = subprocess.run(
        ["ruby", SCRIPT_PATH],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to fetch Rails mappings")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from bsft script: {exc}") from exc

    return data.get("mappings", [])


def get_rails_mappings(search: str = "", limit: int = 1000, offset: int = 0) -> Dict:
    """Fetch mappings from Rails source (via bsft-models)."""
    mappings = _run_bsft_script()

    if search:
        query = search.lower()
        mappings = [
            m for m in mappings
            if (m.get("sending_domain") or "").lower().find(query) >= 0
            or (m.get("account_name") or "").lower().find(query) >= 0
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
    mappings = _run_bsft_script()
    total_mappings = len(mappings)
    total_accounts = len({m.get("account_name") for m in mappings if m.get("account_name")})
    return {
        "total_mappings": total_mappings,
        "total_accounts": total_accounts
    }
