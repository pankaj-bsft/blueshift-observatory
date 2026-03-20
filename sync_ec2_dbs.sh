#!/bin/bash
set -euo pipefail

EC2_HOST="172.31.249.157"
EC2_USER="ec2-user"
EC2_KEY="$HOME/.ssh/deleveribitly-key.pem"
TARGET_DIR="/home/ec2-user/pani/blueshift_observatory/database"

# Only mirror Pulsation/Spamhaus and MBR Deliverability DBs
SRC_DIR="/Users/pankaj/pani/data"
DBS=("deliverability_history.db" "mbr_reports.db")

# Create temp backups to avoid partial writes during sync
TMP_DIR="/tmp/blueshift_db_sync"
mkdir -p "$TMP_DIR"

for db in "${DBS[@]}"; do
  if [ -f "$SRC_DIR/$db" ]; then
    cp -f "$SRC_DIR/$db" "$TMP_DIR/$db"
  fi
done

rsync -az -e "ssh -i $EC2_KEY -o StrictHostKeyChecking=no" \
  "$TMP_DIR/" ec2-user@${EC2_HOST}:"${TARGET_DIR}/"

rm -rf "$TMP_DIR"
