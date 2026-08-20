"""Reusable read-only SQLite-over-SSH client for the EC2 data box.

All observatory metrics live in SQLite files on EC2
(/home/ec2-user/pani/blueshift_observatory/data/*.db). The Druid brokers are
VPC-internal and unreachable from a laptop, but these daily-cached DBs give us
sends/delivered/bounces/reputation without needing live Druid.

This module runs a parameterized query on EC2 over SSH and returns row dicts,
so every metric source (Postmaster, daily metrics, bounces, SNDS) shares one
code path. Config comes from .env (EC2_HOST/EC2_USER/EC2_KEY/EC2_DATA_DIR).
"""

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

EC2_HOST = os.getenv("EC2_HOST", "")
EC2_USER = os.getenv("EC2_USER", "ec2-user")
EC2_KEY = os.path.expanduser(os.getenv("EC2_KEY", "~/.ssh/id_rsa"))
DATA_DIR = os.getenv("EC2_DATA_DIR", "/home/ec2-user/pani/blueshift_observatory/data")


class EC2DataError(RuntimeError):
    """Human-friendly error the agent can relay to the user."""


# Executed on EC2 via `python3 -`; reads args from env to avoid quoting/injection.
_REMOTE_SCRIPT = (
    "import sqlite3, json, os\n"
    "conn = sqlite3.connect('file:' + os.environ['DB'] + '?mode=ro', uri=True)\n"
    "conn.row_factory = sqlite3.Row\n"
    "rows = [dict(r) for r in conn.execute(os.environ['SQL'], json.loads(os.environ['PARAMS'])).fetchall()]\n"
    "print(json.dumps(rows, default=str))\n"
)


def query(db, sql, params=()):
    """Run a read-only query against an EC2 SQLite DB; return a list of dicts.

    `db` may be a bare filename (resolved under DATA_DIR) or an absolute path.
    """
    if not EC2_HOST:
        raise EC2DataError("EC2_HOST is not set. Add EC2_* settings to .env.")
    db_path = db if db.startswith("/") else f"{DATA_DIR.rstrip('/')}/{db}"

    remote_env = (
        f"DB={shlex.quote(db_path)} "
        f"SQL={shlex.quote(sql)} "
        f"PARAMS={shlex.quote(json.dumps(list(params)))} "
        "python3 -"
    )
    cmd = [
        "ssh", "-i", EC2_KEY,
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
        "-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR",
        f"{EC2_USER}@{EC2_HOST}", remote_env,
    ]

    # Retry only transient SSH transport failures (timeout, or ssh's own exit
    # code 255 = connection error). A remote error (bad SQL → exit 1) is NOT
    # retried, since re-running it would fail identically.
    attempts = 3
    last_err = "unknown error"
    for attempt in range(attempts):
        try:
            proc = subprocess.run(cmd, input=_REMOTE_SCRIPT, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            last_err = f"SSH to EC2 ({EC2_HOST}) timed out"
        except FileNotFoundError:
            raise EC2DataError("ssh not found on this machine.")
        else:
            if proc.returncode == 0:
                out = proc.stdout.strip()
                return json.loads(out) if out else []
            if proc.returncode == 255:  # ssh transport failure → retryable
                last_err = f"SSH connection failed: {proc.stderr.strip() or 'ssh error'}"
            else:  # remote/query error → not retryable
                raise EC2DataError(f"EC2 query failed: {proc.stderr.strip() or 'ssh error'}")

        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))  # 1.5s, then 3s

    raise EC2DataError(f"{last_err} after {attempts} attempts. Are you on the VPN/network that can reach {EC2_HOST}?")
