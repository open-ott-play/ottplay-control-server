#!/usr/bin/env python3
"""One-time, exact-device root-ADB bootstrap; no network ADB is enabled."""
import argparse
import json
from pathlib import Path
import re
import subprocess

BASE = '/data/local/ott-remote'
BOOT_PATH = '/system/etc/install-recovery.sh'
TOUCH_BOOT = ('#!/system/bin/sh\n# OTT touch guard: fixed-purpose local broker\n'
              'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 1; done\n'
              'while :; do\n    /system/bin/ott-touch-service\n    sleep 2\ndone\n')
BOOT = ('#!/system/bin/sh\n# OTT native agent and existing touch guard\n'
        'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 1; done\n'
        '(\n while :; do\n  /data/local/ott-remote/agent\n'
        '  if [ -f /data/local/ott-remote/update.pending ] && [ -f /data/local/ott-remote/agent.previous ]; then\n'
        '   mv /data/local/ott-remote/agent.previous /data/local/ott-remote/agent\n'
        '   rm -f /data/local/ott-remote/update.pending\n  fi\n  sleep 5\n done\n) &\n'
        'while :; do\n /system/bin/ott-touch-service\n sleep 2\ndone\n')


def install(shell, push, config_path, binary_path, boot_path):
    config = json.loads(Path(config_path).read_text())
    if config.get('model') != 'Q1001L4B2' or not re.fullmatch(r'[a-zA-Z0-9]{8,64}', config.get('serial', '')):
        raise ValueError('Unsupported configured device')
    if config.get('device') == config.get('parent_device'):
        raise ValueError('Native agent must use a separate device credential')
    actual = shell('getprop ro.product.model; getprop ro.serialno; getprop ro.build.version.sdk').split()
    if actual != [config['model'], config['serial'], '19'] or not shell('id').startswith('uid=0('):
        raise RuntimeError('Exact configured Android API 19 device with root ADB required')
    previous = shell('cat ' + BOOT_PATH)
    if previous not in (TOUCH_BOOT, BOOT):
        raise RuntimeError('Startup script is not the known OTT supervisor; left unchanged')

    def checked(command):
        if 'OTT_NATIVE_OK' not in shell('(' + command + ') && echo OTT_NATIVE_OK').splitlines():
            raise RuntimeError('Android installation step failed; no private output shown')

    checked('mkdir -p ' + BASE + ' && chown 0:0 ' + BASE + ' && chmod 700 ' + BASE)
    for source, name in [(binary_path, 'agent.new'), (config_path, 'config.json.new'), (boot_path, 'boot.new')]:
        push(str(source), BASE + '/' + name)
    checked('chmod 700 ' + BASE + '/agent.new && chmod 600 ' + BASE + '/config.json.new ' + BASE + '/boot.new')
    # Fail before replacing startup if the kernel cannot execute the new binary.
    checked(BASE + '/agent.new --version > /dev/null')
    checked(BASE + '/agent.new --check-config ' + BASE + '/config.json.new')
    checked('stop flash_recovery')
    try:
        checked('cp ' + BOOT_PATH + ' ' + BASE + '/boot.previous')
        checked('mv ' + BASE + '/config.json.new ' + BASE + '/config.json && mv ' + BASE + '/agent.new ' + BASE + '/agent')
        checked('mount -o remount,rw /system')
        checked('cp ' + BASE + '/boot.new ' + BOOT_PATH + '.new && chmod 750 ' + BOOT_PATH + '.new && chown 0:0 '
                + BOOT_PATH + '.new && mv ' + BOOT_PATH + '.new ' + BOOT_PATH)
    finally:
        checked('mount -o remount,ro /system')
        checked('start flash_recovery')
    checked('rm -f ' + BASE + '/boot.new')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True, help='ADB USB serial (hardware identity is checked separately)')
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--binary', required=True, type=Path)
    args = parser.parse_args()
    adb = ['adb', '-s', args.serial]
    def shell(command):
        return subprocess.check_output(adb + ['shell', command], text=True).replace('\r\n', '\n')
    def push(source, target):
        subprocess.run(adb + ['push', source, target], check=True, stdout=subprocess.DEVNULL)
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        boot = Path(temporary) / 'boot'; boot.write_text(BOOT)
        install(shell, push, args.config, args.binary, boot)
    print('Agent installed. Verify with ott NAME android status before disconnecting USB.')

if __name__ == '__main__':
    main()
