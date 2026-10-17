#!/bin/sh
# بکاپ روزانهٔ دیتابیس (کران: 0 4 * * * /opt/classino-bot/scripts/backup.sh)
set -e
cd "$(dirname "$0")/.."
mkdir -p backups
sqlite3 data/bot.db ".backup 'backups/bot-$(date +%F).db'"
find backups -name 'bot-*.db' -mtime +14 -delete
