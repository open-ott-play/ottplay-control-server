#!/bin/sh
set -eu
base=/usr/local/etc/android-manager
case "${1:-}" in
  start)
    /usr/bin/install -o root -g root -m 0644 "$base/android-manager.service" /etc/systemd/system/android-manager.service
    /usr/bin/systemctl daemon-reload
    /usr/bin/systemctl enable android-manager.service
    /usr/bin/systemctl --no-block start android-manager.service
    ;;
  stop) /usr/bin/systemctl stop android-manager.service ;;
  *) echo "Usage: $0 start|stop" >&2; exit 2 ;;
esac
