#!/usr/bin/env bash
set -euo pipefail
release="$1"
case "$release" in /opt/adbuild-agent/releases/*) ;; *) echo 'Invalid release path' >&2; exit 1;; esac
test -f "$release/frontend/package-lock.json"
sudo install -d -o admin -g admin -m 755 /opt/adbuild-agent
sudo install -d -o root -g admin -m 750 /etc/adbuild-agent
if [ ! -x /opt/adbuild-agent/venv/bin/python ]; then
  python3 -m venv /opt/adbuild-agent/venv
fi
/opt/adbuild-agent/venv/bin/pip install --disable-pip-version-check -r "$release/backend/requirements.lock"
cd "$release/frontend"
npm ci --ignore-scripts --no-audit --no-fund
NEXT_TELEMETRY_DISABLED=1 npm run build
mkdir -p .next/standalone/.next
cp -a .next/static .next/standalone/.next/static
if [ ! -f /etc/adbuild-agent/runtime.env ]; then
  /opt/adbuild-agent/venv/bin/python - <<'PY' | sudo tee /etc/adbuild-agent/runtime.env >/dev/null
from cryptography.fernet import Fernet
print('DATABASE_URL=')
print('SUPABASE_URL=')
print('SUPABASE_ANON_KEY=')
print('SUPABASE_SERVICE_KEY=')
print('TOKEN_ENCRYPTION_KEY='+Fernet.generate_key().decode())
print('LLM_API_KEY=')
print('LLM_MODEL=deepseek-v4.1-flash-1017')
print('LLM_BASE_URL=https://api.deepseek.com')
print('APP_ORIGIN=https://stratos.run/apps/adbuild-agent')
print('API_ORIGIN=https://stratos.run/apps/adbuild-agent/api')
print('ROOT_PATH=/apps/adbuild-agent/api')
print('TIKTOK_MCP_URL=https://business-api.tiktok.com/open_mcp/tt-ads-mcp-flat')
PY
  sudo chmod 640 /etc/adbuild-agent/runtime.env
fi
for service in web api worker; do
  sudo install -o root -g root -m 644 "$release/deploy/adbuild-agent-$service.service" /etc/systemd/system/
done
ln -s "$release" /opt/adbuild-agent/current.next
mv -Tf /opt/adbuild-agent/current.next /opt/adbuild-agent/current
sudo systemctl daemon-reload
sudo systemctl enable --now adbuild-agent-web adbuild-agent-api
sudo systemctl restart adbuild-agent-web adbuild-agent-api
for attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18791/health >/dev/null && curl --fail --silent http://127.0.0.1:18790/apps/adbuild-agent >/dev/null; then break; fi
  sleep 2
done
curl --fail --silent http://127.0.0.1:18791/health
curl --fail --silent -o /dev/null http://127.0.0.1:18790/apps/adbuild-agent
sudo install -o root -g root -m 644 "$release/deploy/nginx.locations.conf" /etc/adbuild-agent/nginx.locations.conf
sudo python3 - <<'PY'
from pathlib import Path
import shutil
path=Path('/etc/nginx/sites-available/stratos.run')
text=path.read_text()
line='include /etc/adbuild-agent/nginx.locations.conf;'
if line not in text:
    anchor='proxy_cache off;'
    if text.count(anchor)!=1:
        raise SystemExit('Expected unique TLS routing anchor was not found')
    backup=Path('/etc/adbuild-agent/stratos.run.before-adbuild')
    if not backup.exists(): shutil.copy2(path,backup)
    path.write_text(text.replace(anchor,anchor+'\n\n# Adbuild application namespace\n'+line,1))
PY
if ! sudo nginx -t; then
  sudo cp -a /etc/adbuild-agent/stratos.run.before-adbuild /etc/nginx/sites-available/stratos.run
  echo 'Nginx validation failed; original configuration restored' >&2
  exit 1
fi
sudo systemctl reload nginx
sudo systemctl is-active adbuild-agent-web adbuild-agent-api nginx
echo 'Deployment ready: https://stratos.run/apps/adbuild-agent'
