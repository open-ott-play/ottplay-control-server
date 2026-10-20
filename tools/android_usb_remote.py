#!/usr/bin/env python3
"""Quote a restricted USB controller command for an existing SSH host."""
import argparse
import re
import shlex
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--host', required=True)
p.add_argument('--remote-config', default='/usr/local/etc/ott-k8a-recovery/config.json')
p.add_argument('--remote-script', default='/usr/local/etc/ott-k8a-recovery/android_usb_supervisor.py')
p.add_argument('command', choices=['status', 'wake', 'swipe', 'screenshot', 'recover', 'reboot'])
p.add_argument('arguments', nargs='*')
a = p.parse_args()
if a.host.startswith('-') or not a.host or any(c.isspace() for c in a.host):
    p.error('Use one existing SSH host alias')
if a.command == 'swipe':
    if len(a.arguments) != 4 or not all(v.isdecimal() for v in a.arguments):
        p.error('swipe requires four non-negative coordinates')
elif a.command == 'screenshot':
    if len(a.arguments) != 1 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.png', a.arguments[0]):
        p.error('screenshot requires a bare PNG filename using letters, digits, _ or -')
elif a.arguments:
    p.error('This command takes no extra arguments')
command = ['sudo', '-n', '/usr/bin/python3', a.remote_script, '--config', a.remote_config, a.command, *a.arguments]
raise SystemExit(subprocess.run([
    'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
    '--', a.host, shlex.join(command),
]).returncode)
