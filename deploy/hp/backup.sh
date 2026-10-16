#!/usr/bin/env bash
# Respaldo diario de doc-converter: purga caducados y copia los datos al repo restic del HP.
set -euo pipefail
curl -fsS -o /dev/null http://127.0.0.1:8300/api/files || true   # dispara purge_expired
restic -r /srv/storage/toshiba/Servidor_Backups_HP/restic \
  --password-file /srv/apps/backups/restic.key \
  backup /srv/apps/doc-converter/data /srv/apps/doc-converter/app/.env --tag doc-converter --quiet
restic -r /srv/storage/toshiba/Servidor_Backups_HP/restic \
  --password-file /srv/apps/backups/restic.key \
  forget --tag doc-converter --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --quiet
