#!/usr/bin/env bash
# deploy.sh — publica doc-converter en el HP (desde el Mac). Idempotente.
#   deploy/hp/deploy.sh
# Resultado: https://hp-server.taila36576.ts.net:8300 (solo tailnet)
set -euo pipefail
cd "$(dirname "$0")/../.."
HOST=${HOST:-hp-server}
DIR=/srv/apps/doc-converter
PG=~/Developer/local/hp-server/hp/postgres/add-db.sh

ssh "$HOST" "sudo mkdir -p $DIR/app $DIR/data && sudo chown -R 1000:1000 $DIR"
rsync -az --delete --exclude-from=.dockerignore --exclude=.dockerignore ./ "$HOST:$DIR/app/"
rsync -az .dockerignore "$HOST:$DIR/app/"

# .env del servidor: se crea una vez (Gemini del .env local + base propia en postgres-apps).
if ! ssh "$HOST" "test -f $DIR/app/.env"; then
  gemini=$(grep -E '^GEMINI_API_KEY=' .env | cut -d= -f2-)
  ssh "$HOST" 'mkdir -p ~/pg-add && cat > ~/pg-add/add-db.sh && bash ~/pg-add/add-db.sh doc_converter' < "$PG"
  dburl=$(ssh "$HOST" 'grep ^DATABASE_URL= ~/pg-add/credentials/doc_converter.env | cut -d= -f2-' \
          | sed 's/@[^:/]*:5432/@127.0.0.1:5432/')
  ssh "$HOST" "umask 077; cat > $DIR/app/.env" <<ENV
GEMINI_API_KEY=$gemini
MAX_UPLOAD_MB=100
EXPIRY_DAYS=30
DATABASE_URL=$dburl
ENV
fi

ssh "$HOST" "cd $DIR/app && DOC_CONVERTER_DATA=$DIR/data sudo -E docker compose up -d --build --remove-orphans"
ssh "$HOST" "sudo tailscale serve --bg --https=8300 http://127.0.0.1:8300 >/dev/null"
ssh "$HOST" "sudo install -m 644 $DIR/app/deploy/hp/doc-converter-backup.service $DIR/app/deploy/hp/doc-converter-backup.timer /etc/systemd/system/ \
  && sudo install -m 755 $DIR/app/deploy/hp/backup.sh /usr/local/bin/doc-converter-backup \
  && sudo systemctl daemon-reload && sudo systemctl enable --now doc-converter-backup.timer >/dev/null"

for i in $(seq 1 30); do
  if ssh "$HOST" "curl -fsS http://127.0.0.1:8300/api/status"; then echo; echo "OK → https://hp-server.taila36576.ts.net:8300/tools"; exit 0; fi
  sleep 2
done
echo "El contenedor no respondió; revisa: ssh $HOST sudo docker logs doc-converter" >&2; exit 1
