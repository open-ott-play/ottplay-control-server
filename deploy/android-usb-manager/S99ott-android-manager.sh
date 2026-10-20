#!/bin/sh
set -eu
base=/usr/local/etc/ott-android-manager
unit=ott-android-manager.service
case "${1:-}" in
  start)
    if /usr/bin/systemctl is-active --quiet ott-k8a-recovery.service ||
       /usr/bin/systemctl is-enabled --quiet ott-k8a-recovery.service; then
      echo "Retire the legacy k8a recovery service before starting the shared manager." >&2
      exit 1
    fi
    for file in config.json android_usb_manager.py android_usb_supervisor.py "$unit"; do
      if [ ! -r "$base/$file" ]; then
        echo "Missing Android manager deployment file: $base/$file" >&2
        exit 1
      fi
    done
    /usr/bin/install -o root -g root -m 0644 "$base/$unit" "/etc/systemd/system/$unit"
    /usr/bin/systemctl daemon-reload
    /usr/bin/systemctl enable "$unit"
    /usr/bin/systemctl --no-block start "$unit"
    ;;
  stop)
    /usr/bin/systemctl stop "$unit"
    ;;
  *)
    echo "Usage: $0 start|stop" >&2
    exit 2
    ;;
esac
