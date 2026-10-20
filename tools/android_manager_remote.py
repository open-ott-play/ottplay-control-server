#!/usr/bin/env python3
"""Run verified USB maintenance through the one NAS manager (SSH only)."""
import argparse
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='synology')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command or ['status']
    if command[0] not in ('status', 'exec'):
        parser.error('Use status or exec ALIAS ADB_ARGUMENTS')
    remote = shlex.join(['sudo', '-n', '/usr/local/bin/android-managerctl', *command])
    raise SystemExit(subprocess.call(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.host, remote]))


if __name__ == '__main__':
    main()
