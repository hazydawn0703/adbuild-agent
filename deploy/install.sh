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
mkdir -p .next/standalone/.next .next/standalone/public
cp -a .next/static .next/standalone/.next/static
cp -a public/. .next/standalone/public/
if [ ! -f /etc/adbuild-agent/runtime.env ]; then
  echo 'Configure MySQL DATABASE_URL, SESSION_SECRET, TOKEN_ENCRYPTION_KEY and DeepSeek settings in /etc/adbuild-agent/runtime.env before deployment' >&2
  exit 1
fi
/opt/adbuild-agent/venv/bin/python "$release/deploy/check-release.py"
# Routing is provisioned on the shared host. An application release must not
# replace the include or reload the host's configuration when routes are stable.
sudo cmp -s "$release/deploy/nginx.locations.conf" /etc/adbuild-agent/nginx.locations.conf
sudo nginx -t

for service in web api worker; do
  sudo install -o root -g root -m 644 "$release/deploy/adbuild-agent-$service.service" /etc/systemd/system/
done
ln -s "$release" /opt/adbuild-agent/current.next
mv -Tf /opt/adbuild-agent/current.next /opt/adbuild-agent/current
sudo systemctl daemon-reload
sudo systemctl enable --now adbuild-agent-web adbuild-agent-api adbuild-agent-worker
sudo systemctl restart adbuild-agent-web adbuild-agent-api adbuild-agent-worker
for attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18791/health >/dev/null && curl --fail --silent http://127.0.0.1:18790/apps/adbuild-agent >/dev/null; then break; fi
  sleep 2
done
curl --fail --silent http://127.0.0.1:18791/health
curl --fail --silent -o /dev/null http://127.0.0.1:18790/apps/adbuild-agent
sudo systemctl is-active adbuild-agent-web adbuild-agent-api adbuild-agent-worker nginx
echo 'Deployment ready: https://stratos.run/apps/adbuild-agent'
