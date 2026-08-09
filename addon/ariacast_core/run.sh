#!/usr/bin/with-contenv sh
set -e

CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
  DB_PATH=$(grep -o '"db_path" *: *"[^"]*"' "$CONFIG_PATH" | sed -E 's/.*: *"(.*)"/\1/')
  LOG_LEVEL=$(grep -o '"log_level" *: *"[^"]*"' "$CONFIG_PATH" | sed -E 's/.*: *"(.*)"/\1/')
  [ -n "$DB_PATH" ] && export ARIACAST_DB_PATH="$DB_PATH"
  [ -n "$LOG_LEVEL" ] && export ARIACAST_LOG_LEVEL="$LOG_LEVEL"
fi

echo "Starting AriaCast Core add-on (db=${ARIACAST_DB_PATH:-/data/ariacast.db})"
exec python3 -u /app/main.py
