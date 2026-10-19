#!/bin/sh
set -eu
base=/usr/local/etc/ott-k8a-recovery
case "${1:-}" in
  start)
    /usr/bin/install -o root -g root -m 0644 "$base/ott-k8a-recovery.service" /etc/systemd/system/ott-k8a-recovery.service
    /usr/bin/systemctl daemon-reload
    /usr/bin/systemctl enable ott-k8a-recovery.service
    /usr/bin/systemctl --no-block start ott-k8a-recovery.service
    ;;
  stop) /usr/bin/systemctl stop ott-k8a-recovery.service ;;
  *) echo "Usage: $0 start|stop" >&2; exit 2 ;;
esac
