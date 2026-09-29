#!/usr/bin/env python3
"""Preview or install three scoped DNS-SD records on an OpenWrt router."""
import argparse
import re
import shlex
import struct
import subprocess
import urllib.parse


def dns_name(value):
    value = value.rstrip('.').lower()
    labels = value.split('.')
    if len(value) > 253 or not all(re.fullmatch(r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', x) for x in labels):
        raise ValueError('Use an ASCII DNS name with labels of at most 63 characters')
    return b''.join(bytes([len(x)]) + x.encode('ascii') for x in labels) + b'\0'


def records(domain, address, instance='Home'):
    domain = domain.rstrip('.').lower()
    dns_name(domain)
    if domain == 'local' or domain.endswith('.local'):
        raise ValueError('.local is reserved for multicast DNS; use the DHCP search domain for unicast DNS')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,62}', instance):
        raise ValueError('Use a short ASCII instance label, for example Home')
    url = urllib.parse.urlsplit(address)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('The controller address must be HTTPS without credentials, query or fragment')
    dns_name(url.hostname)
    port = url.port or 443
    if not 1 <= port <= 65535:
        raise ValueError('Invalid controller port')
    path = url.path.rstrip('/') or '/'
    if not re.fullmatch(r'/[A-Za-z0-9._~/-]*', path) or any(x in ('.', '..') for x in path.split('/')):
        raise ValueError('Use a plain absolute URL path without escapes or dot segments')
    service = '_ottplay-ctrl._tcp.' + domain
    target = instance.lower() + '.' + service
    txt = ['txtvers=1', 'scheme=https', 'path=' + path]
    if any(len(value.encode()) > 255 for value in txt):
        raise ValueError('TXT values must fit in 255 bytes')
    return [
        ('ottplay_ctrl_ptr', service, 12, dns_name(target)),
        ('ottplay_ctrl_srv', target, 33, struct.pack('!HHH', 0, 0, port) + dns_name(url.hostname)),
        ('ottplay_ctrl_txt', target, 16, b''.join(bytes([len(x)]) + x.encode('ascii') for x in txt)),
    ]


def install_script(rows):
    commands = []
    test_lines = []
    for section, name, kind, data in rows:
        for key, value in [('', 'dnsrr'), ('.rrname', name), ('.rrnumber', str(kind)), ('.hexdata', data.hex())]:
            commands.append('set dhcp.' + section + key + '=' + shlex.quote(value))
        test_lines.append('dns-rr=' + name + ',' + str(kind) + ',' + data.hex())
    return '''#!/bin/sh
# Stage changes away from live UCI; replace only after syntax and drift checks.
set -eu
lock=/var/lock/ottplay-control-dns.lock
mkdir "$lock" || { echo 'Another OTT-play DNS update is running' >&2; exit 1; }
stage=$(mktemp -d /tmp/ottplay-dns.XXXXXX)
trap 'rm -rf "$stage"; rmdir "$lock"' EXIT INT TERM
[ -z "$(uci changes dhcp)" ] || { echo 'Commit or revert pending DHCP edits first' >&2; exit 1; }
cp /etc/config/dhcp "$stage/original"
cp /etc/config/dhcp "$stage/dhcp"
mkdir "$stage/delta" "$stage/overrides"
uci -c "$stage" -C "$stage/overrides" -t "$stage/delta" batch <<'OTT_UCI'
''' + '\n'.join(commands) + '''
commit dhcp
OTT_UCI
cat > "$stage/records.conf" <<'OTT_DNS'
''' + '\n'.join(test_lines) + '''
OTT_DNS
dnsmasq --test --conf-file="$stage/records.conf"
cmp -s /etc/config/dhcp "$stage/original" || { echo 'DHCP configuration changed concurrently' >&2; exit 1; }
[ -z "$(uci changes dhcp)" ] || { echo 'New pending DHCP edits appeared' >&2; exit 1; }
if cmp -s /etc/config/dhcp "$stage/dhcp"; then
    echo 'OTT-play DNS records are already installed'
    exit 0
fi
backup=/root/ottplay-dns-backups/$(date +%Y%m%d-%H%M%S)-dhcp
mkdir -p /root/ottplay-dns-backups
chmod 700 /root/ottplay-dns-backups
cp /etc/config/dhcp "$backup"
chmod 600 "$backup"
cp "$stage/dhcp" /etc/config/.dhcp-ottplay-new
chmod 600 /etc/config/.dhcp-ottplay-new
mv /etc/config/.dhcp-ottplay-new /etc/config/dhcp
if ! /etc/init.d/dnsmasq restart; then
    cp "$backup" /etc/config/dhcp
    /etc/init.d/dnsmasq restart
    echo 'DNS restart failed; restored the previous DHCP configuration' >&2
    exit 1
fi
echo "Installed three OTT-play DNS records. Backup: $backup"
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--router', required=True, help='SSH host or alias')
    parser.add_argument('--domain', required=True, help='DHCP search domain, for example home.arpa')
    parser.add_argument('--address', required=True, help='HTTPS controller base URL')
    parser.add_argument('--instance', default='Home')
    parser.add_argument('--apply', action='store_true', help='Install records; otherwise print a reviewable script')
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@._:-]*', args.router):
            raise ValueError('Invalid SSH host')
        script = install_script(records(args.domain, args.address, args.instance))
        if args.apply:
            subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', args.router, 'sh', '-s'],
                           input=script, text=True, check=True, timeout=60)
        else:
            print(script, end='')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
