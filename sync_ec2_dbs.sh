#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Sync Pulsation + MBR data from this laptop to the EC2 box.
#
# IMPORTANT: this MERGES rows into the databases the app actually reads
# (.../blueshift_observatory/data/), using INSERT OR IGNORE / NOT EXISTS so
# it only adds missing rows and never overwrites or duplicates existing data.
# It is safe to run repeatedly (idempotent).
#
# The old version rsync-overwrote whole .db files into a `database/` folder
# that NOTHING reads, which is why the dashboard went stale. Do not point this
# back at `database/`.
# ---------------------------------------------------------------------------

EC2_HOST="172.31.249.157"
EC2_USER="ec2-user"
EC2_KEY="$HOME/.ssh/deleveribitly-key.pem"

# The directory the running app reads from (data_paths.py -> PROJECT_ROOT/data)
TARGET_DIR="/home/ec2-user/pani/blueshift_observatory/data"
SRC_DIR="/Users/pankaj/pani/data"

STAMP="$(date +%Y%m%d-%H%M%S)"
SSH="ssh -i $EC2_KEY -o ConnectTimeout=20 -o BatchMode=yes -o StrictHostKeyChecking=no"
SCP="scp -i $EC2_KEY -o ConnectTimeout=20 -o BatchMode=yes -o StrictHostKeyChecking=no"

# sync_merge <db_file> <count_table> <merge_sql>
#   Copies the local DB to EC2 /tmp, backs up the target, merges only missing
#   rows into TARGET_DIR/<db_file>, prints the row delta, and prunes old backups
#   (keeps the 7 most recent per DB).
sync_merge() {
  local DB_FILE="$1" TABLE="$2" MERGE_SQL="$3"
  local SRC="$SRC_DIR/$DB_FILE"

  if [ ! -f "$SRC" ]; then
    echo "SKIP $DB_FILE (no local source at $SRC)"
    return
  fi

  echo ">>> $DB_FILE"
  $SCP "$SRC" "$EC2_USER@$EC2_HOST:/tmp/sync_$DB_FILE"

  $SSH "$EC2_USER@$EC2_HOST" "
    set -e
    DB='$TARGET_DIR/$DB_FILE'
    TMP='/tmp/sync_$DB_FILE'

    # Refuse to proceed if the transferred file is corrupt
    if [ \"\$(sqlite3 \"\$TMP\" 'PRAGMA integrity_check;' | head -1)\" != 'ok' ]; then
      echo '    ABORT: transferred file failed integrity check'; rm -f \"\$TMP\"; exit 1
    fi

    # Back up the live DB before touching it, then merge
    cp -p \"\$DB\" \"\$DB.bak-$STAMP\"
    before=\$(sqlite3 \"\$DB\" 'SELECT COUNT(*) FROM $TABLE;')
    sqlite3 \"\$DB\" \"ATTACH '\$TMP' AS src; $MERGE_SQL DETACH src;\"
    after=\$(sqlite3 \"\$DB\" 'SELECT COUNT(*) FROM $TABLE;')

    echo \"    $TABLE: \$before -> \$after rows (+\$((after - before)))  backup: \$(basename \"\$DB\").bak-$STAMP\"
    rm -f \"\$TMP\"

    # Keep only the 7 most recent backups for this DB
    ls -1t \"\$DB\".bak-* 2>/dev/null | tail -n +8 | xargs -r rm -f
  "
}

echo "Syncing $SRC_DIR  ->  $EC2_USER@$EC2_HOST:$TARGET_DIR"
echo

# --- Pulsation / deliverability history ---
sync_merge "deliverability_history.db" "daily_metrics" \
"INSERT OR IGNORE INTO daily_metrics
  (report_date, from_domain, region, esp, sent, delivered, bounces,
   soft_bounce_count, unique_soft_bounce, spam_report, unsubscribe,
   delivery_rate, spam_rate, unsub_rate, bounce_rate, soft_bounce_pct,
   risk_score, classification, created_at)
 SELECT
   report_date, from_domain, region, esp, sent, delivered, bounces,
   soft_bounce_count, unique_soft_bounce, spam_report, unsubscribe,
   delivery_rate, spam_rate, unsub_rate, bounce_rate, soft_bounce_pct,
   risk_score, classification, created_at
 FROM src.daily_metrics;"

# --- Saved MBR reports (no unique constraint -> dedupe on natural key) ---
sync_merge "mbr_reports.db" "mbr_reports" \
"INSERT INTO mbr_reports
  (report_type, from_date, to_date, duration_days, total_domains,
   total_accounts, report_data, created_at, month, year)
 SELECT
   s.report_type, s.from_date, s.to_date, s.duration_days, s.total_domains,
   s.total_accounts, s.report_data, s.created_at, s.month, s.year
 FROM src.mbr_reports s
 WHERE NOT EXISTS (
   SELECT 1 FROM mbr_reports m
   WHERE m.report_type = s.report_type
     AND m.from_date   = s.from_date
     AND m.to_date     = s.to_date
     AND m.created_at  = s.created_at
 );"

echo
echo "Done."
