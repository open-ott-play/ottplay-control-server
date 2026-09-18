#!/usr/bin/env bash
set -euo pipefail
image="${1:-ottplay-control-server:test}"
config_dir=$(mktemp -d)
container="ottplay-control-smoke-${RANDOM}"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$config_dir"
}
trap cleanup EXIT
# A generated private file is shared only with the unprivileged container group.
docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$config_dir,dst=/config" "$image" init --config /config/config.json --device-id smoke
sudo chgrp 65532 "$config_dir/config.json"
chmod 640 "$config_dir/config.json"
docker run -d --name "$container" --read-only --cap-drop ALL --security-opt no-new-privileges --memory 64m --cpus 0.5 --mount "type=bind,src=$config_dir/config.json,dst=/etc/ottplay-control-server/config.json,readonly" "$image" >/dev/null
for attempt in $(seq 1 30); do
  if docker exec "$container" /ottplay-control-server healthcheck; then
    test "$(docker inspect --format '{{.Config.User}}' "$container")" = '65532:65532'
    exit 0
  fi
  sleep 1
done
exit 1
