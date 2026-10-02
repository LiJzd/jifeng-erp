#!/bin/sh
set -eu
task_base=/opt/jifeng-erp-test
task_compose=$task_base/releases/8858743-r2/deploy/compose.yaml
docker compose --project-directory "$task_base" --env-file "$task_base/.env" -f "$task_compose" --profile tools \
  run --rm --no-deps certbot renew --cert-name 116.62.241.148 --non-interactive --quiet
docker compose --project-directory "$task_base" --env-file "$task_base/.env" -f "$task_compose" \
  exec -T frontend nginx -c /tmp/jifeng-nginx.conf -s reload
